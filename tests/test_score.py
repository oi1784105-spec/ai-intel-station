"""打分与「今日重点」入选规则测试。

设计约定：**入选是规则，排序才是分数**。每条入选理由都必须能原文复述给用户，
因此这里逐条验证 4 条入选规则与它们的排序行为。
"""

from __future__ import annotations

from datetime import timedelta

from pipeline import config, score
from pipeline.cluster import Cluster

from helpers import NOW, hours_ago, make_article


def _cluster_of(articles, cluster_id: str = "c1") -> Cluster:
    """为若干条目构造一个簇，并回填 `cluster_id`。"""
    member = Cluster(
        id=cluster_id,
        article_ids=[article.id for article in articles],
        source_ids={article.source_id for article in articles},
    )
    for article in articles:
        article.cluster_id = cluster_id
    return member


# --------------------------------------------------------------------------- #
# 入选规则
# --------------------------------------------------------------------------- #

def test_highlight_by_independent_sources() -> None:
    zh = make_article("某事件中文报道", source_id="infoq-ai")
    en = make_article("某事件 English report", source_id="hn")
    found = _cluster_of([zh, en])
    breakdown = score.evaluate(zh, found, NOW)
    assert score._is_highlight(zh, breakdown) is not None
    assert "2 个独立来源" in score._is_highlight(zh, breakdown)


def test_highlight_by_community_heat() -> None:
    hot = make_article("社区热议的技术话题", source_id="hn", heat=200, comments=80)
    breakdown = score.evaluate(hot, None, NOW)
    reason = score._is_highlight(hot, breakdown)
    assert reason is not None and "热度" in reason


def test_highlight_by_multiple_signals() -> None:
    """命中两个及以上信号类别：发布/上线 + 基准/榜单。"""
    title = "某模型正式发布并登顶国际评测基准榜单"
    article = make_article(title, source_id="qbitai")
    breakdown = score.evaluate(article, None, NOW)
    reason = score._is_highlight(article, breakdown)
    assert breakdown.signal_count >= 2
    assert reason is not None and "信号" in reason


def test_highlight_by_official_source_with_single_signal() -> None:
    """官方源 + 单个发布类信号即入选（官方一手信息的价值高于转载）。"""
    article = make_article(
        "OpenAI introduces its new reasoning model", source_id="openai", source_official=True,
    )
    breakdown = score.evaluate(article, None, NOW)
    assert breakdown.signal_count == 1
    reason = score._is_highlight(article, breakdown)
    assert reason is not None and "官方来源" in reason


def test_plain_recent_article_is_not_a_highlight() -> None:
    """普通新增条目不该出现在「今日重点」——否则重点区等于第二条列表。"""
    article = make_article("一次普通的行业观察记录", source_id="solidot")
    breakdown = score.evaluate(article, None, NOW)
    assert score._is_highlight(article, breakdown) is None


# --------------------------------------------------------------------------- #
# 排序与明细
# --------------------------------------------------------------------------- #

def test_recency_decays_with_age() -> None:
    fresh = make_article("同一事件的报道", source_id="solidot", published_at=hours_ago(1))
    old = make_article("同一事件的报道", source_id="solidot", published_at=hours_ago(48))
    assert score.evaluate(fresh, None, NOW).score > score.evaluate(old, None, NOW).score


def test_recency_contribution_stops_at_horizon() -> None:
    beyond = make_article(
        "很旧的条目", source_id="solidot",
        published_at=NOW - timedelta(hours=config.RECENCY_HORIZON_HOURS + 5),
    )
    breakdown = score.evaluate(beyond, None, NOW)
    baseline = score.evaluate(
        make_article("很旧的条目二", source_id="solidot",
                     published_at=NOW - timedelta(hours=config.RECENCY_HORIZON_HOURS + 9)),
        None, NOW,
    )
    assert breakdown.score == baseline.score


def test_cross_source_credit_requires_a_cluster() -> None:
    """单个来源不该拿到跨源加分，否则「多来源」这个信号就被稀释了。"""
    article = make_article("众人报道的事件", source_id="solidot")
    peers = [
        make_article("众人报道的事件 English report", source_id="hn"),
        make_article("众人报道的事件 third source", source_id="techcrunch-ai"),
    ]
    found = _cluster_of([article, *peers])

    solo = score.evaluate(article, None, NOW)
    grouped = score.evaluate(article, found, NOW)

    assert solo.independent_sources == 1
    assert grouped.independent_sources == 3
    assert grouped.score > solo.score


def test_heat_contribution_is_capped() -> None:
    """热度归一上限 1000：再高的 HN 分数也不该无限拉大差距。"""
    low = make_article("话题一", source_id="hn", heat=1000)
    high = make_article("话题二", source_id="hn", heat=5000)
    assert abs(score.evaluate(low, None, NOW).score
               - score.evaluate(high, None, NOW).score) < 1e-9


def test_reasons_are_human_readable() -> None:
    article = make_article(
        "OpenAI 发布 GPT-6 并登顶评测榜单", source_id="openai", source_official=True,
    )
    breakdown = score.evaluate(article, None, hours_ago(1))
    assert any("官方一手来源" in reason for reason in breakdown.reasons)
    assert any("涉及" in reason for reason in breakdown.reasons)
    assert any(reason.endswith("前") or reason == "刚刚" for reason in breakdown.reasons)


def test_highlight_reasons_do_not_repeat_the_same_dimension() -> None:
    """入选理由已说明信号维度时，卡片上不该再出现一遍同义文案。"""
    title = "某模型正式发布并登顶国际评测基准榜单"
    article = make_article(title, source_id="qbitai")
    clusters: dict[str, Cluster] = {}
    highlights = score.select_highlights([article], clusters, NOW)
    assert len(highlights) == 1
    signal_reasons = [r for r in highlights[0].reasons if "信号" in r or r.startswith("涉及")]
    assert len(signal_reasons) == 1


# --------------------------------------------------------------------------- #
# 挑选行为
# --------------------------------------------------------------------------- #

def test_select_highlights_ignores_unqualified_articles() -> None:
    plain = make_article("一次普通的行业观察记录", source_id="solidot")
    assert score.select_highlights([plain], {}, NOW) == []


def test_select_highlights_takes_one_article_per_cluster() -> None:
    """同一事件只上一条重点，避免重点区被单事件占满。"""
    zh = make_article("某事件中文报道", source_id="infoq-ai")
    en = make_article("某事件 English report", source_id="hn")
    found = _cluster_of([zh, en])
    highlights = score.select_highlights([zh, en], {found.id: found}, NOW)

    assert len(highlights) == 1
    assert highlights[0].article_id in {zh.id, en.id}
    assert highlights[0].independent_sources == 2


def test_select_highlights_respects_limit() -> None:
    articles = [
        make_article(f"独家报道 {index}", source_id="hn", heat=300 + index)
        for index in range(config.HIGHLIGHT_MAX_ITEMS + 4)
    ]
    highlights = score.select_highlights(articles, {}, NOW, limit=3)
    assert len(highlights) == 3


def test_select_highlights_sorted_by_score_descending() -> None:
    articles = [
        make_article("热度较低的讨论", source_id="hn", heat=160),
        make_article("热度极高的讨论", source_id="hn", heat=900),
    ]
    highlights = score.select_highlights(articles, {}, NOW)
    assert len(highlights) == 2
    assert highlights[0].score >= highlights[1].score


# --------------------------------------------------------------------------- #
# 相对时间文案
# --------------------------------------------------------------------------- #

def test_relative_time_zh() -> None:
    cases = [
        (NOW + timedelta(minutes=5), "刚刚"),
        (NOW - timedelta(minutes=30), "30 分钟前"),
        (NOW - timedelta(hours=5), "5 小时前"),
        (NOW - timedelta(days=3), "3 天前"),
        (NOW - timedelta(days=95), "3 个月前"),
    ]
    for moment, expected in cases:
        assert score.relative_time_zh(moment, NOW) == expected


def test_highlight_reasons_exclude_bare_timestamp() -> None:
    """**回归测试（截图核对时发现的）**：重点理由里不能只剩下一句时间。

    条目级 `reasons` 为了解释时效分项会带上「1 天前」，它经 `_card_reasons`
    流进重点条目后，与卡片上单独渲染的相对时间重复，页面上会出现两个时间。
    这里断言重点理由里不含任何一条「就是个时间」的项。
    """
    article = make_article(
        "官方发布新模型", source_id="openai", published_at=NOW - timedelta(days=1),
        # `make_article` 不会从源清单推导这个字段，要显式给，
        # 否则命不中「官方来源，涉及…」这条入选规则。
        source_official=True,
    )
    highlights = score.select_highlights([article], {}, NOW)
    assert highlights, "构造的条目应当入选重点"

    stamp = score.relative_time_zh(NOW - timedelta(days=1), NOW)
    assert stamp not in highlights[0].reasons, (
        f"重点理由里仍带着时间「{stamp}」：{highlights[0].reasons}"
    )
    # 时间仍应能在条目级 reasons 里查到 —— 那是打分透明度的解释，不该被删掉。
    breakdown = score.evaluate(article, None, NOW)
    assert stamp in breakdown.reasons