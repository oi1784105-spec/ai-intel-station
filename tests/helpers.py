"""测试用的对象构造助手。

集中在一处，避免每个测试文件各造一套 —— 造错了会让断言测到假对象的行为。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from pipeline import dedupe
from pipeline.models import Article

#: 全部测试共用的基准时刻，固定值使断言不随运行日期漂移。
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def stable_slug(text: str) -> str:
    """稳定短串。不用内置 `hash()` —— 它对 str 加盐，跨进程不确定。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def make_article(
    title: str,
    *,
    source_id: str = "qbitai",
    source_name: str | None = None,
    url: str | None = None,
    published_at: datetime | None = NOW,
    first_seen_at: datetime | None = None,
    **overrides,
) -> Article:
    """构造一条归一化条目，只填必填字段，其余走默认值。"""
    resolved_url = url or f"https://example.com/{stable_slug(title)}"
    canonical = dedupe.canonical_url(resolved_url)
    seen = first_seen_at or NOW
    fields = {
        "id": dedupe.make_article_id(canonical),
        "title": title,
        "url": resolved_url,
        "canonical_url": canonical,
        "source_id": source_id,
        "source_name": source_name or source_id,
        "source_lang": "zh" if source_id in {"qbitai", "infoq-ai"} else "en",
        "published_at": published_at,
        "published_raw": published_at.isoformat() if published_at else None,
        "first_seen_at": seen,
        "last_seen_at": seen,
    }
    fields.update(overrides)
    return Article(**fields)


def hours_ago(hours: float) -> datetime:
    """相对 `NOW` 的过去时刻，用于时效相关的断言。"""
    return NOW - timedelta(hours=hours)