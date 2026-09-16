"""存储层测试：幂等合并、滚动窗口、原子写。

「重复导入不应产生重复记录」是本题的硬性验收项，本文件是它的机械证明。
"""

from __future__ import annotations

import copy
import json
from datetime import timedelta

from pipeline import store
from pipeline.models import NewsStore

from helpers import NOW, make_article


def _write(tmp_path, articles, name: str = "news.json") -> str:
    """把条目写成 Data 文件，返回文件内容文本。"""
    path = tmp_path / name
    payload = store.dump_store(
        NewsStore(generated_at=NOW, window_days=30, articles=articles)
    )
    store.write_json_atomic(path, payload)
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 幂等
# --------------------------------------------------------------------------- #

def test_merge_adds_new_items() -> None:
    articles = [make_article("第一条"), make_article("第二条"), make_article("第三条")]
    outcome = store.merge([], articles, NOW)
    assert outcome.added == 3
    assert outcome.refreshed == 0
    assert len(outcome.articles) == 3
    assert outcome.added_ids == {article.id for article in articles}


def test_merge_is_idempotent_across_runs(tmp_path) -> None:
    """同一批输入合并两次，结果必须逐字节相同且条目数不增长。

    这是「重复导入不应产生重复记录」的直接证明。
    """
    incoming = [
        make_article("OpenAI 发布 GPT-6 Astra"),
        make_article("清华发布结构化数据基础模型"),
        make_article("Gemini Live 音频能力上线"),
    ]
    first = store.merge([], copy.deepcopy(incoming), NOW)
    snapshot_one = _write(tmp_path, first.articles)

    reloaded = store.load_store(tmp_path / "news.json")
    assert reloaded is not None
    second = store.merge(
        list(reloaded.articles), copy.deepcopy(incoming), NOW
    )
    snapshot_two = _write(tmp_path, second.articles)

    assert second.added == 0
    assert second.refreshed == 3
    assert len(second.articles) == len(first.articles) == 3
    assert snapshot_two == snapshot_one


def test_first_seen_at_is_never_overwritten() -> None:
    """`first_seen_at` 是「本次运行的新内容」的判定锚点，一经写入不得改变。"""
    original = make_article("同一篇文章", first_seen_at=NOW - timedelta(hours=10))
    first = store.merge([], [original], NOW)
    assert first.articles[0].first_seen_at == NOW - timedelta(hours=10)

    reappeared = make_article("同一篇文章", first_seen_at=NOW)
    second = store.merge(first.articles, [reappeared], NOW)

    assert len(second.articles) == 1
    assert second.articles[0].first_seen_at == NOW - timedelta(hours=10)
    assert second.articles[0].last_seen_at == NOW


def test_duplicate_reasons_are_recorded() -> None:
    """去重必须留下理由，否则无法区分「去重生效」与「碰巧没有重复」。

    注意断言的是「被归并的那一方」而不是具体哪条 —— 谁成为保留记录由内容
    与 id 决定（见 `test_merge_is_independent_of_input_order`），不能写死。
    """
    left = make_article("OpenAI 发布 GPT-6", url="https://a.example.com/x")
    right = make_article("OpenAI 发布 GPT-6 - 量子位", url="https://b.example.com/y")
    outcome = store.merge([], [left, right], NOW)

    assert len(outcome.articles) == 1
    assert outcome.added == 1
    assert outcome.refreshed == 1
    assert len(outcome.duplicates) == 1
    merged_url, reason = outcome.duplicates[0]
    assert merged_url != outcome.articles[0].url
    assert merged_url in {"https://a.example.com/x", "https://b.example.com/y"}
    assert "包含度" in reason


def test_url_tracking_params_do_not_create_new_records() -> None:
    """同一篇文章二次抓取时 URL 带上了 `utm_*`，不得被判为新条目。"""
    base = "https://infoq.cn/article/abc"
    first = make_article("InfoQ 报道", url=base)
    again = make_article(
        "InfoQ 报道", url=base + "?utm_source=rss&utm_medium=article"
    )
    outcome = store.merge([], [first, again], NOW)
    assert len(outcome.articles) == 1
    assert outcome.added == 1


def test_merge_is_independent_of_input_order() -> None:
    """两条候选指向同一篇文章时，谁成为保留记录必须由内容决定，不能由调用顺序决定。"""
    strong = make_article(
        "OpenAI 发布 GPT-6", source_id="openai", source_weight=1.3,
        url="https://openai.com/x",
    )
    weak = make_article(
        "OpenAI 发布 GPT-6", source_id="techcrunch-ai", source_weight=1.1,
        url="https://techcrunch.com/y",
    )
    forward = store.merge([], [strong, weak], NOW)
    backward = store.merge([], [weak, strong], NOW)

    assert len(forward.articles) == len(backward.articles) == 1
    assert forward.articles[0].id == backward.articles[0].id
    # 来源权重高的一方胜出
    assert forward.articles[0].source_id == "openai"


def test_refresh_is_monotonic_for_volatile_fields() -> None:
    """热度只增不减：HN 的 points 会随时间上涨，重复导入不该把它改小。"""
    first = store.merge([], [make_article("热门讨论", heat=80, comments=10)], NOW)
    again = make_article("热门讨论", heat=200, comments=45)
    second = store.merge(first.articles, [again], NOW)
    assert second.articles[0].heat == 200
    assert second.articles[0].comments == 45

    smaller = make_article("热门讨论", heat=5, comments=1)
    third = store.merge(second.articles, [smaller], NOW)
    assert third.articles[0].heat == 200


def test_refresh_fills_missing_published_at() -> None:
    """先入库时发布时间缺失，后续抓到了就补上；已有值不覆盖。"""
    without = make_article("来源未给时间", published_at=None)
    first = store.merge([], [without], NOW)
    assert first.articles[0].published_at is None

    with_time = make_article("来源未给时间", published_at=NOW - timedelta(hours=2))
    second = store.merge(first.articles, [with_time], NOW)
    assert second.articles[0].published_at == NOW - timedelta(hours=2)


def test_refresh_keeps_longer_summary() -> None:
    short = make_article("摘要演变", summary="短摘要")
    first = store.merge([], [short], NOW)
    longer = make_article("摘要演变", summary="这是一段明显更长也更完整的摘要正文内容")
    second = store.merge(first.articles, [longer], NOW)
    assert second.articles[0].summary == "这是一段明显更长也更完整的摘要正文内容"

    tiny = make_article("摘要演变", summary="短")
    third = store.merge(second.articles, [tiny], NOW)
    assert third.articles[0].summary == "这是一段明显更长也更完整的摘要正文内容"


# --------------------------------------------------------------------------- #
# 滚动窗口与容量
# --------------------------------------------------------------------------- #

def test_window_evicts_old_items() -> None:
    old = make_article("很久以前的条目", published_at=NOW - timedelta(days=40))
    fresh = make_article("今天的条目")
    outcome = store.merge([old], [fresh], NOW, window_days=30)

    assert [article.title for article in outcome.articles] == ["今天的条目"]
    assert outcome.evicted_by_window == 1


def test_items_without_published_at_use_first_seen_for_window() -> None:
    """没有发布时间的条目按首次见到的时间判窗口，不能被无差别丢弃。"""
    item = make_article("时间未知但刚见到", published_at=None, first_seen_at=NOW)
    outcome = store.merge([], [item], NOW, window_days=30)
    assert len(outcome.articles) == 1


def test_capacity_keeps_newest() -> None:
    articles = [
        make_article(f"条目{i}", published_at=NOW - timedelta(hours=i)) for i in range(10)
    ]
    outcome = store.merge([], articles, NOW, max_items=4)

    assert len(outcome.articles) == 4
    assert outcome.evicted_by_capacity == 6
    assert [article.title for article in outcome.articles] == [
        "条目0", "条目1", "条目2", "条目3",
    ]


def test_retention_sort_is_stable_across_runs(tmp_path) -> None:
    """留存后的顺序必须由不可变字段决定，重复运行顺序一致。"""
    articles = [
        make_article(f"条目{i}", published_at=NOW - timedelta(minutes=i * 7))
        for i in range(20)
    ]
    first = store.merge([], copy.deepcopy(articles), NOW)
    second = store.merge([], copy.deepcopy(list(reversed(articles))), NOW)
    assert [a.id for a in first.articles] == [a.id for a in second.articles]


# --------------------------------------------------------------------------- #
# 原子写与容错
# --------------------------------------------------------------------------- #

def test_write_is_atomic_and_leaves_no_temp_file(tmp_path) -> None:
    path = tmp_path / "news.json"
    store.write_json_atomic(path, '{"a":1}\n')
    assert path.read_text(encoding="utf-8") == '{"a":1}\n'
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "news.json"]
    assert leftovers == []


def test_write_ends_with_single_newline(tmp_path) -> None:
    path = tmp_path / "x.json"
    store.write_json_atomic(path, '{"a":1}')
    assert path.read_text(encoding="utf-8").endswith("}\n")


def test_load_store_returns_none_for_broken_file(tmp_path) -> None:
    """存储损坏不能拖垮整轮采集：按首次运行处理并让上层发出警告。"""
    path = tmp_path / "news.json"
    path.write_text("不是 JSON", encoding="utf-8")
    assert store.load_store(path) is None


def test_load_store_returns_none_for_missing_file(tmp_path) -> None:
    assert store.load_store(tmp_path / "nope.json") is None


def test_dump_and_load_roundtrip(tmp_path) -> None:
    articles = [make_article("往返测试", summary="摘要", heat=42)]
    path = tmp_path / "news.json"
    store.write_json_atomic(
        path,
        store.dump_store(NewsStore(generated_at=NOW, window_days=30, articles=articles)),
    )
    reloaded = store.load_store(path)
    assert reloaded is not None
    assert reloaded.articles[0].title == "往返测试"
    assert reloaded.articles[0].heat == 42
    assert reloaded.articles[0].published_at == NOW


def test_dumped_json_is_parseable_and_has_schema_version(tmp_path) -> None:
    path = tmp_path / "news.json"
    store.write_json_atomic(
        path,
        store.dump_store(
            NewsStore(
                generated_at=NOW, window_days=30, articles=[make_article("x")]
            )
        ),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["window_days"] == 30
    assert payload["generated_at"].startswith("2026-09-16T12:00:00")