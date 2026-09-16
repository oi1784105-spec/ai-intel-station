"""URL 归一化、标题去重与实体抽取测试。

用例大多取自 2026-09-16 对真实源的抓取结果。其中「4 篇互不相关的 arXiv 论文曾被
误并成一簇」是本项目真实发生过、并被修复的缺陷，作为回归用例保留。
"""

from __future__ import annotations

import pytest

from pipeline import config, dedupe

# --------------------------------------------------------------------------- #
# 真实标题（取自当次抓取结果，逐字保留）
# --------------------------------------------------------------------------- #

ARXIV_TITLES = (
    "Asclepius: An Adaptive Harness for Long-Horizon Clinical Agents",
    "Governing at Machine Speed: An Adaptive Intelligence Architecture "
    "for Real-Time AI Policy Enforcement",
    "Vibe Patenting: Evaluating LLM Judges for Professional Patent-Drafting Agents",
    "Toward Self-Adaptive Physical AI: Can LLM Agents Manage Long-Horizon Physical Tasks?",
)

GPT6_ZH = "OpenAI 发布适用于编程和计算机应用的 GPT-6 Astra"
GPT6_EN = "GPT-5.6 Luna vs. GPT-6 Astra: Is a $1.20 Model Good Enough for Code Review?"
GPT6_OFFICIAL = "Perplexity trusts GPT-6 Astra with end-to-end systems"

MISTRAL_ZH = "Mistral 与 Mozilla 合作推出 Firefox Smart Window"
MISTRAL_EN = "Mistral X Mozilla: Private, Multilingual AI Browsing"


# --------------------------------------------------------------------------- #
# URL 归一化
# --------------------------------------------------------------------------- #

def test_canonical_url_strips_tracking_params() -> None:
    """中文源普遍带 `utm_*` / `spm`：不剥离则同一篇文章二次抓取会被判成新条目。"""
    raw = "https://www.infoq.cn/article/5PVJPjNlo2g1XKl90u1F?utm_source=rss&utm_medium=article"
    assert dedupe.canonical_url(raw) == "https://infoq.cn/article/5PVJPjNlo2g1XKl90u1F"


def test_canonical_url_strips_spm_and_weibo_id() -> None:
    raw = "https://www.leiphone.com/news/2026/x.html?spm=a2c4e.123&weibo_id=99"
    assert dedupe.canonical_url(raw) == "https://leiphone.com/news/2026/x.html"


def test_canonical_url_keeps_meaningful_params() -> None:
    """有语义的参数必须保留 —— 剥多了会把两篇不同文章并成一条。"""
    raw = "https://news.ycombinator.com/item?id=41234567"
    assert dedupe.canonical_url(raw) == "https://news.ycombinator.com/item?id=41234567"


def test_canonical_url_normalizes_host_case_www_slash_fragment_scheme() -> None:
    variants = [
        "http://WWW.Example.COM/a/b/",
        "https://example.com/a/b#section",
        "https://example.com:443/a/b",
    ]
    canonical = {dedupe.canonical_url(value) for value in variants}
    assert canonical == {"https://example.com/a/b"}


def test_canonical_url_normalizes_root_path() -> None:
    """站点根的两种写法必须落到同一个 id，否则同一篇文章会被导入两次。"""
    assert dedupe.canonical_url("https://example.com") == "https://example.com/"
    assert dedupe.canonical_url("https://example.com/") == "https://example.com/"


def test_canonical_url_returns_input_when_not_a_url() -> None:
    assert dedupe.canonical_url("") == ""
    assert dedupe.canonical_url("不是链接") == "不是链接"


def test_article_id_is_derived_from_canonical_url() -> None:
    """id 由归一 URL 派生，因此「归一 URL 相同」与「id 相同」是同一件事，
    这是去重第一段的实现基础。"""
    left = dedupe.make_article_id("https://example.com/a")
    assert left == dedupe.make_article_id("https://example.com/a")
    assert left != dedupe.make_article_id("https://example.com/b")
    assert len(left) == 16


# --------------------------------------------------------------------------- #
# 标题归一与重复判定
# --------------------------------------------------------------------------- #

def test_normalize_title_removes_punctuation_and_case() -> None:
    assert dedupe.normalize_title("GPT-6 发布！") == dedupe.normalize_title("gpt6发布")
    assert dedupe.normalize_title("ＡＩ　周报") == dedupe.normalize_title("ai周报")


def test_is_duplicate_on_identical_and_near_identical_titles() -> None:
    assert dedupe.is_duplicate("OpenAI 发布 GPT-6", "OpenAI 发布 GPT-6")[0] is True
    # 源站改写标题（后缀来源名）仍应判重
    assert dedupe.is_duplicate(
        "OpenAI 发布适用于编程的 GPT-6",
        "OpenAI 发布适用于编程的 GPT-6 - 量子位",
    )[0] is True


def test_is_duplicate_rejects_unrelated_titles() -> None:
    assert dedupe.is_duplicate(ARXIV_TITLES[0], ARXIV_TITLES[1])[0] is False
    assert dedupe.is_duplicate(GPT6_ZH, MISTRAL_ZH)[0] is False


def test_is_duplicate_guards_against_length_mismatch() -> None:
    """短标题被长标题包含时，3-gram 交集必然稀疏但比率会虚高，必须用长度护栏挡住。"""
    assert dedupe.is_duplicate("AI", "AI 重塑了整个行业的成本结构、人才结构与竞争格局")[0] is False


def test_duplicate_threshold_comes_from_config() -> None:
    assert 0 < config.DUP_TITLE_CONTAINMENT <= 1
    assert 0 < config.DUP_TITLE_MIN_LENGTH_RATIO <= 1


# --------------------------------------------------------------------------- #
# 实体抽取
# --------------------------------------------------------------------------- #

def test_entities_extract_latin_proper_nouns() -> None:
    found = dedupe.entities(GPT6_ZH)
    assert {"GPT-6", "Astra"} <= found


def test_entities_exclude_function_words() -> None:
    """冠词必须被排除：`A` / `An` / `The` 在标题里必然出现，
    留着会让两篇无关文章轻松凑够「共享实体数」。"""
    found = dedupe.entities("Asclepius: An Adaptive Harness for Long-Horizon Clinical Agents")
    assert "An" not in found
    assert "A" not in found


def test_entities_exclude_generic_technical_nouns() -> None:
    """`Agent` / `LLM` / `Adaptive` 描述类别而非具体对象，是误合并的主要来源。"""
    found = dedupe.entities("Vibe Patenting: Evaluating LLM Judges for Professional Agents")
    assert "LLM" not in found
    assert "Agents" not in found
    assert "Patenting" in found


# --------------------------------------------------------------------------- #
# 事件聚合判定
# --------------------------------------------------------------------------- #

def test_same_event_matches_cross_language_reporting() -> None:
    """中文源与英文源报道同一事件：拉丁模型名是唯一可用的公共信号。"""
    matched, reason = dedupe.same_event(GPT6_ZH, GPT6_EN)
    assert matched is True
    assert "GPT-6" in reason


def test_same_event_matches_official_and_media() -> None:
    assert dedupe.same_event(GPT6_ZH, GPT6_OFFICIAL)[0] is True


def test_same_event_matches_mistral_reports() -> None:
    assert dedupe.same_event(MISTRAL_ZH, MISTRAL_EN)[0] is True


def test_same_event_requires_two_shared_entities() -> None:
    """只共享 1 个实体不足以下结论 —— 单实体重合太容易碰巧。"""
    left = "Gemini 计划把价格降到一半"
    right = "Gemini 计划在明年进入车载场景"
    assert dedupe.same_event(left, right)[0] is False


@pytest.mark.parametrize(("left", "right"), [
    (ARXIV_TITLES[0], ARXIV_TITLES[1]),
    (ARXIV_TITLES[0], ARXIV_TITLES[2]),
    (ARXIV_TITLES[0], ARXIV_TITLES[3]),
    (ARXIV_TITLES[1], ARXIV_TITLES[2]),
    (ARXIV_TITLES[1], ARXIV_TITLES[3]),
    (ARXIV_TITLES[2], ARXIV_TITLES[3]),
])
def test_same_event_regression_arxiv_titles_stay_apart(left: str, right: str) -> None:
    """回归：这 4 篇 arXiv 论文曾因共享 `Adaptive` / `An` / `Agents` / `LLM`
    被并成一簇，进而虚增「跨源命中度」。修复后两两都不得判为同事件。"""
    matched, reason = dedupe.same_event(left, right)
    assert matched is False, f"误判为同事件：{reason}"


def test_shingle_similarity_orders_obviously() -> None:
    same = dedupe.shingle_similarity("OpenAI 发布 GPT-6 Astra", "OpenAI 发布 GPT-6 Astra")
    different = dedupe.shingle_similarity(GPT6_ZH, MISTRAL_ZH)
    assert same == 1.0
    assert different < config.CLUSTER_JACCARD


def test_blocking_grams_are_bounded_and_cover_short_text() -> None:
    assert dedupe.blocking_grams("", 3) == set()
    assert dedupe.blocking_grams("ab", 3) == {"ab"}
    grams = dedupe.blocking_grams("abcd", 3)
    assert grams == {"abc", "bcd"}


def test_jaccard_edge_cases() -> None:
    assert dedupe.jaccard(frozenset(), frozenset({"a"})) == 0.0
    assert dedupe.jaccard(frozenset({"a"}), frozenset({"a"})) == 1.0
    assert dedupe.jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0