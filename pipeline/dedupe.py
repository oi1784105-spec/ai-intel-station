"""URL 归一化、标题归一化，以及四段去重与事件聚合共用的判定原语。

去重的四段顺序（前一段解决不了才落到下一段）：

1. `id` 相同 —— 由归一化 URL 派生，解决「同一 URL 重复导入」
2. `canonical_url` 相同 —— 解决「URL 带追踪参数 / 大小写 / 尾斜杠差异」
3. 标题归一后完全相同 —— 解决「同一文章被源站改写链接」
4. 标题 3-gram Jaccard ≥ 阈值 —— 解决「同一文章标题带后缀差异」

第 4 段用**长度护栏**先过滤：短标题被长标题包含时 3-gram 交集天然稀疏，
不做护栏会把「GPT-6」这类短文误判为重复。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import config

#: 归一标题时丢弃的字符：空白、标点、符号。保留字母数字与中日韩文字。
_TITLE_DROP_RE = re.compile(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

#: 实体词：标题中的拉丁专名（OpenAI、GPT-6、Llama）。跨源报道同一事件时这些词高度重合，
#: 也是「中文源 + 英文源报道同一件事」时唯一可用的公共信号。
_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:[.-][A-Za-z0-9]+)*\b")
#: 不具区分度的泛用词。两类：
#: ① 功能词（冠词/介词/代词/疑问词/星期月份）—— 冠词尤其致命：标题里必然出现的
#:    `A` / `An` / `The` 会轻易凑够「共享实体数」而把两篇无关文章并成一簇。
#: ② 通用技术名词（Agent / Model / Data / LLM …）—— 它们描述**类别**而非**具体对象**，
#:    是 arXiv 标题里最常见的 capitalized 词，是最主要的误合并来源。
_ENTITY_STOP: frozenset[str] = frozenset({
    # 功能词
    "A", "An", "The", "This", "That", "New", "How", "Why", "What", "When", "Where", "Who",
    "AI", "It", "In", "On", "For", "And", "Of", "To", "With", "Is", "Are", "As", "At",
    "Show", "Ask", "We", "You", "Your", "My", "Our", "Its", "Can", "Will", "Not",
    "After", "Before", "From", "By", "But", "Or", "If", "So", "Up", "Out", "Now",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
    # 通用技术名词
    "Adaptive", "Agent", "Agents", "Agentic", "Application", "Applications",
    "Approach", "Architecture", "Benchmark", "Chip", "Chips", "Dataset", "Datasets",
    "Data", "Evaluation", "Framework", "GPU", "GPUs", "GenAI", "Guide", "Inference",
    "Intelligence", "Language", "Learning", "LLM", "LLMs", "Machine", "Method",
    "Model", "Models", "Network", "Networks", "Platform", "Policy", "Real", "Report",
    "Research", "Review", "Robotics", "Search", "Security", "Service", "Services",
    "Study", "System", "Systems", "Token", "Tokens", "Tool", "Tools", "Training",
    "Vision",
})


def canonical_url(raw: str) -> str:
    """把 URL 归一为可比较形式。

    小写主机、去 `www.`、去默认端口、按 `config.TRACKING_PARAMS` 剥离追踪参数、
    去 fragment、去尾斜杠、统一为 https。

    实测必要性：中文源普遍携带 `utm_*` / `spm` 参数，同一篇文章二次抓取时 URL 并不相同，
    不归一会被判成新条目。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.netloc:
        return text

    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key.lower() not in config.TRACKING_PARAMS
        and not key.lower().startswith(config.TRACKING_PARAM_PREFIXES)
    ]
    scheme = parts.scheme.lower() or "https"
    if scheme in ("http", "https"):
        scheme = "https"
    path = re.sub(r"/{2,}", "/", parts.path)
    # 站点根归一为 `/`，使 `https://a.com` 与 `https://a.com/` 落到同一个 id。
    path = path.rstrip("/") or "/"

    return urlunsplit((scheme, host, path, urlencode(query_pairs), ""))


def make_article_id(canonical: str) -> str:
    """由归一 URL 派生稳定 id。同一篇文章在任何一天推导出的 id 都相同。"""
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


def normalize_title(title: str) -> str:
    """标题归一：NFKC + 小写 + 去空白标点。"""
    return _TITLE_DROP_RE.sub("", unicodedata.normalize("NFKC", title or "").lower())


def trigram_set(normalized: str) -> frozenset[str]:
    """3-gram 集合，用于重复判定。短于 3 字符时整体作为一个 gram。"""
    n = len(normalized)
    if n == 0:
        return frozenset()
    if n <= 3:
        return frozenset({normalized})
    return frozenset(normalized[i : i + 3] for i in range(n - 2))


def shingle_set(normalized: str) -> frozenset[str]:
    """2-gram ∪ 3-gram，用于事件聚合。含 1-gram 会让常见字撑高相似度。"""
    n = len(normalized)
    if n == 0:
        return frozenset()
    if n < 3:
        return frozenset({normalized})
    grams = {normalized[i : i + 2] for i in range(n - 1)}
    grams |= {normalized[i : i + 3] for i in range(n - 2)}
    return frozenset(grams)


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """集合 Jaccard 相似度。任一侧为空返回 0。"""
    if not left or not right:
        return 0.0
    union = len(left | right)
    if union == 0:
        return 0.0
    return len(left & right) / union


def blocking_grams(normalized: str, size: int) -> set[str]:
    """生成倒排索引分桶键。

    用于把 O(n²) 的两两比较压缩到「至少共享一个 `size`-gram」的候选对上。
    代价是可能漏掉「3-gram 相似但无 `size`-gram 交集」的极端情况，
    因此精确的 id / 归一 URL 相等始终作为第一优先级先行判定，不依赖分桶。
    """
    length = len(normalized)
    if length == 0:
        return set()
    if length < size:
        return {normalized}
    return {normalized[i : i + size] for i in range(length - size + 1)}


def entities(title: str) -> frozenset[str]:
    """抽取标题中的拉丁专名，用于跨源事件聚合的保底信号。"""
    found = {
        token
        for token in _ENTITY_RE.findall(title or "")
        if len(token) >= 2 and token not in _ENTITY_STOP
    }
    return frozenset(found)


def is_duplicate(title_a: str, title_b: str) -> tuple[bool, str]:
    """判定两个标题是否指向同一篇文章。返回 `(是否重复, 理由)`。

    用**包含度**而非 Jaccard 度量：实测的重复形态是「同一标题被源站加上来源后缀」
    （`OpenAI 发布 GPT-6 - 量子位`），此时包含度为 1.0 而 Jaccard 只有 0.84。
    """
    left, right = normalize_title(title_a), normalize_title(title_b)
    if not left or not right:
        return False, ""
    if left == right:
        return True, "标题归一后完全相同"

    shorter, longer = sorted((len(left), len(right)))
    if shorter / longer < config.DUP_TITLE_MIN_LENGTH_RATIO:
        # 长度差异过大：短标题被长标题包含时包含度必然虚高（`AI` 会被长标题「包含」）。
        return False, ""

    left_grams, right_grams = trigram_set(left), trigram_set(right)
    denominator = min(len(left_grams), len(right_grams))
    if denominator == 0:
        return False, ""
    containment = len(left_grams & right_grams) / denominator
    if containment >= config.DUP_TITLE_CONTAINMENT:
        return True, f"标题包含度 {containment:.2f}"
    return False, ""


def shingle_similarity(title_a: str, title_b: str) -> float:
    """两条标题的 2/3-gram Jaccard 相似度。用于事件聚合与诊断输出。"""
    left, right = normalize_title(title_a), normalize_title(title_b)
    if not left or not right:
        return 0.0
    return jaccard(shingle_set(left), shingle_set(right))


def same_event(
    title_a: str,
    title_b: str,
    entities_a: frozenset[str] | None = None,
    entities_b: frozenset[str] | None = None,
) -> tuple[bool, str]:
    """判定两条标题是否在报道同一事件（不删条目，只建立聚合关系）。

    `entities_a` / `entities_b` 由调用方传入**经过稀有度过滤**的实体集
    （见 `cluster.build_clusters` 的 IDF 过滤）。省略时退化为对原始实体集判定，
    仅用于单元测试与诊断，生产路径必须传过滤后的集合 —— 否则
    `Agent` / `Model` 这类通用名词会让不相干的文章并成一簇。
    """
    left, right = normalize_title(title_a), normalize_title(title_b)
    if not left or not right:
        return False, ""

    similarity = jaccard(shingle_set(left), shingle_set(right))
    if similarity >= config.CLUSTER_JACCARD:
        return True, f"标题相似度 {similarity:.2f}"

    left_entities = entities(title_a) if entities_a is None else entities_a
    right_entities = entities(title_b) if entities_b is None else entities_b
    shared = left_entities & right_entities
    if len(shared) >= config.CLUSTER_MIN_SHARED_ENTITIES:
        return True, "共同实体：" + "、".join(sorted(shared)[:3])
    return False, ""