"""管线数据模型。

时间口径是本题的关键：`published_at`（来源声称的发布时间）与 `first_seen_at`（本管线首次看到
它的时间）严格分离，前者才可用于「今天 / 近 7 天」筛选与界面展示。
抓取时刻只记录在 `first_seen_at` / `last_seen_at` 中，**绝不冒充发布时间**。

所有跨进程读写的 JSON 边界（源站 feed、本地 fixtures、data/*.json）都经 pydantic 校验；
同进程内的函数调用不重复校验。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: 单个源的采集结果状态。`parse_warning` 与 `stale` 是两个容易被漏掉的失效模式：
#: 前者是「HTTP 200 却解析出 0 条」（源格式变了，解析器静默失效），
#: 后者是「端点活着但内容已死」（官方博客改版后 feed 停更）。
SourceStatus = Literal["ok", "no_new", "parse_warning", "stale", "failed"]

#: 整次运行的结论。
OverallStatus = Literal["success", "partial", "no_new", "failed"]

#: `published_raw` 无法直接采信时记录的原因，前端据此展示诚实的时间文案。
#: `tz_corrected` 不是异常而是**已修复**：源站时区标注错误，已按 `declared_utc_offset_minutes` 更正。
PublishNote = Literal[
    "unparseable", "future_date", "tz_assumed", "tz_corrected", "implausible", "missing"
]


class RawItem(BaseModel):
    """适配器输出的未归一化条目，字段取值直接来自源站，不做推断。"""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    title: str
    url: str
    published_raw: str | None = None
    summary_raw: str | None = None
    #: 配图 URL 原样取自 feed 载荷（enclosure / media:content / media:thumbnail / 摘要 HTML 首张 img）。
    #: 该字段随 feed 一并下载，提取**不产生任何额外网络请求**，故不违反「不抓文章配图」的取舍。
    image_raw: str | None = None
    heat: int | None = None
    comments: int | None = None
    announce_type: str | None = None
    tags: list[str] = Field(default_factory=list)


class Article(BaseModel):
    """归一化后的条目，是 `data/news.json` 的记录单元。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    url: str
    canonical_url: str
    source_id: str
    source_name: str
    #: 统一的英文展示名。界面（卡片 / 今日重点 / 源状态表 / 筛选条）一律用它，
    #: 使中文源与英文源的标签形态一致；`source_name` 保留源站原始名称供溯源与日志。
    source_name_en: str = ""
    source_lang: Literal["zh", "en"]
    source_official: bool = False
    #: 来源权重，取自 `config.SOURCES_BY_ID[id].weight`，随条目固化，
    #: 使打分成为条目的纯函数（历史条目的分数不随源清单调整而漂移）。
    source_weight: float = 1.0

    published_at: datetime | None = None
    published_raw: str | None = None
    published_note: PublishNote | None = None

    #: 首次被本管线看到的时间与最近一次看到的时间。重复导入只推进 `last_seen_at`，
    #: `first_seen_at` 一经写入不再改变 —— 这是「重复导入不产生重复记录」的锚点。
    first_seen_at: datetime
    last_seen_at: datetime

    summary: str | None = None
    summary_source: Literal["feed", "none"] = "none"
    #: 配图 URL（已过协议白名单与长度校验）。多数源不提供配图，为空即前端渲染占位块。
    image_url: str | None = None

    lang: Literal["zh", "en", "other"] = "other"
    heat: int | None = None
    comments: int | None = None

    #: 所属事件簇 id；仅当该事件有多来源报道时非空。
    cluster_id: str | None = None
    #: 所属事件簇规模与该簇的独立来源数，由聚合阶段写入。
    #: 固化下来供前端直接展示，避免前端去解析 `reasons` 里的文案。
    cluster_size: int = 1
    independent_sources: int = 1
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class SourceOutcome(BaseModel):
    """单个源在一次运行中的采集结果，是运行报告与 `update-status.json` 的单元。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    #: 与 `Article.source_name_en` 同源，使运行报告、状态页与前端展示共用一套源名。
    name_en: str = ""
    url: str
    status: SourceStatus
    http_status: int | None = None
    items_seen: int = 0
    items_kept: int = 0
    items_filtered: int = 0
    #: 因源站时区标注错误而被更正的条目数。非零即证明该源的时间缺陷已被处理。
    items_tz_corrected: int = 0
    #: 因是纯导航样板或过短而被丢弃的摘要数。
    items_summary_dropped: int = 0
    #: 成功提取到配图的条目数。用于如实统计「有图显图」的实际覆盖率，而非估算。
    items_with_image: int = 0
    #: 因早于滚动窗口而被**在合并前**丢弃的条目数。这类条目若进入合并也会立刻被
    #: retention 淘汰，结果是每轮都把同一批条目当成「新增」再淘汰一遍。
    items_out_of_window: int = 0
    items_new: int = 0
    items_dup: int = 0
    latest_published_at: datetime | None = None
    duration_ms: int = 0
    error: str | None = None


class RunReport(BaseModel):
    """一次完整运行的记录，追加进 `data/runs.json` 作为「定时任务持续工作」的证据。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    trigger: Literal["manual", "cron", "fixture"]
    offline: bool = False
    sources: list[SourceOutcome] = Field(default_factory=list)
    overall: OverallStatus = "success"
    total_in_store: int = 0
    total_new: int = 0
    total_dup: int = 0
    highlights: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Highlight(BaseModel):
    """「今日重点」条目：一条条目的推荐理由与跨源命中信息。"""

    model_config = ConfigDict(extra="forbid")

    article_id: str
    title: str
    url: str
    source_name: str
    #: 与 `Article` 同义的英文展示名与配图，供「今日重点」卡片直接渲染，
    #: 避免前端为了显示源名再去 `news.json` 里反查。
    source_name_en: str = ""
    image_url: str | None = None
    score: float
    reasons: list[str] = Field(default_factory=list)
    cluster_size: int = 1
    independent_sources: int = 1


class NewsStore(BaseModel):
    """`data/news.json` 的顶层结构。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    generated_at: datetime
    window_days: int
    sources: list[dict] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    articles: list[Article] = Field(default_factory=list)


class Meta(BaseModel):
    """`data/meta.json`：供前端页脚展示的运行摘要。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    last_run_id: str
    last_run_at: datetime
    last_success_at: datetime | None = None
    total_articles: int = 0
    source_count: int = 0
    success_rate_7d: float | None = None
    window_days: int = 30
    highlights: list[Highlight] = Field(default_factory=list)