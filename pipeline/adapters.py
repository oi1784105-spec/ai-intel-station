"""四类源的适配器：把异构响应统一成 `RawItem` 列表。

`rss` / `atom` 走 feedparser —— 这是本方案唯一保留的格式解析依赖，价值在于**容错**：
实测品玩的 feed 是非法 XML（`not well-formed: line 56`），feedparser 仍能救出条目，
而 `xml.etree` 会直接抛异常丢掉整个源。`bozo` 标记会被记录到运行报告。
`hn` / `arxiv` 是 API 型，各有专属的取数策略。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import feedparser

from . import config
from .fetch import FetcherProtocol, FetchError
from .models import RawItem
from .parse import clean_image_url, find_first_image, parse_published, squeeze


@dataclass
class AdapterResult:
    """一个源的解析结果。"""

    items: list[RawItem] = field(default_factory=list)
    #: 响应中的原始条目数（未经过滤与裁剪），用于区分「源没更新」与「解析器失效」。
    seen: int = 0
    #: feedparser 的容错标记。为真表示该源 XML 不合法但被救回了条目。
    bozo: bool = False
    bozo_exception: str | None = None
    http_status: int | None = None


def collect_source(
    source: config.SourceConfig, fetcher: FetcherProtocol, now: datetime
) -> AdapterResult:
    """按源类型分派采集，返回统一结果。网络错误按 `FetchError` 向上抛。"""
    if source.kind == "hn":
        return _collect_hacker_news(source, fetcher, now)
    if source.kind == "arxiv":
        return _collect_arxiv(source, fetcher, now)
    return _collect_feed(source, fetcher, now)


def _raw_date(entry: Any) -> str | None:
    """取源站声称的原始时间字符串。

    优先取原始字符串而非 feedparser 已解析的结构 —— 本方案要求自己解析时间，
    这样才能记录「时区缺失」「无法解析」等原因，而不是被依赖悄悄补一个默认值。
    """
    for key in ("published", "updated", "created", "issued", "date"):
        value = entry.get(key)
        if value:
            return str(value)
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return time.strftime("%Y-%m-%dT%H:%M:%S%z", value)
    return None


def _entry_summary(entry: Any) -> str:
    """取条目摘要。不同源分别用 `summary` / `description` / `content`。"""
    for key in ("summary", "description"):
        value = entry.get(key)
        if value:
            return str(value)
    contents = entry.get("content") or []
    if contents and isinstance(contents, list):
        return str(contents[0].get("value") or "")
    return ""


def _entry_html(entry: Any) -> str:
    """把条目里所有可能承载正文的字段拼成一个片段，**仅供配图扫描使用**。

    不能复用 `_entry_summary()`：后者按「description 优先」只返回一个字段，
    而实测 爱范儿 / 钛媒体 / The Verge 的 `description` 只有几十字导语，
    配图（20 / 2 / 1 张）全部在 `<content:encoded>` —— 也就是 feedparser 的
    `entry.content[0].value` 里。只看 description 会**稳定漏掉这三个源的全部配图**，
    且不会报错，属于静默降级，必须靠这个合并取数避免。
    """
    parts: list[str] = []
    for key in ("summary", "description"):
        value = entry.get(key)
        if value:
            parts.append(str(value))
    for content in entry.get("content") or []:
        if isinstance(content, dict):
            parts.append(str(content.get("value") or ""))
    return "\n".join(parts)


def _media_url(media: Any) -> str | None:
    """从 Media RSS 结构里取图片地址；`type` 非图片时丢弃（同结构也承载音视频）。"""
    if not isinstance(media, dict):
        return None
    media_type = str(media.get("type") or "")
    if media_type and not media_type.startswith("image"):
        return None
    return clean_image_url(media.get("url") or media.get("href"))


def _entry_image(entry: Any) -> str | None:
    """取条目配图 URL。**只读 feed 载荷里已有的字段，不额外请求文章页。**

    按实测命中率排序，逐个载体尝试，找不到就返回 `None` 让前端渲染占位块：

    1. `media_content` —— Media RSS 正文，实测 Google AI Blog 条目带此字段
    2. `media_thumbnail` —— 同标准的缩略图变体
    3. `enclosures` —— RSS 标准附件（播客也用它，故校验 `type` 为图片）
    4. `links` 中 `rel="enclosure"` 的图片项
    5. 摘要与正文 HTML（`description` + `content:encoded`）里的第一张 `<img>` —— 多数中文源的配图只在这里

    第 5 步既是兜底也是主力：中文源几乎不写 Media RSS，配图全靠正文 HTML。
    这也是「不抓文章配图」与「有图显图」能同时成立的原因 —— 成本是零次额外请求。
    """
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key) or []:
            url = _media_url(media)
            if url:
                return url

    for enclosure in entry.get("enclosures") or []:
        url = _media_url(enclosure)
        if url:
            return url

    for link in entry.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "enclosure":
            url = clean_image_url(link.get("href"))
            if url:
                return url

    return find_first_image(_entry_html(entry))


def _cap_by_date(items: list[RawItem], limit: int | None, now: datetime) -> list[RawItem]:
    """按发布时间倒序裁剪到 `limit` 条。

    必要性：OpenAI News 单 feed 实测 1193 条、arXiv 单次 634 条，
    不裁剪会在首次运行就把 30 天窗口灌满并撑大产物体积。
    """
    if limit is None or len(items) <= limit:
        return items
    ordered = sorted(
        items,
        key=lambda item: (parse_published(item.published_raw, now)[0] or datetime.min.replace(
            tzinfo=now.tzinfo)),
        reverse=True,
    )
    return ordered[:limit]


def _collect_feed(
    source: config.SourceConfig, fetcher: FetcherProtocol, now: datetime
) -> AdapterResult:
    """RSS / Atom 走 feedparser。"""
    response = fetcher.get(source.url)
    parsed = feedparser.parse(response.content)

    entries = parsed.get("entries") or []
    items: list[RawItem] = []
    for entry in entries:
        title = squeeze(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            # 标题或链接缺失的条目无法展示也无法去重，直接丢弃并计入 seen 差值。
            continue
        items.append(
            RawItem(
                source_id=source.id,
                title=title,
                url=link,
                published_raw=_raw_date(entry),
                summary_raw=_entry_summary(entry),
                image_raw=_entry_image(entry),
                tags=[
                    str(tag.get("term"))
                    for tag in (entry.get("tags") or [])
                    if tag.get("term")
                ],
            )
        )

    truncated = _cap_by_date(items, source.max_items, now)
    return AdapterResult(
        items=truncated,
        seen=len(entries),
        bozo=bool(parsed.get("bozo")),
        bozo_exception=(
            str(parsed.get("bozo_exception"))[:200] if parsed.get("bozo") else None
        ),
        http_status=response.status,
    )


def _collect_arxiv(
    source: config.SourceConfig, fetcher: FetcherProtocol, now: datetime
) -> AdapterResult:
    """arXiv cs.AI。

    实测约束：单次响应 1.34MB / 634 条 item，RSS 端点**不支持** `max_results` 参数，
    因此限流只能在本侧做（`max_items=20`）。仅保留 `new` / `replace` 两类发布，
    跳过 cross-list 与 replace-cross，避免重复计数。
    """
    response = fetcher.get(source.url)
    parsed = feedparser.parse(response.content)
    allowed = set(source.params.get("announce_types") or ())

    entries = parsed.get("entries") or []
    items: list[RawItem] = []
    for entry in entries:
        announce = (
            entry.get("arxiv_announce_type")
            or entry.get("announce_type")
            or entry.get("arxiv_announce_type_")
        )
        announce = str(announce).strip().lower() if announce else None
        if allowed and announce and announce not in allowed:
            continue

        title = squeeze(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        items.append(
            RawItem(
                source_id=source.id,
                title=title,
                url=link,
                published_raw=_raw_date(entry),
                summary_raw=_entry_summary(entry),
                image_raw=_entry_image(entry),
                announce_type=announce,
                tags=[str(tag.get("term")) for tag in (entry.get("tags") or []) if tag.get("term")],
            )
        )

    truncated = _cap_by_date(items, source.max_items, now)
    return AdapterResult(
        items=truncated,
        seen=len(entries),
        bozo=bool(parsed.get("bozo")),
        bozo_exception=(
            str(parsed.get("bozo_exception"))[:200] if parsed.get("bozo") else None
        ),
        http_status=response.status,
    )


def _hn_track_url(query: str, numeric_filters: list[str], page_size: int) -> str:
    """构造 HN Algolia 查询 URL。`numericFilters` 是逗号分隔的与关系。"""
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": ",".join(numeric_filters),
        "hitsPerPage": str(page_size),
    }
    return f"{config.SOURCES_BY_ID['hn'].url}?{urlencode(params)}"


def _collect_hacker_news(
    source: config.SourceConfig, fetcher: FetcherProtocol, now: datetime
) -> AdapterResult:
    """Hacker News 双轨采集。

    硬性要求是「多个来源」，单一 HN 轨道无法满足，两条轨道的分工是：

    - **高信号轨**：`points > min_points_high`，捞出「已确认有热度」的近期帖子
    - **新鲜轨**：最近 `fresh_hours` 小时内 `points > min_points_fresh`，捞出「正在起势」的帖子

    裸 `query=AI` 不可用：实测 20/20 条低于 10 分、最高仅 4 分，等同噪音
    （`search_by_date` 按时间排序，不代表相关度）。
    """
    params = source.params
    query = str(params.get("query", "AI"))
    page_size = int(params.get("page_size", 20))

    tracks = [
        ("high", [f"points>{int(params['min_points_high'])}",
                  f"created_at_i>{int((now - timedelta(days=config.WINDOW_DAYS)).timestamp())}"]),
    ]
    if params.get("fresh_hours"):
        fresh_after = int((now - timedelta(hours=int(params["fresh_hours"]))).timestamp())
        tracks.append(
            ("fresh", [f"points>{int(params['min_points_fresh'])}",
                       f"created_at_i>{fresh_after}"])
        )

    items: list[RawItem] = []
    seen = 0
    for _track_name, filters in tracks:
        url = _hn_track_url(query, filters, page_size)
        payload = fetcher.get_json(url)
        hits = payload.get("hits") or []
        seen += len(hits)
        for hit in hits:
            title = squeeze(hit.get("title") or "")
            if not title:
                continue
            object_id = hit.get("objectID")
            url_value = (hit.get("url") or "").strip() or (
                f"https://news.ycombinator.com/item?id={object_id}"
            )
            items.append(
                RawItem(
                    source_id=source.id,
                    title=title,
                    url=url_value,
                    published_raw=hit.get("created_at"),
                    summary_raw="",
                    heat=int(hit.get("points") or 0),
                    comments=int(hit.get("num_comments") or 0),
                )
            )

    return AdapterResult(items=items, seen=seen, bozo=False, http_status=200)