"""事件聚合：把报道同一事件的多来源条目归入一个簇。

与去重严格区分 —— **去重是删除，聚合是建立关系**。同一事件被 5 家媒体报道，
5 条都必须保留（这是「多个来源」的广告位），只是互相标记为同簇，用于计算跨源命中度。

为避免 O(n²) 全量比较，先在时间窗内用倒排索引生成候选对（4-gram 分桶 + 实体词分桶），
只对候选对求相似度。时间窗外或分桶不重合的条目直接不相比较。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import config, dedupe
from .models import Article


@dataclass
class Cluster:
    """一个事件簇。`id` 取簇内字典序最小的条目 id，保证跨运行稳定。"""

    id: str
    article_ids: list[str] = field(default_factory=list)
    source_ids: set[str] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.article_ids)

    @property
    def independent_sources(self) -> int:
        return len(self.source_ids)


class _UnionFind:
    """带理由记录的并查集：合并时保留触发合并的那条理由，便于在 UI 上解释。"""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._reasons: dict[int, str] = {}

    def find(self, node: int) -> int:
        root = node
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[node] != root:
            self._parent[node], node = root, self._parent[node]
        return root

    def union(self, left: int, right: int, reason: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        self._parent[right_root] = left_root
        merged = [self._reasons.get(left_root), self._reasons.get(right_root), reason]
        self._reasons[left_root] = next(r for r in merged if r)

    def reason(self, node: int) -> str:
        return self._reasons.get(self.find(node), "")


def _blocking_grams(normalized: str, size: int = 4) -> set[str]:
    """分桶键：归一标题的 `size`-gram。短标题整体作为一个桶。"""
    return dedupe.blocking_grams(normalized, size)


def _effective_time(article: Article) -> datetime:
    """用于时间窗判定的时间。发布时间缺失时退回首次见到的时间，避免条目被漏出聚合。"""
    return article.published_at or article.first_seen_at


def build_clusters(articles: list[Article], now: datetime) -> dict[str, Cluster]:
    """在时间窗内聚合事件，返回 `{cluster_id: Cluster}`。

    只返回**跨源**（≥2 个独立来源）且成员数 > 1 的簇 —— 因此 `article.cluster_id`
    非空就等价于「这条被多个来源报道过」，前端与统计口径都无需再判断来源数。
    """
    horizon = now - timedelta(hours=config.CLUSTER_WINDOW_HOURS)
    candidates = [a for a in articles if _effective_time(a) >= horizon]
    if len(candidates) < 2:
        return {}

    postings: dict[str, list[int]] = defaultdict(list)
    entity_df: Counter[str] = Counter()
    raw_entities: list[frozenset[str]] = []
    for article in candidates:
        found = dedupe.entities(article.title)
        raw_entities.append(found)
        for entity in found:
            entity_df[entity.lower()] += 1

    # 稀有度过滤：在本次比较窗口内过于常见的实体不参与聚合。
    # 这是对 `dedupe._ENTITY_STOP` 静态黑名单的动态补充 —— 黑名单不可能穷举
    # `Agent` / `Model` / `Adaptive` 这类会随领域词汇漂移的通用词，
    # 而它们的共同特征是「在这批数据里到处都是」，文档频率正好能抓住这一点。
    distinctive_entities = [
        frozenset(
            entity
            for entity in found
            if entity_df[entity.lower()] <= config.CLUSTER_DISTINCTIVE_ENTITY_MAX_DF
        )
        for found in raw_entities
    ]

    for index, article in enumerate(candidates):
        for gram in _blocking_grams(dedupe.normalize_title(article.title)):
            postings[gram].append(index)
    for index, entities in enumerate(distinctive_entities):
        for entity in entities:
            postings["\x00" + entity.lower()].append(index)

    union_find = _UnionFind(len(candidates))
    for indexes in postings.values():
        if not 2 <= len(indexes) <= config.BLOCKING_MAX_POSTINGS:
            # 单条 posting 无需比较；超长 posting 是常用搭配，比较收益低且开销大。
            continue
        for i in range(len(indexes)):
            for j in range(i + 1, len(indexes)):
                left, right = indexes[i], indexes[j]
                if union_find.find(left) == union_find.find(right):
                    continue
                matched, reason = dedupe.same_event(
                    candidates[left].title,
                    candidates[right].title,
                    distinctive_entities[left],
                    distinctive_entities[right],
                )
                if matched:
                    union_find.union(left, right, reason)

    grouped: dict[int, list[int]] = defaultdict(list)
    for index in range(len(candidates)):
        grouped[union_find.find(index)].append(index)

    clusters: dict[str, Cluster] = {}
    for root, members in grouped.items():
        if len(members) < 2:
            continue
        source_ids = {candidates[i].source_id for i in members}
        if len(source_ids) < 2:
            # 同一来源的连续报道（例如同站的系列推广帖）不构成「跨源事件」。
            # 只保留 ≥2 个独立来源的簇，使 `cluster_id` 非空 ⟺ 真正的多来源报道，
            # 前端与统计口径都因此无需再二次判断。
            continue
        members.sort(key=lambda i: candidates[i].id)
        article_ids = [candidates[i].id for i in members]
        clusters[article_ids[0]] = Cluster(
            id=article_ids[0],
            article_ids=article_ids,
            source_ids=source_ids,
            reasons=[union_find.reason(root)],
        )
    return clusters


def cluster_index(clusters: dict[str, Cluster]) -> dict[str, str]:
    """`{article_id: cluster_id}` 反查表，用于回填 `article.cluster_id`。"""
    return {
        article_id: cluster.id
        for cluster in clusters.values()
        for article_id in cluster.article_ids
    }