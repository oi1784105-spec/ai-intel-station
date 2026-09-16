"""源注册表与全部可调参数。

部署相关的可调项集中在本模块，不散落到业务代码中；每一项在 README「配置说明」里有对应解释。
本文件中的源清单、条数与日期格式均为 2026-09-16 本机实测结果。

**源名称有两个字段，分工不同**：`name` 是源的原始名称（中文源即中文名），用于溯源与日志；
`name_en` 是统一的英文展示名，界面（卡片、今日重点、源状态表、筛选条）一律用它。
这样源标签在全站形态一致，不会出现中文源显示中文名、英文源显示英文名的混排。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["rss", "atom", "hn", "arxiv"]
Lang = Literal["zh", "en"]


@dataclass(frozen=True)
class SourceConfig:
    """一个信息源的采集配置。

    `params` 承载按 `kind` 区分的专属参数，当前使用的键：

    - `hn`：`min_points_high`、`fresh_hours`、`min_points_fresh`、`page_size`、`query`
    - `arxiv`：`announce_types`（仅保留的发布类型）

    `declared_utc_offset_minutes` 用于更正源站的时区标注错误：源站写出的钟面时间按该偏移
    解释，而不再采信源站自己声称的时区。实测 InfoQ 中文站把北京时间标注成 `GMT`，
    其最新一条 pubDate 恰好等于抓取时刻的北京时间，整源时间因此偏早 8 小时。
    """

    id: str
    name: str
    lang: Lang
    kind: Kind
    url: str
    weight: float = 1.0
    official: bool = False
    relevance_filter: bool = False
    max_items: int | None = None
    declared_utc_offset_minutes: int | None = None
    #: 是否作为生产源参与日常采集。置 `False` 表示**已知失效但留档**：
    #: 保留其配置与离线样本，用于「疑似停更」判定的回归测试，
    #: 而不是从代码里删掉、让这条经验无从追溯。`--only` 仍可显式采集这类源。
    enabled: bool = True
    #: 该源在中国大陆网络下需要代理才能访问。置真时，本地直连运行会出现
    #: `failed(timeout)` 而云端（GitHub Actions，出口在境外）正常 ——
    #: 这是部署环境差异，不是源或代码的缺陷，README「已知限制」有对应说明。
    needs_overseas_network: bool = False
    #: 统一的英文展示名。留空时回退到 `name`（见 `display_name`）。
    name_en: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """界面展示用名称：优先英文名，未提供时回退原始名称，绝不返回空串。"""
        return self.name_en or self.name


#: 生产源清单。实测时点 2026-09-16，全部返回 HTTP 200。
#: `official` 标记官方一手源；`relevance_filter` 标记需要 AI 相关性过滤的通用科技源。
ALL_SOURCES: tuple[SourceConfig, ...] = (
    # ---- 中文：AI 垂类 ----
    SourceConfig("qbitai", "量子位", "zh", "rss", "https://www.qbitai.com/feed",
                 name_en="QbitAI", weight=1.10),
    # 实测：本源 pubDate 标注为 GMT，但最新一条恰好等于抓取时刻的北京时间，
    # 整源时间比真实 UTC 早 8 小时，必须按北京时间重新解释。
    SourceConfig("infoq-ai", "InfoQ 中国 AI", "zh", "rss", "https://www.infoq.cn/feed/ai",
                 name_en="InfoQ China", weight=1.05, declared_utc_offset_minutes=480),
    # ---- 中文：通用科技源，需相关性过滤 ----
    SourceConfig("leiphone", "雷锋网", "zh", "rss", "https://www.leiphone.com/feed",
                 name_en="Leiphone", weight=1.00, relevance_filter=True),
    SourceConfig("oschina", "开源中国", "zh", "rss", "https://www.oschina.net/news/rss",
                 name_en="OSChina", weight=0.90, relevance_filter=True),
    SourceConfig("ifanr", "爱范儿", "zh", "rss", "https://www.ifanr.com/feed",
                 name_en="iFanr", weight=0.90, relevance_filter=True),
    SourceConfig("sspai", "少数派", "zh", "rss", "https://sspai.com/feed",
                 name_en="Sspai", weight=0.85, relevance_filter=True),
    SourceConfig("solidot", "Solidot", "zh", "rss", "https://www.solidot.org/index.rss",
                 name_en="Solidot", weight=0.90, relevance_filter=True),
    SourceConfig("tmtpost", "钛媒体", "zh", "rss", "https://www.tmtpost.com/rss.xml",
                 name_en="TMTPost", weight=0.85, relevance_filter=True),
    # ---- 英文：官方一手源 ----
    # OpenAI 单 feed 实测 1193 条 / 725KB，必须按 `max_items` 裁剪，否则一天灌满存储。
    SourceConfig("openai", "OpenAI News", "en", "rss", "https://openai.com/news/rss.xml",
                 name_en="OpenAI News", weight=1.30, official=True, max_items=20),
    # 实测：中国大陆网络下 blog.google / google.com 的 TLS 握手直接超时（25s 无响应），
    # 需代理才能访问；GitHub Actions 出口在境外则正常。源本身 200 可用。
    SourceConfig("google-ai", "Google AI Blog", "en", "rss", "https://blog.google/technology/ai/rss/",
                 name_en="Google AI Blog", weight=1.25, official=True,
                 needs_overseas_network=True),
    # ---- 英文：AI 垂类媒体 ----
    SourceConfig("techcrunch-ai", "TechCrunch AI", "en", "rss",
                 "https://techcrunch.com/category/artificial-intelligence/feed/",
                 name_en="TechCrunch AI", weight=1.10),
    SourceConfig("verge-ai", "The Verge AI", "en", "atom",
                 "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
                 name_en="The Verge AI", weight=1.05),
    SourceConfig("mit-techreview-ai", "MIT Tech Review AI", "en", "rss",
                 "https://www.technologyreview.com/topic/artificial-intelligence/feed",
                 name_en="MIT Tech Review AI", weight=1.10),
    SourceConfig("simonw", "Simon Willison", "en", "atom",
                 "https://simonwillison.net/atom/everything/",
                 name_en="Simon Willison", weight=1.00, relevance_filter=True),
    # ---- API 型 ----
    # HN 双轨：高信号轨「最近达到过 high 分」= 既新鲜又有热度；新鲜轨捞 6 小时内已起色的帖子。
    SourceConfig("hn", "Hacker News", "en", "hn",
                 "https://hn.algolia.com/api/v1/search_by_date",
                 name_en="Hacker News", weight=1.20,
                 params={"query": "AI", "min_points_high": 80, "fresh_hours": 6,
                         "min_points_fresh": 20, "page_size": 20}),
    # arXiv 实测单次响应 1.34MB / 634 条 item，RSS 无 max_results 参数，
    # 故以 max_items 限流到 20 条/天，避免与「近 7 天」筛选在数学上冲突。
    SourceConfig("arxiv", "arXiv cs.AI", "en", "arxiv",
                 "https://rss.arxiv.org/rss/cs.AI",
                 name_en="arXiv cs.AI", weight=0.95, max_items=20,
                 params={"announce_types": ("new", "replace")}),
    # ---- 留档源：已知失效，不参与生产采集 ----
    # 实测该源 30 条上限内最新一条停在 2026-08-27，已 20 天无更新。
    # 它是「疑似停更」判定的真实样本，故保留配置与离线样本用于回归，
    # 而不是删掉配置让这条排查经验消失。
    SourceConfig("venturebeat-ai", "VentureBeat AI", "en", "rss",
                 "https://venturebeat.com/category/ai/feed/",
                 name_en="VentureBeat AI", weight=0.95, enabled=False),
)

#: 参与日常采集的生产源。
SOURCES: tuple[SourceConfig, ...] = tuple(s for s in ALL_SOURCES if s.enabled)
#: 全部已登记源（含留档源），供 `--only` 诊断与离线样本抓取使用。
SOURCES_BY_ID: dict[str, SourceConfig] = {s.id: s for s in ALL_SOURCES}

# ---------------------------------------------------------------------------
# 配图
# ---------------------------------------------------------------------------

#: 配图 URL 的最大长度。超过即视为异常丢弃 —— 少数源会内嵌带签名的超长 CDN URL，
#: 那类 URL 既会撑大产物，又往往在几分钟后失效。
IMAGE_URL_MAX_CHARS: int = 600
#: 允许的配图协议。**仅 http/https**：`data:` 会把图片内联进 HTML 撑爆单文件产物，
#: `javascript:` 则是注入向量。源站内容不可信，白名单而不是黑名单。
IMAGE_ALLOWED_SCHEMES: tuple[str, ...] = ("http", "https")
#: 从正文 HTML 里找配图时，排除这些明显不是正文配图的路径片段
#: （头像、表情、像素埋点、图标、源站 feed 默认模板图）。命中即跳过并继续找下一张。
IMAGE_URL_DENY_PATTERNS: tuple[str, ...] = (
    "/avatar", "/emoji", "/smiley", "gravatar", "/icon", "/logo", "/sprite",
    "pixel.gif", "spacer.gif", "blank.gif", "/badge", "/button", ".svg",
    # 钛媒体会给部分条目挂一张固定的 RSS 模板图（实测 5 条有图条目中占 1 条），
    # 它与正文无关。展示一张错图比不展示更糟，故按路径特征排除。
    "/tkrss/",
)
#: 单条摘要 HTML 里最多检查的 `<img>` 数量，避免为找一张图扫完整个长 HTML。
IMAGE_SCAN_MAX_TAGS: int = 12

# ---------------------------------------------------------------------------
# 存储与窗口
# ---------------------------------------------------------------------------

#: `published_at` 早于该天数的条目在合并阶段被淘汰。
WINDOW_DAYS: int = 30
#: 存储硬上限，超出时按 (published_at, first_seen_at) 倒序保留最新的。
#: 取值依据：约 80 条/天 × 30 天 ≈ 2400 条，上限留有冗余。
MAX_STORE_ITEMS: int = 4000
#: `data/runs.json` 保留的最近运行次数。
RETAIN_RUNS: int = 60
#: 摘要截断长度。
MAX_SUMMARY_CHARS: int = 240
#: 摘要短于该字符数视为无信息量，置空而不是当作摘要展示。
#: 依据：实测 279 条 feed 条目中 12.5% 的摘要为空或极短，
#: 其中 InfoQ 中文站 **20/20** 条的摘要都是「点击查看原文>」这类纯导航链接（平均 7 字符）。
SUMMARY_MIN_CHARS: int = 20
#: 纯导航/样板文案，命中即丢弃摘要。前端宁可没有摘要，也不该渲染一句「点击查看原文>」。
SUMMARY_BOILERPLATE: tuple[str, ...] = (
    "点击查看原文", "点击阅读原文", "阅读全文", "查看全文", "阅读原文", "点击阅读",
    "详情请见", "关注我们", "扫码关注", "分享到",
    "read more", "continue reading", "learn more", "view original",
    "the post ", "appeared first on", "originally published", "submitted by",
)
#: 整行模板文案的正则。与上面的子串列表分工不同：子串列表处理「短导航文案」，
#: 正则处理 WordPress 类 feed 的整句模板（`The post <标题> appeared first on <站点>.`）。
#: 末尾都做了长度上界，避免把一句恰好以 `Read more` 开头的正常摘要误杀。
SUMMARY_BOILERPLATE_REGEXES: tuple[str, ...] = (
    r"^the post .{0,140}appeared first on .{1,60}$",
    r"^appeared first on .{1,60}$",
    r"^originally published (at|on) .{1,60}$",
    r"^submitted by .{1,60}$",
    r"^(read|learn|continue|view) more\b.{0,40}$",
    # 中文导航文案的上界必须收紧：`点击查看原文之外，该论文还提出了…` 是正常正文，
    # 放成 {0,20} 会把它当成导航文案丢掉。
    r"^点击?查看?原文.{0,10}$",
    r"^(阅读|查看)全文.{0,10}$",
)
#: 源的最新条目早于该天数即判定为疑似停更（`stale`）。
STALE_SOURCE_DAYS: int = 14

# ---------------------------------------------------------------------------
# 时间解析
# ---------------------------------------------------------------------------

#: 来源声称的时间晚于当前时间超过该小时数，视为异常而非未来时间。
FUTURE_TOLERANCE_HOURS: int = 2
#: 早于该年份的发布时间视为异常。
MIN_PUBLISH_YEAR: int = 2000

# ---------------------------------------------------------------------------
# 去重与事件聚合
# ---------------------------------------------------------------------------

#: 重复判定的标题**包含度**阈值：3-gram 交集 ÷ 较短一侧的 gram 数。
#: 用包含度而非 Jaccard —— 实测的重复形态是「同一标题被源站加上来源后缀」
#: （`xxx - 量子位`），此时包含度为 1.0 而 Jaccard 只有 0.84，用 Jaccard 会漏判。
DUP_TITLE_CONTAINMENT: float = 0.90
#: 重复判定的长度比下限。短标题被长标题包含时包含度必然虚高，必须先用长度护栏挡住。
DUP_TITLE_MIN_LENGTH_RATIO: float = 0.70
#: 标题 2/3-gram Jaccard 阈值，达到即归入同一事件簇（不删条目）。
CLUSTER_JACCARD: float = 0.45
#: 判定同事件所需的**共享稀有实体**数。要求 2 个而非 1 个：
#: 单个实体重合过于脆弱（`Gemini` / `Mistral` 之外的词太容易碰巧相同）。
CLUSTER_MIN_SHARED_ENTITIES: int = 2
#: 实体的区分度上限（文档频率）。在比较窗口内出现次数超过该值的实体视为通用词，
#: 不参与候选生成与判定。这是对静态黑名单的动态补充：黑名单漏掉的词，
#: 只要在这批数据里足够常见，就会被自动淘汰。
CLUSTER_DISTINCTIVE_ENTITY_MAX_DF: int = 3

#: URL 归一化时剥离的追踪参数（小写前缀匹配）。中文源普遍携带 `utm_*` / `spm`，
#: 不剥离会导致同一篇文章在二次抓取时被判定为新条目。
TRACKING_PARAMS: frozenset[str] = frozenset({
    "fbclid", "gclid", "spm", "ref", "source", "from", "share_token",
    "mc_cid", "mc_eid", "igshid", "_hsenc", "_hsmi", "yclid", "weibo_id",
})
TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_",)

# ---------------------------------------------------------------------------
# AI 相关性过滤
# ---------------------------------------------------------------------------

#: 拉丁词必须用词边界匹配 —— 子串匹配会让 `AI` 命中 `said`/`email`/`chair`/`available`/`maintain`。
LATIN_AI_TERMS: tuple[str, ...] = (
    "AI", "AGI", "LLM", "LLMs", "GPT", "RAG", "GenAI", "ChatGPT", "Claude", "Gemini",
    "Llama", "Qwen", "DeepSeek", "Mistral", "Grok", "Copilot", "Midjourney", "Sora",
    "OpenAI", "Anthropic", "DeepMind", "HuggingFace", "NVIDIA", "TPU", "GPU",
    "transformer", "transformers", "multimodal", "agentic", "chatbot", "diffusion",
    "inference", "fine-tuning", "pretraining", "embedding", "hallucination",
    "artificial intelligence", "machine learning", "deep learning", "neural network",
    "large language model", "foundation model", "computer vision", "reinforcement learning",
    "robotics", "autonomous driving", "self-driving", "prompt engineering",
)
#: 中文词用子串匹配即可（无词边界歧义）。
CJK_AI_TERMS: tuple[str, ...] = (
    "人工智能", "大模型", "大语言模型", "智能体", "机器人", "具身智能",
    "深度学习", "机器学习", "神经网络", "多模态", "生成式", "生成式AI",
    "算力", "微调", "预训练", "推理", "幻觉", "提示词", "向量",
    "自动驾驶", "语音识别", "计算机视觉", "强化学习", "开源模型",
    "英伟达", "芯片", "智算",
)

# ---------------------------------------------------------------------------
# 「今日重点」可解释打分
# ---------------------------------------------------------------------------

#: 打分权重。改这里即可调整重点排序，无需改代码。
SCORE_WEIGHTS: dict[str, float] = {
    "source": 3.0,      # 来源权重（官方源 / AI 垂类更高）
    "cross": 2.5,       # 跨源命中度：被越多独立来源报道越重要
    "recency": 1.5,     # 时效衰减
    "signal": 0.8,      # 信号词命中
    "heat": 0.4,        # HN 热度归一
}
#: 时效衰减的半衰窗口（小时）。超过该小时数贡献为 0。
RECENCY_HORIZON_HOURS: int = 72
#: 跨源命中度饱和值：簇内独立来源数达到该值即记满分。
CROSS_SOURCE_SATURATION: int = 4

#: 事件聚合的比较时间窗（小时）。跨源报道是当日事件，无需与 20 天前的条目比较；
#: 收窄窗口同时把 O(n²) 比较量从约 2400 条压到约 240 条。
CLUSTER_WINDOW_HOURS: int = 72
#: 倒排索引分桶时，出现次数超过该值的 gram 视为无区分度的常用搭配，不参与候选生成。
BLOCKING_MAX_POSTINGS: int = 40

#: 信号词：命中即加成，并在 UI 展示为可读理由。
SIGNAL_TERMS: dict[str, tuple[str, ...]] = {
    "发布/上线": ("发布", "上线", "推出", "releases", "launches", "introduces", "unveils", "ships"),
    "开源": ("开源", "open-sources", "open source", "open-source", "weights"),
    "融资/并购": ("融资", "收购", "并购", "funding", "raises", "acquisition", "acquires", "IPO"),
    "安全": ("漏洞", "安全", "攻击", "泄露", "vulnerability", "exploit", "breach", "jailbreak"),
    "基准/榜单": ("榜单", "基准", "评测", "benchmark", "leaderboard", "SOTA", "state of the art"),
    "监管/政策": ("监管", "法案", "合规", "regulation", "lawsuit", "policy", "ban"),
}
#: 「今日重点」的入选规则。入选是**规则判定**而非分数阈值，这样每条理由都能原文讲清；
#: `score` 只负责把入选条目排出先后。
#: 满足以下任一条件即入选：多个独立来源报道 / 社区高热度 / 命中多重信号词 / 官方源发布类内容。
HIGHLIGHT_MIN_INDEPENDENT_SOURCES: int = 2
HIGHLIGHT_MIN_HEAT: int = 150
HIGHLIGHT_MIN_SIGNALS: int = 2
#: 「今日重点」最多条数。
HIGHLIGHT_MAX_ITEMS: int = 5

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 AI-Intelligence-Station/1.0"
)
HTTP_TIMEOUT_SECONDS: float = 20.0
HTTP_RETRIES: int = 2
HTTP_BACKOFF_SECONDS: float = 1.5
#: 单个响应体的字节上限，防止异常大的 feed 拖垮内存。
HTTP_MAX_BYTES: int = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

DATA_DIR: str = "data"
NEWS_PATH: str = "data/news.json"
RUNS_PATH: str = "data/runs.json"
STATUS_PATH: str = "data/update-status.json"
META_PATH: str = "data/meta.json"