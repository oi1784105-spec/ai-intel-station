"""离线重放验收测试：不访问网络，验证可复现性、幂等性与产物完整性。

这是提交材料里最硬的一条证据 —— 评审在任意机器上执行同一命令，
必须得到逐字节相同的产物。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "replay"
FROZEN_NOW = "2026-09-16T12:00:00Z"
OUTPUT_FILES = ("news.json", "update-status.json", "meta.json", "runs.json")


def _replay(data_dir: Path) -> subprocess.CompletedProcess:
    """以冻结时刻离线重放一次。返回完成的进程，便于断言退出码与 stderr。"""
    return subprocess.run(
        [
            sys.executable, "collect.py",
            "--input", str(FIXTURES),
            "--now", FROZEN_NOW,
            "--data-dir", str(data_dir),
            "--quiet",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.fixture(scope="module")
def replay(tmp_path_factory) -> dict:
    """跑三次重放，并在关键节点留快照。

    - 第 1 次写入 A（首轮）
    - 第 2 次写入 B（同输入、同冻结时刻 → 与 A 比对确定性）
    - 第 3 次再写入 A（把 A 已有数据当历史再合并一次 → 比对幂等性）

    确定性比对的快照必须在第 3 次运行**之前**取，否则 A 的 runs.json
    会因为多了一次运行而与 B 不同，测到的是运行次数而不是确定性。
    """
    dir_a = tmp_path_factory.mktemp("replay_a")
    dir_b = tmp_path_factory.mktemp("replay_b")

    first = _replay(dir_a)
    assert first.returncode == 0, first.stderr

    # 在 A 尚未追加第二次运行前快照两份产物
    snapshot_a = {name: (dir_a / name).read_bytes() for name in OUTPUT_FILES}
    first_news = json.loads((dir_a / "news.json").read_text(encoding="utf-8"))

    second = _replay(dir_b)
    assert second.returncode == 0, second.stderr

    third = _replay(dir_a)
    assert third.returncode == 0, third.stderr
    second_news = json.loads((dir_a / "news.json").read_text(encoding="utf-8"))

    return {
        "a": dir_a,
        "b": dir_b,
        "snapshot_a": snapshot_a,
        "first_news": first_news,
        "second_news": second_news,
    }


def test_fixture_set_exists_and_is_documented() -> None:
    """离线样本必须齐备，且带有可核对的来源清单。"""
    manifest_path = FIXTURES / "manifest.json"
    assert manifest_path.exists(), "缺少 manifest.json，样本来源无法核对"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["sources"]
    assert len(entries) >= 15, f"样本源过少：{len(entries)}"

    for entry in entries:
        assert entry.get("ok") is True, f"{entry['id']} 抓取失败：{entry.get('error')}"
        assert entry["http_status"] == 200
        assert (FIXTURES / entry["file"]).exists(), f"{entry['id']} 的样本文件缺失"
        assert entry["sha256"], f"{entry['id']} 缺少 sha256，内容无法核对"


def test_replay_outputs_are_byte_identical(replay: dict) -> None:
    """同输入 + 同冻结时刻，两次运行的产物必须逐字节相同。"""
    for name in OUTPUT_FILES:
        left = replay["snapshot_a"][name]
        right = (replay["b"] / name).read_bytes()
        assert left == right, f"{name} 两次运行结果不一致"


def test_replay_is_idempotent(replay: dict) -> None:
    """把首轮产物当历史再合并一次同批输入，条目集合不得改变。"""
    first = replay["first_news"]["articles"]
    second = replay["second_news"]["articles"]
    assert first, "重放没有产出任何条目"
    assert [article["id"] for article in first] == [article["id"] for article in second], (
        "重复导入改变了条目集合"
    )

    runs = json.loads((replay["a"] / "runs.json").read_text(encoding="utf-8"))["runs"]
    assert len(runs) == 2, "第三次重放应追加一条运行记录"
    assert runs[-1]["total_new"] == 0, "重复导入不应产生新条目"
    assert runs[-1]["total_dup"] > 0, "重复导入应被识别为「已存在」，而不是被忽略"


def test_replay_articles_have_unique_identifiers(replay: dict) -> None:
    """去重的最终证明：id、归一 URL、标题三者都不得有重复。"""
    articles = replay["first_news"]["articles"]

    ids = [article["id"] for article in articles]
    urls = [article["canonical_url"] for article in articles]
    titles = [article["title"] for article in articles]

    assert len(set(ids)) == len(ids)
    assert len(set(urls)) == len(urls)
    assert len(set(titles)) == len(titles)


def test_replay_covers_both_languages_and_multiple_sources(replay: dict) -> None:
    payload = json.loads((replay["a"] / "news.json").read_text(encoding="utf-8"))
    counts = payload["counts"]

    assert counts["zh"] > 0, "中文源没有产出条目"
    assert counts["en"] > 0, "英文源没有产出条目"
    assert counts["official"] > 0, "官方源没有产出条目"
    assert len(payload["sources"]) >= 15


def test_replay_never_fabricates_publish_time(replay: dict) -> None:
    """无法采信发布时间时必须是 `null` 并给出原因，不能退化成抓取时间。"""
    articles = json.loads(
        (replay["a"] / "news.json").read_text(encoding="utf-8")
    )["articles"]
    notes = {
        "missing", "unparseable", "future_date", "tz_assumed", "tz_corrected", "implausible",
    }
    for article in articles:
        if article["published_at"] is None:
            assert article["published_note"] in notes, article["published_note"]
        else:
            assert article["published_raw"], "有发布时间就必须保留原始字符串以便追溯"


def test_replay_reports_timezone_correction(replay: dict) -> None:
    """InfoQ 的时区缺陷必须在运行报告里留下痕迹，证明它被处理而不是被静默吞掉。"""
    status = json.loads(
        (replay["a"] / "update-status.json").read_text(encoding="utf-8")
    )
    infoq = next(s for s in status["sources"] if s["id"] == "infoq-ai")
    assert infoq["items_tz_corrected"] > 0


def test_replay_flags_stale_source_only_when_asked(tmp_path) -> None:
    """「疑似停更」与「抓取失败」必须可区分：留档源显式采集时给出 stale 而不是 failed。"""
    result = subprocess.run(
        [
            sys.executable, "collect.py",
            "--input", str(FIXTURES),
            "--only", "venturebeat-ai",
            "--now", FROZEN_NOW,
            "--data-dir", str(tmp_path),
            "--quiet",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr

    status = json.loads((tmp_path / "update-status.json").read_text(encoding="utf-8"))
    outcome = next(s for s in status["sources"] if s["id"] == "venturebeat-ai")
    assert outcome["status"] == "stale"
    assert outcome["http_status"] == 200
    assert outcome["error"] and "14 天" in outcome["error"]


def test_dry_run_writes_nothing(tmp_path) -> None:
    """`--dry-run` 必须真的不落盘，否则它就没有存在的意义。"""
    result = subprocess.run(
        [
            sys.executable, "collect.py",
            "--input", str(FIXTURES),
            "--now", FROZEN_NOW,
            "--data-dir", str(tmp_path),
            "--dry-run",
            "--quiet",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []