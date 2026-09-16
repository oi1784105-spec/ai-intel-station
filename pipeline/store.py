"""持久化：原子写、幂等合并、滚动窗口与容量保留。

**幂等性是本模块存在的理由。** 定时任务会重复导入同一批 feed 内容，
「重复导入不应产生重复记录」这条验收要求完全由 `merge()` 保证，其不变量是：

1. `first_seen_at` 一经写入永不改变，重复导入只推进 `last_seen_at`；
2. 已存在条目不会被新副本替代，也不会产生第二条记录（归一 URL → 标题三级匹配）；
3. 字段更新都**只依赖入参本身**（取最大值 / 更长者胜 / 补空 / 按配置回写展示名），
   因此「同一输入 + 同一时刻」重复合并的结果与单次合并完全相同；
4. 输出顺序由不可变字段（`published_at`, `first_seen_at`, `id`）决定，与输入顺序无关。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import config, dedupe
from .models import Article, NewsStore


@dataclass
class MergeOutcome:
    """一次合并的结果与其统计，供运行报告使用。"""

    articles: list[Article] = field(default_factory=list)
    added: int = 0
    refreshed: int = 0
    #: 本轮真正写入存储的条目 id（**在 retention 之前**统计）。
    #: retention 会把窗口外条目剔除，因此不能靠「最终集合里是否有它」反推新增。
    added_ids: set[str] = field(default_factory=set)
    #: `(入参 URL, 判定理由)`，证明去重确实生效，而不是「碰巧没重复」。
    duplicates: list[tuple[str, str]] = field(default_factory=list)
    evicted_by_window: int = 0
    evicted_by_capacity: int = 0


class _TitleIndex:
    """标题倒排索引。

    把「每个新条目 vs 全部历史条目」的比较从 O(n·m) 降到只比较共享 3-gram 的候选对。
    查询时跳过 posting 过长的 gram（常用搭配无区分度）；精确标题命中走字典直查。
    """

    def __init__(self) -> None:
        self._exact: dict[str, Article] = {}
        self._postings: dict[str, list[Article]] = defaultdict(list)

    def add(self, article: Article) -> None:
        normalized = dedupe.normalize_title(article.title)
        if not normalized:
            return
        self._exact.setdefault(normalized, article)
        for gram in dedupe.blocking_grams(normalized, 3):
            self._postings[gram].append(article)

    def match(self, article: Article) -> tuple[Article, str] | None:
        """找出与 `article` 指向同一篇文章的已有条目。"""
        normalized = dedupe.normalize_title(article.title)
        if not normalized:
            return None

        exact = self._exact.get(normalized)
        if exact is not None:
            return exact, "标题归一后完全相同"

        candidates: list[Article] = []
        for gram in dedupe.blocking_grams(normalized, 3):
            bucket = self._postings.get(gram)
            if not bucket or len(bucket) > config.BLOCKING_MAX_POSTINGS:
                continue
            candidates.extend(bucket)

        visited: set[str] = set()
        for candidate in candidates:
            if candidate.id in visited or candidate.id == article.id:
                continue
            visited.add(candidate.id)
            is_dup, reason = dedupe.is_duplicate(candidate.title, article.title)
            if is_dup:
                return candidate, reason
        return None


def _refresh(target: Article, candidate: Article, now: datetime) -> None:
    """用本轮数据更新已有条目的易变字段。

    不改动 `id` / `url` / `title` / `first_seen_at` —— 这四项是条目的身份。
    其余更新均为单调操作，保证重复合并同一输入不改变结果。
    """
    target.last_seen_at = now

    if target.published_at is None and candidate.published_at is not None:
        # 首次导入时解析失败、后续源站修好了时间，才补写；已有时间永不覆盖。
        target.published_at = candidate.published_at
        target.published_raw = candidate.published_raw
        target.published_note = candidate.published_note

    if candidate.summary and (
        not target.summary or len(candidate.summary) > len(target.summary)
    ):
        target.summary = candidate.summary
        target.summary_source = candidate.summary_source

    # HN 分数随时间上涨，取最大值才能在重复运行时反映最新热度，同时保持幂等。
    if candidate.heat is not None and (target.heat is None or candidate.heat > target.heat):
        target.heat = candidate.heat
    if candidate.comments is not None and (
        target.comments is None or candidate.comments > target.comments
    ):
        target.comments = candidate.comments

    # 配图**只补空，绝不覆盖**：源站有时整批响应不带图（例如换了 CDN 或未渲染正文 HTML），
    # 若允许空值覆盖，一次这样的响应就会把已抓到的配图永久抹掉。补空同时保持幂等。
    if target.image_url is None and candidate.image_url is not None:
        target.image_url = candidate.image_url

    # 展示名则相反，**始终按当前配置回写**：它完全由 `config` 决定、与响应内容无关，
    # 因此回写同样是输入到输出的确定映射（幂等不受影响），却能让「源清单改名」在下一次
    # 运行时自动传播到已入库条目，不必为了改个显示名去跑 `--rebuild`。
    if candidate.source_name_en:
        target.source_name_en = candidate.source_name_en


def _apply_retention(
    records: list[Article],
    now: datetime,
    window_days: int,
    capacity: int,
) -> tuple[list[Article], int, int]:
    """滚动窗口 + 容量上限。返回 `(保留列表, 窗口淘汰数, 容量淘汰数)`。"""
    cutoff = now - timedelta(days=window_days)
    within = [record for record in records if (record.published_at or record.first_seen_at) >= cutoff]
    evicted_by_window = len(records) - len(within)

    within.sort(
        key=lambda article: (
            article.published_at or article.first_seen_at,
            article.first_seen_at,
            article.id,
        ),
        reverse=True,
    )

    evicted_by_capacity = 0
    if len(within) > capacity:
        evicted_by_capacity = len(within) - capacity
        within = within[:capacity]
    return within, evicted_by_window, evicted_by_capacity


def split_by_window(
    articles: list[Article], now: datetime, window_days: int
) -> tuple[list[Article], list[Article]]:
    """按滚动窗口把入参分成 `(窗口内, 窗口外)`。

    必须在合并**之前**执行。窗口外的条目即便合并进去也会立刻被 retention 淘汰，
    结果是每一轮运行都把它们当成「新增」再淘汰一次：报告里的新增数虚高，
    「重复导入不产生新条目」这条性质在报告层面也不成立。

    时间口径与 retention 一致：有发布时间用发布时间，否则用首次见到的时间。
    """
    cutoff = now - timedelta(days=window_days)
    kept: list[Article] = []
    dropped: list[Article] = []
    for article in articles:
        target = kept if (article.published_at or article.first_seen_at) >= cutoff else dropped
        target.append(article)
    return kept, dropped


def merge(
    existing: list[Article],
    incoming: list[Article],
    now: datetime,
    *,
    window_days: int | None = None,
    max_items: int | None = None,
) -> MergeOutcome:
    """把本轮的归一化条目合并进已有集合。"""
    days = config.WINDOW_DAYS if window_days is None else window_days
    capacity = config.MAX_STORE_ITEMS if max_items is None else max_items

    records = list(existing)
    by_id: dict[str, Article] = {record.id: record for record in records}
    index = _TitleIndex()
    for record in records:
        index.add(record)

    outcome = MergeOutcome()
    # 先给入参一个确定的顺序：两条指向同一篇文章的候选谁先被处理，决定了哪一条成为
    # 保留记录。若依赖调用方的入参顺序，跨源重复条目的幸存者就会随运行而不定，
    # 从而破坏「同一输入重复合并结果相同」。排序键：来源权重 → 发布时间 → id。
    ordered = sorted(
        incoming,
        key=lambda article: (
            -article.source_weight,
            -(article.published_at or article.first_seen_at).timestamp(),
            article.id,
        ),
    )

    for candidate in ordered:
        target = by_id.get(candidate.id)
        reason = "归一 URL 相同"

        if target is None:
            hit = index.match(candidate)
            if hit is not None:
                target, reason = hit

        if target is None:
            records.append(candidate)
            by_id[candidate.id] = candidate
            index.add(candidate)
            outcome.added += 1
            outcome.added_ids.add(candidate.id)
        else:
            # 不同 id 命中同一篇文章时，把候选 id 也指向该记录，
            # 使后续相同 URL 的候选走 O(1) 直查。
            by_id[candidate.id] = target
            _refresh(target, candidate, now)
            outcome.refreshed += 1
            outcome.duplicates.append((candidate.url, reason))

    kept, evicted_window, evicted_capacity = _apply_retention(records, now, days, capacity)
    outcome.articles = kept
    outcome.evicted_by_window = evicted_window
    outcome.evicted_by_capacity = evicted_capacity
    return outcome


def sync_source_display_names(
    articles: list[Article], sources: Iterable[config.SourceConfig] | None = None
) -> int:
    """把在库条目的源展示名重派生为当前配置的英文名，返回被更正的条数。

    展示名完全由 `source_id` + 源清单决定，**与「本轮该源有没有回这条」无关**。
    若只在合并候选上更新它，改名就会半生效：源站本轮没回那条（已滚出 feed 首页，
    或该源整体抓取失败）的条目会一直留着旧值 —— 实测 230 条里有 19 条属于这种情况，
    页面上于是露出中文源名，与「源名全站统一英文」直接冲突。

    因此这里对**全部在库条目**重派生一次。它不依赖网络，开销是一次字典查找/条。
    """
    lookup = {source.id: source.display_name for source in (sources or config.SOURCES)}
    fixed = 0
    for article in articles:
        name = lookup.get(article.source_id)
        if name and article.source_name_en != name:
            article.source_name_en = name
            fixed += 1
    return fixed


def write_json_atomic(path: str | Path, payload: str) -> None:
    """原子写 JSON：先写同目录临时文件再 `os.replace`。

    必要性：定时任务写 `data/news.json` 时若被中断，非原子写会留下截断的 JSON，
    下一次运行将无法加载历史，导致去重锚点丢失并整站重灌。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    text = payload if payload.endswith("\n") else payload + "\n"
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, target)


def dump_store(store: NewsStore) -> str:
    """序列化 `NewsStore` 为稳定格式的 JSON（缩进 2，UTF-8 原样保留中文）。"""
    return json.dumps(store.model_dump(mode="json"), ensure_ascii=False, indent=2)


def load_store(path: str | Path) -> NewsStore | None:
    """读取 `data/news.json`。文件不存在或无法解析时返回 `None`（视为首次运行）。"""
    target = Path(path)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 无法解析的历史文件不阻断运行：重建比崩溃好，但要在报告里提示。
        return None
    return NewsStore.model_validate(payload)


def load_articles(path: str | Path) -> list[Article]:
    """只取历史条目列表。"""
    store = load_store(path)
    return list(store.articles) if store else []