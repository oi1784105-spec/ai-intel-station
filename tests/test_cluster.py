"""事件聚合测试。

含一个真实缺陷的回归用例：4 篇互不相关的 arXiv 论文曾因共享
`Adaptive` / `An` / `Agents` / `LLM` 等通用词被并成一簇，进而虚增「跨源命中度」，
让它们凭虚假的「多来源报道」挤进「今日重点」。
"""

from __future__ import annotations

from datetime import timedelta

from pipeline import cluster, config

from helpers import NOW, hours_ago, make_article

GPT6_ZH = "OpenAI 发布适用于编程和计算机应用的 GPT-6 Astra"
GPT6_EN = "GPT-5.6 Luna vs. GPT-6 Astra: Is a $1.20 Model Good Enough for Code Review?"
GPT6_OFFICIAL = "Perplexity trusts GPT-6 Astra with end-to-end systems"
MISTRAL_ZH = "Mistral 与 Mozilla 合作推出 Firefox Smart Window"
MISTRAL_EN = "Mistral X Mozilla: Private, Multilingual AI Browsing"

ARXIV_TITLES = (
    "Asclepius: An Adaptive Harness for Long-Horizon Clinical Agents",
    "Governing at Machine Speed: An Adaptive Intelligence Architecture "
    "for Real-Time AI Policy Enforcement",
    "Vibe Patenting: Evaluating LLM Judges for Professional Patent-Drafting Agents",
    "Toward Self-Adaptive Physical AI: Can LLM Agents Manage Long-Horizon Physical Tasks?",
)


def _alternating_sources(titles: tuple[str, ...]):
    """给相邻标题分配不同来源。

    「同源不构成跨源事件」这条规则会掩盖实体判定逻辑 —— 如果全部同源，
    测试即使通过也证明不了实体过滤生效。交替来源可以隔离出真正要测的那一层。
    """
    source_ids = ("arxiv", "techcrunch-ai")
    return [
        make_article(title, source_id=source_ids[index % len(source_ids)])
        for index, title in enumerate(titles)
    ]


# --------------------------------------------------------------------------- #
# 应当聚合
# --------------------------------------------------------------------------- #

def test_cross_language_event_is_clustered() -> None:
    """中文源 + 英文媒体 + 官方源报道同一事件 → 一个簇、三个独立来源。"""
    articles = [
        make_article(GPT6_ZH, source_id="infoq-ai"),
        make_article(GPT6_EN, source_id="hn"),
        make_article(GPT6_OFFICIAL, source_id="openai"),
    ]
    clusters = cluster.build_clusters(articles, NOW)

    assert len(clusters) == 1
    found = next(iter(clusters.values()))
    assert found.independent_sources == 3
    assert found.size == 3


def test_two_source_event_is_clustered() -> None:
    articles = [
        make_article(MISTRAL_ZH, source_id="solidot"),
        make_article(MISTRAL_EN, source_id="hn"),
    ]
    clusters = cluster.build_clusters(articles, NOW)
    assert len(clusters) == 1
    assert next(iter(clusters.values())).independent_sources == 2


# --------------------------------------------------------------------------- #
# 不应聚合
# --------------------------------------------------------------------------- #

def test_same_source_is_not_a_cross_source_event() -> None:
    """同一来源的连续报道（如系列推广帖）不是「多来源报道」。"""
    articles = [
        make_article(MISTRAL_ZH, source_id="solidot"),
        make_article(MISTRAL_EN, source_id="solidot"),
    ]
    assert cluster.build_clusters(articles, NOW) == {}


def test_unrelated_articles_stay_isolated() -> None:
    articles = [
        make_article(GPT6_ZH, source_id="infoq-ai"),
        make_article(MISTRAL_ZH, source_id="solidot"),
    ]
    assert cluster.build_clusters(articles, NOW) == {}


def test_regression_arxiv_titles_are_not_clustered() -> None:
    """回归：这 4 篇论文曾被并成一簇。来源已交替分配，因此这里检验的纯粹是
    实体稀有度过滤与停用词表是否生效。"""
    articles = _alternating_sources(ARXIV_TITLES)
    assert cluster.build_clusters(articles, NOW) == {}


def test_articles_outside_time_window_are_not_clustered() -> None:
    """跨源报道是当日事件，不该与 4 天前的条目比较。"""
    outside = NOW - timedelta(hours=config.CLUSTER_WINDOW_HOURS + 1)
    articles = [
        make_article(MISTRAL_ZH, source_id="solidot", published_at=outside),
        make_article(MISTRAL_EN, source_id="hn", published_at=outside),
    ]
    assert cluster.build_clusters(articles, NOW) == {}


def test_single_article_produces_no_cluster() -> None:
    assert cluster.build_clusters([make_article(GPT6_ZH)], NOW) == {}


def test_empty_input_is_safe() -> None:
    assert cluster.build_clusters([], NOW) == {}


# --------------------------------------------------------------------------- #
# 簇元信息
# --------------------------------------------------------------------------- #

def test_cluster_index_covers_every_member() -> None:
    articles = [
        make_article(GPT6_ZH, source_id="infoq-ai"),
        make_article(GPT6_EN, source_id="hn"),
        make_article(GPT6_OFFICIAL, source_id="openai"),
    ]
    clusters = cluster.build_clusters(articles, NOW)
    index = cluster.cluster_index(clusters)

    assert set(index) == {article.id for article in articles}
    assert len(set(index.values())) == 1


def test_cluster_id_is_stable_and_is_smallest_member_id() -> None:
    """簇 id 取成员中字典序最小的条目 id，保证跨运行稳定（不能是随机或计数值）。"""
    articles = [
        make_article(GPT6_ZH, source_id="infoq-ai"),
        make_article(GPT6_EN, source_id="hn"),
    ]
    forward = cluster.build_clusters(articles, NOW)
    backward = cluster.build_clusters(list(reversed(articles)), NOW)

    assert set(forward) == set(backward)
    cluster_id = next(iter(forward))
    assert cluster_id == min(article.id for article in articles)


def test_cluster_records_a_human_readable_reason() -> None:
    articles = [
        make_article(MISTRAL_ZH, source_id="solidot"),
        make_article(MISTRAL_EN, source_id="hn"),
    ]
    found = next(iter(cluster.build_clusters(articles, NOW).values()))
    assert found.reasons
    assert "共同实体" in found.reasons[0] or "相似度" in found.reasons[0]


def test_entity_rarity_threshold_is_configured() -> None:
    """稀有度上限必须在 config 里，可随数据规模调整。"""
    assert config.CLUSTER_DISTINCTIVE_ENTITY_MAX_DF >= 2
    assert config.CLUSTER_MIN_SHARED_ENTITIES >= 2