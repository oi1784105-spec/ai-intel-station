"""源注册表与配置健全性测试。

配置写错属于「静默错误」：源少了一个不会报错，只会让情报面变窄；
权重写成 0 不会报错，只会让打分退化。因此这些不变量必须机械校验。
"""

from __future__ import annotations

import pytest

from pipeline import config


def test_source_ids_are_unique() -> None:
    ids = [source.id for source in config.ALL_SOURCES]
    assert len(set(ids)) == len(ids)


def test_source_ids_are_ascii_slugs() -> None:
    """id 会进 CLI 参数与文件名，必须是 ASCII 短横线形式。"""
    for source in config.ALL_SOURCES:
        assert source.id.isascii(), source.id
        assert source.id == source.id.lower(), source.id
        assert " " not in source.id, source.id


def test_production_urls_are_https() -> None:
    for source in config.SOURCES:
        assert source.url.startswith("https://"), f"{source.id} 使用了非 https 地址"


def test_source_urls_are_unique() -> None:
    urls = [source.url for source in config.ALL_SOURCES]
    assert len(set(urls)) == len(urls)


def test_every_source_declares_language_and_kind() -> None:
    for source in config.ALL_SOURCES:
        assert source.lang in {"zh", "en"}, source.id
        assert source.kind in {"rss", "atom", "hn", "arxiv"}, source.id


def test_registry_has_both_language_groups() -> None:
    """《每日 AI 情报站》面向中文读者，中文源不能少于英文源太多。"""
    zh = [source for source in config.SOURCES if source.lang == "zh"]
    en = [source for source in config.SOURCES if source.lang == "en"]
    assert len(zh) >= 5, f"中文源过少：{len(zh)}"
    assert len(en) >= 5, f"英文源过少：{len(en)}"


def test_weights_are_positive_and_bounded() -> None:
    for source in config.ALL_SOURCES:
        assert 0 < source.weight <= 2.0, f"{source.id} 权重越界：{source.weight}"


def test_official_sources_are_the_expected_ones() -> None:
    official = {source.id for source in config.SOURCES if source.official}
    assert official == {"openai", "google-ai"}


def test_general_tech_sources_require_relevance_filter() -> None:
    """通用科技源（非 AI 垂类）必须开相关性过滤，否则会混进消费电子新闻。"""
    expected = {"leiphone", "oschina", "ifanr", "sspai", "solidot", "tmtpost", "simonw"}
    flagged = {source.id for source in config.SOURCES if source.relevance_filter}
    assert flagged == expected


def test_infoq_carries_timezone_correction() -> None:
    """实测 InfoQ 中文站把北京时间标注成 GMT，整源时间偏早 8 小时。"""
    source = config.SOURCES_BY_ID["infoq-ai"]
    assert source.declared_utc_offset_minutes == 480


def test_no_other_source_declares_a_timezone_correction() -> None:
    """时区更正是针对已实测确认的缺陷，不能顺手给别的源加上。"""
    corrected = {
        source.id for source in config.ALL_SOURCES
        if source.declared_utc_offset_minutes is not None
    }
    assert corrected == {"infoq-ai"}


def test_hn_dual_track_parameters_are_complete() -> None:
    """HN 必须走双轨：高信号轨保证质量，新鲜轨保证时效。缺一个都会让信号失衡。"""
    params = config.SOURCES_BY_ID["hn"].params
    for key in ("query", "min_points_high", "fresh_hours", "min_points_fresh", "page_size"):
        assert key in params, f"HN 缺少参数 {key}"
    assert params["min_points_high"] > params["min_points_fresh"]
    assert params["fresh_hours"] > 0


def test_arxiv_declares_an_item_cap() -> None:
    """实测 arXiv 单次响应 634 条 / 1.34MB，RSS 无 max_results，必须自行限流。"""
    source = config.SOURCES_BY_ID["arxiv"]
    assert source.max_items is not None and source.max_items <= 50
    assert source.params.get("announce_types")


def test_openai_feed_declares_an_item_cap() -> None:
    """实测 OpenAI 单 feed 1193 条 / 725KB，不裁剪会一天灌满存储。"""
    source = config.SOURCES_BY_ID["openai"]
    assert source.max_items is not None and source.max_items <= 50


def test_archived_source_is_registered_but_disabled() -> None:
    """留档源保留配置但退出生产采集，使「已知停更」这条经验可追溯、可回归。"""
    archived = config.SOURCES_BY_ID["venturebeat-ai"]
    assert archived.enabled is False
    assert archived.id not in {source.id for source in config.SOURCES}


def test_only_the_archived_source_is_disabled() -> None:
    disabled = {source.id for source in config.ALL_SOURCES if not source.enabled}
    assert disabled == {"venturebeat-ai"}


def test_production_registry_size() -> None:
    """源数量是刻意选择的结果（16 个生产源 + 1 个留档源），改动应是有意识的。"""
    assert len(config.SOURCES) == 16
    assert len(config.ALL_SOURCES) == 17


def test_tracking_params_are_lowercase() -> None:
    """比对时用小写键查找，配置里写了大写就会静默失效。"""
    for param in config.TRACKING_PARAMS:
        assert param == param.lower(), param
    for prefix in config.TRACKING_PARAM_PREFIXES:
        assert prefix == prefix.lower(), prefix


def test_tracking_params_do_not_swallow_meaningful_ids() -> None:
    """剥追踪参数时不能把有语义的 id 一起剥掉，否则不同文章会并成一条。"""
    assert "id" not in config.TRACKING_PARAMS
    assert "p" not in config.TRACKING_PARAMS


@pytest.mark.parametrize(
    ("name", "lower", "upper"),
    [
        ("WINDOW_DAYS", 1, 365),
        ("MAX_STORE_ITEMS", 100, 100_000),
        ("MAX_SUMMARY_CHARS", 40, 2000),
        ("SUMMARY_MIN_CHARS", 1, 200),
        ("STALE_SOURCE_DAYS", 1, 90),
        ("RETAIN_RUNS", 1, 1000),
        ("CLUSTER_WINDOW_HOURS", 1, 720),
        ("RECENCY_HORIZON_HOURS", 1, 720),
    ],
)
def test_numeric_thresholds_are_within_sane_ranges(
    name: str, lower: int, upper: int
) -> None:
    value = getattr(config, name)
    assert lower <= value <= upper, f"{name}={value} 越界 [{lower}, {upper}]"


def test_summary_min_is_below_truncation_limit() -> None:
    assert config.SUMMARY_MIN_CHARS < config.MAX_SUMMARY_CHARS


def test_duplicate_and_cluster_thresholds_are_ordered() -> None:
    """重复判定必须比聚合判定严格得多，否则会把「相似」当成「同一篇」删掉。"""
    assert config.DUP_TITLE_CONTAINMENT > config.CLUSTER_JACCARD
    assert config.CLUSTER_MIN_SHARED_ENTITIES >= 2


def test_highlight_rules_are_usable() -> None:
    assert config.HIGHLIGHT_MIN_INDEPENDENT_SOURCES >= 2
    assert config.HIGHLIGHT_MIN_HEAT > 0
    assert config.HIGHLIGHT_MIN_SIGNALS >= 2
    assert 1 <= config.HIGHLIGHT_MAX_ITEMS <= 20


def test_score_weights_cover_every_component() -> None:
    assert set(config.SCORE_WEIGHTS) == {"source", "cross", "recency", "signal", "heat"}
    assert all(weight > 0 for weight in config.SCORE_WEIGHTS.values())


def test_signal_categories_are_non_empty() -> None:
    for name, terms in config.SIGNAL_TERMS.items():
        assert terms, f"信号类别 {name} 没有词条"


def test_ai_term_lists_are_non_empty_and_lowercase_safe() -> None:
    """拉丁词只用于词边界匹配，大小写不敏感；中文词走子串匹配。"""
    assert config.LATIN_AI_TERMS and config.CJK_AI_TERMS
    for term in config.LATIN_AI_TERMS:
        assert term.strip() == term and term, term


def test_http_settings_are_bounded() -> None:
    assert 1 <= config.HTTP_RETRIES <= 10
    assert 1 <= config.HTTP_TIMEOUT_SECONDS <= 120
    assert config.HTTP_MAX_BYTES >= 1024 * 1024
    assert config.HTTP_BACKOFF_SECONDS > 0
    assert "Mozilla" in config.USER_AGENT or "bot" in config.USER_AGENT.lower()


def test_data_paths_live_under_one_directory() -> None:
    """四个产物必须同目录，否则前端与部署脚本的相对路径假设会失效。"""
    paths = [config.NEWS_PATH, config.RUNS_PATH, config.STATUS_PATH, config.META_PATH]
    assert all(path.startswith(config.DATA_DIR + "/") for path in paths)