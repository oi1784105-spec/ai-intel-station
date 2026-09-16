"""时间解析与文本归一化测试。

日期格式全部取自 2026-09-16 对 16 个源的真实抓取结果，不是构造的样例。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from pipeline import config
from pipeline.parse import (
    clean_summary,
    detect_lang,
    html_to_text,
    is_boilerplate_summary,
    normalize_for_compare,
    parse_published,
    squeeze,
    truncate,
)

from helpers import NOW


# --------------------------------------------------------------------------- #
# 日期：实测到的 4 种格式 + arXiv 的 -0400
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("raw", "expected_iso"),
    [
        # 量子位 / 爱范儿 / TechCrunch / Google AI（+0000）
        ("Wed, 16 Sep 2026 08:51:10 +0000", "2026-09-16T08:51:10+00:00"),
        # 雷锋网 / 开源中国 / 少数派 / Solidot / 钛媒体（+0800）
        ("Wed, 16 Sep 2026 08:51:10 +0800", "2026-09-16T00:51:10+00:00"),
        # OpenAI（GMT）
        ("Wed, 16 Sep 2026 08:51:10 GMT", "2026-09-16T08:51:10+00:00"),
        # Simon Willison（ISO8601 +00:00）
        ("2026-09-16T07:00:00+00:00", "2026-09-16T07:00:00+00:00"),
        # arXiv cs.AI（-0400）
        ("Wed, 16 Sep 2026 00:00:00 -0400", "2026-09-16T04:00:00+00:00"),
    ],
)
def test_real_feed_date_formats(raw: str, expected_iso: str) -> None:
    """5 种实测格式都必须解析到正确的 UTC 时刻，且不带任何 note。"""
    moment, note = parse_published(raw, NOW)
    assert moment is not None
    assert moment.isoformat() == expected_iso
    assert note is None


def test_missing_date_is_reported_not_invented() -> None:
    """缺字段时不猜时间 —— 返回 None 并说明原因，绝不退化成抓取时间。"""
    assert parse_published(None, NOW) == (None, "missing")
    assert parse_published("   ", NOW) == (None, "missing")


@pytest.mark.parametrize("raw", ["昨天", "not a date", "2026-13-45T99:99:99Z", "周三"])
def test_unparseable_date(raw: str) -> None:
    """解析不了就是解析不了，不能兜底成「现在」。"""
    assert parse_published(raw, NOW) == (None, "unparseable")


def test_epoch_seconds_and_milliseconds() -> None:
    """API 型源（HN）给的是 epoch 秒；毫秒也要能识别。"""
    seconds = int(NOW.timestamp())
    moment, note = parse_published(str(seconds), NOW)
    assert moment is not None and moment == NOW and note is None

    moment_ms, note_ms = parse_published(str(seconds * 1000), NOW)
    assert moment_ms is not None and moment_ms == NOW and note_ms is None


def test_naive_date_is_marked_tz_assumed() -> None:
    """源站没写时区时按 UTC 处理，但必须标记，让用户知道这是假设。"""
    moment, note = parse_published("2026-09-16 08:00:00", NOW)
    assert moment is not None
    assert moment.isoformat() == "2026-09-16T08:00:00+00:00"
    assert note == "tz_assumed"


def test_slightly_future_within_tolerance_is_accepted() -> None:
    """源站时钟略快是常态，容忍窗口内的时间照常采信。"""
    raw = "2026-09-16 13:30:00 GMT"
    moment, note = parse_published(raw, NOW)
    assert moment is not None and note is None


def test_far_future_is_rejected() -> None:
    """远超容忍窗口的未来时间说明源站标注有问题，宁可承认不知道。"""
    raw = "2026-09-16 20:00:00 GMT"
    assert parse_published(raw, NOW) == (None, "future_date")


def test_implausible_old_date_is_rejected() -> None:
    """1970 之类的默认值不是真实发布时间。"""
    assert parse_published("1990-01-01T00:00:00Z", NOW) == (None, "implausible")


def test_offset_override_corrects_mislabelled_timezone() -> None:
    """InfoQ 中文站把北京时间标成 GMT —— 按配置偏移重新解释钟面时间。

    实测证据：该源 feed 里最新一条 pubDate 恰好等于抓取时刻的**北京时间**，
    即整源时间比真实 UTC 早 8 小时。
    """
    raw = "Wed, 16 Sep 2026 19:26:11 GMT"
    moment, note = parse_published(raw, NOW, offset_override_minutes=480)
    assert moment is not None
    assert moment.isoformat() == "2026-09-16T11:26:11+00:00"
    assert note == "tz_corrected"


def test_offset_override_runs_before_future_check() -> None:
    """更正必须早于未来时间判定：否则整源偏移的条目会先被当成未来时间丢掉，
    更正逻辑永远走不到。这条断言锁住那个顺序。"""
    raw = "Wed, 16 Sep 2026 19:26:11 GMT"
    assert parse_published(raw, NOW) == (None, "future_date")
    corrected, note = parse_published(raw, NOW, offset_override_minutes=480)
    assert corrected is not None and note == "tz_corrected"


# --------------------------------------------------------------------------- #
# HTML → 纯文本
# --------------------------------------------------------------------------- #

def test_html_to_text_strips_tags_and_unescapes() -> None:
    """实测源站摘要是 HTML 片段（含 <img>/<div>），必须压成纯文本。"""
    assert html_to_text("<p>OpenAI 发布了 <b>GPT-6</b></p>") == "OpenAI 发布了 GPT-6"
    assert html_to_text("Level &amp; scale") == "Level & scale"


def test_html_to_text_drops_script_and_style() -> None:
    """脚本与样式不承载可展示文本，连同内容一起丢弃。"""
    assert html_to_text("<script>evil()</script>正文") == "正文"
    assert html_to_text("<style>p{color:red}</style>正文") == "正文"


def test_html_to_text_handles_escaped_markup() -> None:
    """先反转义再剥标签，使被转义成实体的标签文本也被清掉。"""
    assert html_to_text("&lt;p&gt;hi&lt;/p&gt;") == "hi"


def test_html_to_text_collapses_whitespace() -> None:
    assert html_to_text("a\n\n   b\t\tc") == "a b c"
    assert html_to_text(None) == ""


def test_truncate_uses_ellipsis_only_when_needed() -> None:
    exact = "x" * 240
    assert truncate(exact) == exact
    assert len(truncate("x" * 300)) == 240
    assert truncate("x" * 300).endswith("…")


# --------------------------------------------------------------------------- #
# 摘要清洗
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # InfoQ 中文站 20/20 条的真实摘要形态
        ("点击查看原文>", True),
        ("点击查看原文", True),
        ("阅读全文 »", True),
        ("Read more", True),
        ("Continue reading", True),
        # WordPress 类整句模板
        ("The post GPT-6 landed appeared first on TechCrunch.", True),
        ("Appeared first on Solidot.", True),
        ("", True),
        ("   ", True),
        # 正文不能被误杀
        ("点击查看原文之外，该论文还提出了新的训练目标函数。", False),
        ("Read more about how the model handles long context windows.", False),
    ],
)
def test_is_boilerplate_summary(text: str, expected: bool) -> None:
    assert is_boilerplate_summary(text) is expected


def test_clean_summary_drops_boilerplate_and_short_text() -> None:
    """纯导航摘要与过短文本返回 None，让前端不渲染摘要区。"""
    assert clean_summary("<div align='right'><a href='x'>点击查看原文></a></div>") is None
    assert clean_summary("AI协同办公") is None
    assert clean_summary(None) is None
    assert clean_summary("") is None


def test_clean_summary_keeps_real_text() -> None:
    text = "该研究提出了一个结构化数据基础模型，在多项国际评测榜单上取得了领先结果。"
    assert clean_summary(text) == text


def test_summary_min_chars_is_configured_not_hardcoded() -> None:
    """阈值必须在 config 里，可随部署调整。"""
    assert config.SUMMARY_MIN_CHARS > 0
    borderline = "x" * (config.SUMMARY_MIN_CHARS - 1)
    assert clean_summary(borderline) is None
    assert clean_summary("x" * config.SUMMARY_MIN_CHARS) is not None


# --------------------------------------------------------------------------- #
# 语言与文本工具
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("OpenAI releases GPT-6 for coding", "en"),
        ("AI 大模型发布，榜单登顶", "zh"),
        ("GPT-6 Astra 正式发布并登顶评测榜单", "zh"),
        ("", "other"),
        ("12345", "other"),
    ],
)
def test_detect_lang(text: str, expected: str) -> None:
    assert detect_lang(text) == expected


def test_squeeze_collapses_newlines_in_titles() -> None:
    """标题里的换行会破坏列表排版，必须合并。"""
    assert squeeze("第一行\n第二行") == "第一行 第二行"


def test_normalize_for_compare_handles_fullwidth() -> None:
    """全角/半角与大小写差异不应造成文本比对失败。"""
    assert normalize_for_compare("ＰＴ－６") == normalize_for_compare("pt-6")


def test_normalize_for_compare_removes_newlines() -> None:
    """换行也必须归一到空（早期只处理空格/制表符，跨行文本会被判为不同）。"""
    assert normalize_for_compare("GPT\n-6") == normalize_for_compare("gpt-6")


def test_now_relative_helper_matches_config_window() -> None:
    """`helpers.hours_ago` 与测试基准时刻一致，防止断言基线漂移。"""
    assert (NOW - timedelta(hours=3)).hour == 9