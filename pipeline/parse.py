"""时间、文本与配图归一化。

日期处理覆盖实测到的 4 种格式（`+0000` / `GMT` / `+0800` / ISO8601 `+00:00`）与 4 类边界情况。
核心原则：**宁可承认不知道，也不猜**。无法采信的时间一律置 `None` 并记录原因，由前端展示
「发布时间未知」而不是填一个看似合理的假时间。

配图提取遵循同一条原则：只取 feed 载荷里**已经存在**的图片地址，解析不出就置空让前端显示
占位块，不为了一张配图去额外请求文章页（那会把一次运行的请求数从 16 次放大到 200+ 次）。
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from dateutil import parser as dateutil_parser

from . import config

# --- 文本清洗 ---------------------------------------------------------------

#: 连同内容一起丢弃的标签（脚本与样式不承载可展示文本）。
_DROP_CONTENT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1\s*>", re.S | re.I)
#: 承担换行语义的标签，替换为空格以避免相邻词粘连。
_BLOCK_RE = re.compile(r"<br\s*/?>|</p\s*>|</div\s*>|</li\s*>|</h\d\s*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
#: 全部空白字符（含换行）。三种用途共用一套：压平 HTML、合并标题空白、比对前归一。
#: 换行必须在内 —— 否则 `a\n\nb` 压平后是 `a   b` 而不是 `a b`，标题比对也会漏掉跨行差异。
_WS_RE = re.compile(r"[ \t\r\n\v\f\u00a0\u3000]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def html_to_text(raw: str | None) -> str:
    """把 feed 中的 HTML 片段压成单行纯文本。

    摘要来源普遍是 HTML（含 `<img>`、`<div>` 等），必须转纯文本后再入库。
    先反转义再剥标签，可顺带丢弃被转义成实体形式的标签文本。

    前端必须以文本节点渲染该字段（而非 innerHTML），否则源站内容可执行脚本注入页面。
    """
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _DROP_CONTENT_RE.sub(" ", text)
    text = _BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = text.replace("\u200b", "")
    return _WS_RE.sub(" ", text).strip()


def truncate(text: str | None, limit: int = config.MAX_SUMMARY_CHARS) -> str | None:
    """按字符数截断并加省略号。实测源站摘要长度 38~3161 字，必须截断。"""
    if not text:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


#: 摘要末尾的导航符号（`点击查看原文>` 这类），判定样板文案前先剥掉。
_TRAILING_JUNK_RE = re.compile(r"[\s>»＞|·・—\-–:：,，.。!！?？、]+$")
#: 整行模板文案正则，由 `config.SUMMARY_BOILERPLATE_REGEXES` 编译而来。
_BOILERPLATE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in config.SUMMARY_BOILERPLATE_REGEXES
)


def is_boilerplate_summary(text: str | None) -> bool:
    """判定是否为纯导航/样板文案。

    判定的是「整条摘要就是一句免打扰文案」，不做全文子串搜索 ——
    否则一篇正常提到 `read more` 的摘要会被误杀。
    """
    stripped = _TRAILING_JUNK_RE.sub("", (text or "").strip())
    if not stripped:
        return True
    lowered = stripped.lower()

    for pattern in _BOILERPLATE_PATTERNS:
        if pattern.match(lowered):
            return True

    for phrase in config.SUMMARY_BOILERPLATE:
        if lowered == phrase:
            return True
        # 命中样板词且剩余内容不足 8 字符，视为同一类导航文案的变体。
        if lowered.startswith(phrase) and len(lowered) - len(phrase) < 8:
            return True
    return False


def clean_summary(raw: str | None) -> str | None:
    """把 feed 摘要清洗成可展示文本，无信息量时返回 `None`。

    返回 `None` 的三种情况：原文为空、是纯导航样板、短于 `SUMMARY_MIN_CHARS`。
    前端据此不渲染摘要区 —— 这比渲染一句「点击查看原文>」诚实也更好看。
    """
    text = truncate(html_to_text(raw))
    if not text:
        return None
    if is_boilerplate_summary(text):
        return None
    if len(text) < config.SUMMARY_MIN_CHARS:
        return None
    return text


def detect_lang(text: str | None) -> str:
    """按中日韩字符与拉丁字母的比例判定语言，用于按语言筛选。"""
    if not text:
        return "other"
    cjk = len(_CJK_RE.findall(text))
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if cjk == 0 and latin == 0:
        return "other"
    if cjk > 0 and cjk * 3 >= latin:
        return "zh"
    if latin > 0:
        return "en"
    return "other"


def squeeze(text: str | None) -> str:
    """合并空白并去首尾，用于标题清洗（标题中的换行会破坏列表排版）。"""
    if not text:
        return ""
    return _WS_RE.sub(" ", text).strip()


# --- 配图 -------------------------------------------------------------------

#: 先把正文切成一个个 `<img ...>` 标签，再在标签内部按属性优先级取地址。
#: 不用「一条正则直接抓地址」的写法：懒加载标签里 `src` 通常是占位图（骨架图 / 1x1 像素），
#: 真图在 `data-original` / `data-src`；而按出现位置匹配的正则会先命中排在前面的 `src`。
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
#: 标签内的候选属性。`(?<![\w-])` 防止 `data-src=` 尾部的 `src=` 被当成第二个属性重复捕获。
#: 刻意**不取 `srcset`** —— 它的值是一组「地址 + 宽度」候选，正则只能拿到最低分辨率那一档。
_IMG_ATTR_RE = re.compile(
    r"(?<![\w-])(data-original|data-src|data-lazy-src|src)\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))",
    re.I,
)
#: 属性取值顺序：懒加载的 `data-*` 才是真图，`src` 只做兜底。
_IMG_ATTR_PRIORITY = ("data-original", "data-src", "data-lazy-src", "src")
#: 协议相对地址（`//cdn.example.com/a.jpg`）。实测中文源 feed 里很常见，需补上 https 才能用。
_PROTOCOL_RELATIVE_RE = re.compile(r"^//[^/]")


def clean_image_url(raw: str | None) -> str | None:
    """校验并规范化一个配图 URL，不合格返回 `None`。

    只接受 `http` / `https`，用**白名单**而不是黑名单：源站内容不可信，而 `data:` 会把图片
    内联进 base64（一张图就能把单文件产物撑大几百 KB），`javascript:` 则是直接的注入向量。
    超长 URL 一并丢弃 —— 少数源内嵌带签名的 CDN 地址，既撑大产物又往往几分钟后失效。
    """
    if not raw:
        return None
    url = html.unescape(raw).strip().strip("\"'")
    if not url:
        return None
    if _PROTOCOL_RELATIVE_RE.match(url):
        url = "https:" + url
    if len(url) > config.IMAGE_URL_MAX_CHARS:
        return None
    if not url.lower().startswith(
        tuple(f"{scheme}://" for scheme in config.IMAGE_ALLOWED_SCHEMES)
    ):
        return None
    return url


def is_plausible_image_url(url: str) -> bool:
    """排除头像、表情、埋点像素等「是图片但不是正文配图」的地址。"""
    lowered = url.lower()
    return not any(pattern in lowered for pattern in config.IMAGE_URL_DENY_PATTERNS)


def _tag_image_urls(tag: str) -> list[str]:
    """按属性优先级返回单个 `<img>` 标签里的候选地址。"""
    attributes: dict[str, str] = {}
    for match in _IMG_ATTR_RE.finditer(tag):
        name = match.group(1).lower()
        value = next((group for group in match.groups()[1:] if group), None)
        if value and name not in attributes:
            attributes[name] = value
    return [attributes[name] for name in _IMG_ATTR_PRIORITY if name in attributes]


def find_first_image(fragment: str | None) -> str | None:
    """从 HTML 片段里取第一张像样的配图 URL，取不到返回 `None`。

    实测一个中文源条目的正文常有十几张 `<img>`（头像、二维码、相关阅读缩略图），
    所以不能直接取第一张 —— 必须按序扫描、跳过命中拒绝名单的，并限制扫描数量，
    免得为了一张配图把整个长 HTML 扫完。

    同一标签内先看 `data-original` / `data-src` 再看 `src`：懒加载写法下 `src`
    往往是占位图，直接采信会得到一张骨架图或 1x1 像素。
    """
    if not fragment:
        return None
    text = html.unescape(fragment)
    for index, tag in enumerate(_IMG_TAG_RE.finditer(text)):
        if index >= config.IMAGE_SCAN_MAX_TAGS:
            break
        for raw_url in _tag_image_urls(tag.group(0)):
            candidate = clean_image_url(raw_url)
            if candidate and is_plausible_image_url(candidate):
                return candidate
    return None


# --- 时间解析 ---------------------------------------------------------------

_EPOCH_RE = re.compile(r"^\d{10,13}$")


def parse_published(
    raw: str | None,
    now: datetime,
    *,
    offset_override_minutes: int | None = None,
) -> tuple[datetime | None, str | None]:
    """把来源声称的发布时间解析为 UTC 时间。

    返回 `(utc_datetime | None, note | None)`。`note` 非空表示时间不可完全采信，
    取值见 `models.PublishNote`。

    处理顺序对应实测到的边界：
    1. 缺字段 → `missing`
    2. 纯数字 → 按 epoch 秒/毫秒
    3. 配置了 `offset_override_minutes` → 保留钟面时间，改用该偏移重新解释并标记 `tz_corrected`
    4. 无时区信息 → 按 UTC 处理但标记 `tz_assumed`（不假装知道真实时区）
    5. 早于 `MIN_PUBLISH_YEAR` 或晚于当前时间 `FUTURE_TOLERANCE_HOURS` 小时 → `implausible` / `future_date`
    6. 解析失败 → `unparseable`

    `offset_override_minutes` 必须早于「未来时间」判定，否则整源偏移的条目会先被当成
    未来时间丢弃，更正逻辑永远走不到。
    """
    if raw is None:
        return None, "missing"
    text = raw.strip()
    if not text:
        return None, "missing"

    note: str | None = None
    parsed: datetime | None = None

    if _EPOCH_RE.match(text):
        seconds = int(text) / (1000 if len(text) == 13 else 1)
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None, "unparseable"
    else:
        try:
            parsed = dateutil_parser.parse(text)
        except (ValueError, OverflowError, TypeError):
            return None, "unparseable"

    if parsed is None:
        return None, "unparseable"

    if offset_override_minutes is not None:
        # 源站时区标注不可信：保留钟面时间，改用配置的偏移重新解释。
        parsed = parsed.replace(
            tzinfo=timezone(timedelta(minutes=offset_override_minutes))
        ).astimezone(timezone.utc)
        note = "tz_corrected"
    elif parsed.tzinfo is None:
        # 源站未给出时区。按 UTC 处理是最不易出错的选择，但必须让用户知道。
        parsed = parsed.replace(tzinfo=timezone.utc)
        note = "tz_assumed"
    else:
        parsed = parsed.astimezone(timezone.utc)

    if parsed.year < config.MIN_PUBLISH_YEAR:
        return None, "implausible"
    if parsed > now + timedelta(hours=config.FUTURE_TOLERANCE_HOURS):
        return None, "future_date"

    return parsed, note


def utc_now() -> datetime:
    """当前 UTC 时间，统一入口便于测试注入。"""
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None) -> str | None:
    """UTC 时间转 ISO8601 字符串，毫秒精度。"""
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def normalize_for_compare(text: str) -> str:
    """NFKC 归一 + 小写 + 去空白，用于拼音/全半角差异下的文本比对。"""
    return _WS_RE.sub("", unicodedata.normalize("NFKC", text).lower())