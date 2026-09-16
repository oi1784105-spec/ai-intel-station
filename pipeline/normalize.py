"""归一化：把各源的 `RawItem` 转成统一的 `Article`。

这里是「诚实时间」原则的落点：`published_at` **只**来自源站声称的发布时间，
解析不出或不可信时置 `None` 并记录 `published_note`，绝不用抓取时间顶替。
抓取时间单独存在 `first_seen_at` 中，只用于判定「本次运行的新内容」。
"""

from __future__ import annotations

from datetime import datetime

from dataclasses import dataclass, field

from . import config, dedupe
from .models import Article, RawItem
from .parse import clean_image_url, clean_summary, detect_lang, parse_published, squeeze


@dataclass
class BatchResult:
    """一次批量归一化的结果与诊断计数。"""

    articles: list[Article] = field(default_factory=list)
    #: 因源站时区标注错误而被更正的条目数。
    tz_corrected: int = 0
    #: 因是纯导航样板或过短而被丢弃的摘要数。
    summary_dropped: int = 0
    #: 成功提取到配图的条目数。这是「有图显图」覆盖率的**实测值**，
    #: 前端占位块的实际比例因此可核对，而不是靠估计。
    with_image: int = 0


def normalize_item(
    item: RawItem, source: config.SourceConfig, now: datetime
) -> Article | None:
    """把一条原始条目归一化为 `Article`。

    返回 `None` 表示该条目缺少展示与去重都必需的最小字段（标题或链接），必须丢弃。
    """
    title = squeeze(item.title)
    url = (item.url or "").strip()
    if not title or not url:
        return None

    canonical = dedupe.canonical_url(url)
    if not canonical:
        return None

    published_at, note = parse_published(
        item.published_raw, now, offset_override_minutes=source.declared_utc_offset_minutes
    )
    summary = clean_summary(item.summary_raw)
    detected = detect_lang(f"{title} {summary or ''}")
    if detected == "other":
        # 检测不出语言时退回源站声明的语言，避免出现第三类语言值。
        detected = source.lang

    return Article(
        id=dedupe.make_article_id(canonical),
        title=title,
        url=url,
        canonical_url=canonical,
        source_id=source.id,
        source_name=source.name,
        # 展示用英文名：与 `source.name` 并列写入，界面只读这一项，
        # 中文源与英文源的标签形态因此一致。
        source_name_en=source.display_name,
        source_lang=source.lang,
        source_official=source.official,
        source_weight=source.weight,
        published_at=published_at,
        published_raw=item.published_raw or None,
        published_note=note,
        first_seen_at=now,
        last_seen_at=now,
        summary=summary,
        summary_source="feed" if summary else "none",
        image_url=clean_image_url(item.image_raw),
        lang=detected,
        heat=item.heat,
        comments=item.comments,
    )


def normalize_batch(
    items: list[RawItem], source: config.SourceConfig, now: datetime
) -> BatchResult:
    """批量归一化，并统计被更正的时区与被丢弃的样板摘要。

    两个计数都是运行报告里的**证据**：它们证明源站的数据缺陷被定位到并逐条处理，
    而不是被静默吞掉。
    """
    result = BatchResult()
    for item in items:
        article = normalize_item(item, source, now)
        if article is None:
            continue
        result.articles.append(article)
        if article.published_note == "tz_corrected":
            result.tz_corrected += 1
        if article.summary is None and item.summary_raw:
            result.summary_dropped += 1
        if article.image_url is not None:
            result.with_image += 1
    return result