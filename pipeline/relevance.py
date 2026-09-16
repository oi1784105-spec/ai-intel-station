"""AI 相关性过滤，用于通用科技源的降噪。

只有标记了 `relevance_filter` 的源才走这里：AI 垂类源（量子位、InfoQ AI 频道、OpenAI 等）
本身就是 AI 内容，不过滤。爱范儿、少数派、Solidot、开源中国这类通用源必须过滤，
否则《每日 AI 情报站》里会混进手机评测与消费电子新闻。

**拉丁词必须用词边界匹配**：`AI` 做子串匹配会命中 `said`、`email`、`chair`、
`available`、`maintain`、`certain`，实测能把绝大多数无关条目误判为 AI 相关。
"""

from __future__ import annotations

import re

from . import config

#: 预编译的拉丁词边界模式。长词优先编译在前，便于优先展示更具体的匹配。
_LATIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (term, re.compile(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])"))
    for term in sorted(config.LATIN_AI_TERMS, key=len, reverse=True)
)
_CJK_TERMS: tuple[str, ...] = tuple(sorted(config.CJK_AI_TERMS, key=len, reverse=True))


def matched_terms(title: str, summary: str | None = None) -> list[str]:
    """返回命中的 AI 相关词。

    标题命中与仅摘要命中的可信度不同：标题命中说明这条内容本身就是讲 AI 的，
    （实测）摘要命中常常只是文末推荐里的「相关阅读」。
    """
    title_lower = (title or "").lower()
    haystack = f"{title_lower} {summary or ''}".lower()

    hits: list[str] = []
    for term, pattern in _LATIN_PATTERNS:
        if pattern.search(haystack):
            hits.append(term)
    for term in _CJK_TERMS:
        if term.lower() in haystack:
            hits.append(term)
    return hits


def matched_in_title(title: str) -> list[str]:
    """仅在标题中匹配，用于收紧判定。"""
    return matched_terms(title, None)


def is_ai_related(title: str, summary: str | None = None) -> bool:
    """只要标题或摘要命中任一 AI 相关词即视为相关。"""
    return bool(matched_terms(title, summary))


def should_keep(title: str, summary: str | None, source: config.SourceConfig) -> bool:
    """按源配置决定是否保留。未开启过滤的源一律保留。"""
    if not source.relevance_filter:
        return True
    return is_ai_related(title, summary)