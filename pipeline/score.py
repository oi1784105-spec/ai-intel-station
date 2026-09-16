"""可解释打分与「今日重点」入选判定。

设计取舍：**入选是规则，排序才是分数**。
如果只用一条分数阈值做入选，最后无法向用户解释「为什么这条是重点」。
这里把入选条件写成 4 条可原文复述的规则，`score` 只负责把入选条目排出先后，
并把每个分项拆成 `reasons` 展示在卡片上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from . import config
from .cluster import Cluster
from .models import Article, Highlight


def _term_hit(haystack_lower: str, term: str) -> bool:
    """词命中判定。

    拉丁词用词边界匹配 —— 子串匹配会让 `AI` 命中 `said`/`email`/`chair`/`maintain`。
    中文与含空格的短语直接子串匹配。
    """
    lowered = term.lower()
    is_single_latin_word = (
        lowered.isascii() and " " not in lowered and lowered.replace("-", "").isalpha()
    )
    if is_single_latin_word:
        pattern = rf"(?<![a-z]){re.escape(lowered)}(?![a-z])"
        return re.search(pattern, haystack_lower) is not None
    return lowered in haystack_lower


def count_signals(title: str, summary: str | None) -> tuple[int, list[str]]:
    """统计命中的信号类别数与类别名，用于展示「涉及发布/上线」这类可读理由。"""
    haystack = f"{title} {summary or ''}".lower()
    names = [
        name
        for name, terms in config.SIGNAL_TERMS.items()
        if any(_term_hit(haystack, term) for term in terms)
    ]
    return len(names), names


def relative_time_zh(moment: datetime, now: datetime) -> str:
    """中文相对时间文案。仅用于展示，不参与任何筛选。"""
    seconds = (now - moment).total_seconds()
    if seconds < 0:
        return "刚刚"
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    days = int(seconds // 86400)
    if days < 30:
        return f"{days} 天前"
    return f"{days // 30} 个月前"


@dataclass
class ScoreBreakdown:
    """一条条目的打分明细。`score` 用于排序，`reasons` 直接渲染到卡片上。"""

    score: float
    reasons: list[str] = field(default_factory=list)
    signal_count: int = 0
    signal_names: list[str] = field(default_factory=list)
    independent_sources: int = 1
    cluster_size: int = 1


def evaluate(article: Article, cluster: Cluster | None, now: datetime) -> ScoreBreakdown:
    """计算一条条目的分数与可读理由。簇信息缺失时按孤立条目处理。"""
    weights = config.SCORE_WEIGHTS
    independent = cluster.independent_sources if cluster else 1
    cluster_size = cluster.size if cluster else 1

    # 跨源命中度：单个来源记 0，达到饱和值记满分。
    if independent >= 2:
        cross = min(
            1.0,
            (independent - 1) / max(1, config.CROSS_SOURCE_SATURATION - 1),
        )
    else:
        cross = 0.0

    moment = article.published_at or article.first_seen_at
    hours = max(0.0, (now - moment).total_seconds() / 3600)
    recency = max(0.0, 1.0 - hours / config.RECENCY_HORIZON_HOURS)

    signal_count, signal_names = count_signals(article.title, article.summary)
    heat_normalized = min(article.heat or 0, 1000) / 1000

    score = (
        weights["source"] * article.source_weight
        + weights["cross"] * cross
        + weights["recency"] * recency
        + weights["signal"] * min(signal_count, 2)
        + weights["heat"] * heat_normalized
    )

    reasons: list[str] = []
    if independent >= 2:
        reasons.append(f"{independent} 个独立来源报道")
    elif cluster_size >= 2:
        reasons.append(f"同一事件聚合 {cluster_size} 条")
    if article.source_official:
        reasons.append("官方一手来源")
    reasons.append(relative_time_zh(moment, now))
    if signal_names:
        reasons.append("涉及" + "、".join(signal_names))
    if article.heat:
        detail = f"HN {article.heat} 分"
        if article.comments:
            detail += f" · {article.comments} 评论"
        reasons.append(detail)

    return ScoreBreakdown(
        score=round(score, 3),
        reasons=reasons,
        signal_count=signal_count,
        signal_names=signal_names,
        independent_sources=independent,
        cluster_size=cluster_size,
    )


def _is_highlight(article: Article, breakdown: ScoreBreakdown) -> str | None:
    """「今日重点」入选规则。命中任一条件即返回入选理由，否则返回 `None`。"""
    if breakdown.independent_sources >= config.HIGHLIGHT_MIN_INDEPENDENT_SOURCES:
        return f"{breakdown.independent_sources} 个独立来源同时报道"
    if (article.heat or 0) >= config.HIGHLIGHT_MIN_HEAT:
        return f"社区热度 {article.heat} 分"
    if breakdown.signal_count >= config.HIGHLIGHT_MIN_SIGNALS:
        return "命中多重信号：" + "、".join(breakdown.signal_names)
    if article.source_official and breakdown.signal_count >= 1:
        return f"官方来源，涉及{breakdown.signal_names[0]}"
    return None


def _card_reasons(why: str, breakdown: ScoreBreakdown) -> list[str]:
    """把入选理由与打分明细合成卡片文案。

    入选理由往往已经说清了某个维度（例如「命中多重信号：发布/上线」），
    若再把同义的分项理由原样附上，卡片上会出现两遍「信号」字样。
    这里按维度去重，保留信息量更高的一条。
    """
    merged = [why]
    for reason in breakdown.reasons:
        if "信号" in why and reason.startswith("涉及"):
            continue
        if "独立来源" in why and reason.endswith("个独立来源报道"):
            continue
        if "社区热度" in why and reason.startswith("HN "):
            continue
        if reason not in merged:
            merged.append(reason)
    return merged[:4]


def select_highlights(
    articles: list[Article],
    clusters: dict[str, Cluster],
    now: datetime,
    limit: int | None = None,
) -> list[Highlight]:
    """挑选「今日重点」。同一事件簇只取分数最高的那一条，避免重点列表被单事件占满。"""
    maximum = limit or config.HIGHLIGHT_MAX_ITEMS
    candidates: list[tuple[float, Article, ScoreBreakdown, Cluster | None, str]] = []

    for article in articles:
        cluster = clusters.get(article.cluster_id) if article.cluster_id else None
        breakdown = evaluate(article, cluster, now)
        why = _is_highlight(article, breakdown)
        if why is not None:
            candidates.append((breakdown.score, article, breakdown, cluster, why))

    candidates.sort(key=lambda item: (-item[0], item[1].id))

    picked: list[Highlight] = []
    used_clusters: set[str] = set()
    for _, article, breakdown, cluster, why in candidates:
        if cluster is not None:
            if cluster.id in used_clusters:
                continue
            used_clusters.add(cluster.id)
        # 时间不是「理由」：重点卡片已经单独渲染了相对时间，`reasons` 里那句
        # 「1 天前」会被再渲染一遍，卡片上就出现两个时间（截图核对时发现的）。
        # 这里用**同一个函数算出的字符串**做比对，而不是靠正则猜时间格式 ——
        # 时间格式将来变了，这个比对仍然成立。
        time_reason = relative_time_zh(article.published_at or article.first_seen_at, now)
        picked.append(
            Highlight(
                article_id=article.id,
                title=article.title,
                url=article.url,
                source_name=article.source_name,
                # 展示名与配图从条目上原样带过来：重点卡片要能直接渲染，
                # 不必让前端为了显示一个源名再去 `news.json` 里反查。
                source_name_en=article.source_name_en,
                image_url=article.image_url,
                score=breakdown.score,
                reasons=[r for r in _card_reasons(why, breakdown) if r != time_reason],
                cluster_size=breakdown.cluster_size,
                independent_sources=breakdown.independent_sources,
            )
        )
        if len(picked) >= maximum:
            break
    return picked