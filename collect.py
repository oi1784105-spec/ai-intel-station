#!/usr/bin/env python3
"""每日 AI 情报站 · 采集入口。

一条命令跑完 抓取 → 解析 → 归一化 → 相关性过滤 → 去重 → 事件聚合 → 打分 → 落盘。

用法:

    python collect.py                              # 采集全部源并更新 data/
    python collect.py --only qbitai,hn             # 只采集指定源，便于调试单源
    python collect.py --dry-run                    # 全流程跑通但不写任何文件
    python collect.py --input tests/fixtures/replay \\
        --now 2026-09-16T12:00:00Z                 # 离线确定性重放，验收测试用

退出码: 0 = 运行成功（含「部分源失败」），1 = 全部源失败，130 = 被中断。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 支持 `python collect.py` 与 `python -m` 两种启动方式。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import adapters
from pipeline import cluster as cluster_module
from pipeline import config
from pipeline import fetch
from pipeline import normalize
from pipeline import relevance
from pipeline import report as report_module
from pipeline import score
from pipeline import store
from pipeline.fetch import FetchError
from pipeline.models import Article, Highlight, Meta, NewsStore, RunReport, SourceOutcome
from pipeline.parse import parse_published


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数。"""
    parser = argparse.ArgumentParser(
        prog="collect.py",
        description="每日 AI 情报站采集管线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python collect.py\n"
            "  python collect.py --only qbitai,hn --quiet\n"
            "  python collect.py --input tests/fixtures/replay --now 2026-09-16T12:00:00Z\n"
        ),
    )
    parser.add_argument("--only", default=None, metavar="IDS",
                        help="逗号分隔的源 id，只采集这些源")
    parser.add_argument("--input", default=None, metavar="DIR",
                        help="从本地目录重放响应，完全离线（验收测试用）")
    parser.add_argument("--now", default=None, metavar="ISO8601",
                        help="冻结基准时刻；与 --input 同用可得到逐字节确定的结果")
    parser.add_argument("--window-days", type=int, default=config.WINDOW_DAYS,
                        help=f"滚动窗口天数（默认 {config.WINDOW_DAYS}）")
    parser.add_argument("--data-dir", default=None, metavar="DIR",
                        help=f"数据输出目录（默认 {config.DATA_DIR}）；"
                             "离线重放与测试用它避免覆盖生产数据")
    parser.add_argument("--max-items", type=int, default=config.MAX_STORE_ITEMS,
                        help=f"存储条目上限（默认 {config.MAX_STORE_ITEMS}）")
    parser.add_argument("--trigger", choices=("manual", "cron", "fixture"), default=None,
                        help="运行触发方式，仅用于报告与 runs.json 标记")
    parser.add_argument("--dry-run", action="store_true", help="跑完整流程但不写任何文件")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="忽略已有 data/news.json，从零重建。修正 config 中的解析参数"
             "（例如源站时区偏移）后用它重建，否则旧记录的字段不会被就地改写",
    )
    parser.add_argument("--quiet", action="store_true", help="不打印报告")
    return parser


def _utcnow() -> datetime:
    """当前 UTC 时刻，秒级截断（秒级让 JSON 输出稳定、便于断言）。"""
    return datetime.now(timezone.utc).replace(microsecond=0)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _resolve_now(raw: str | None) -> datetime:
    """解析 `--now`。放宽未来时间校验，因为它就是被用来冻结基准时刻的。"""
    if raw is None:
        return _utcnow()
    parsed, note = parse_published(raw, datetime(2100, 1, 1, tzinfo=timezone.utc))
    if parsed is None:
        raise SystemExit(f"--now 无法解析：{raw!r}（{note}）")
    return parsed


def _select_sources(only: str | None) -> list[config.SourceConfig]:
    """按 `--only` 取源清单，遇到未知 id 立即失败而不是静默跳过。"""
    if not only:
        return list(config.SOURCES)
    wanted = [part.strip() for part in only.split(",") if part.strip()]
    unknown = [source_id for source_id in wanted if source_id not in config.SOURCES_BY_ID]
    if unknown:
        raise SystemExit(
            f"未知的源 id：{', '.join(unknown)}\n可用 id：{', '.join(config.SOURCES_BY_ID)}"
        )
    return [config.SOURCES_BY_ID[source_id] for source_id in wanted]


def _collect_one(
    source: config.SourceConfig, fetcher: fetch.FetcherProtocol, now: datetime
) -> tuple[SourceOutcome, list[Article]]:
    """采集单个源。**任何异常都被收敛成该源的 failed 状态**，不影响其它源。"""
    started = time.perf_counter()
    outcome = SourceOutcome(
        id=source.id,
        name=source.name,
        name_en=source.display_name,
        url=source.url,
        status="ok",
    )

    try:
        result = adapters.collect_source(source, fetcher, now)
    except FetchError as exc:
        outcome.status = "failed"
        outcome.http_status = exc.http_status
        outcome.error = f"{exc.kind}：{exc}"
        outcome.duration_ms = _elapsed_ms(started)
        return outcome, []
    except Exception as exc:  # noqa: BLE001
        # 单个源的畸形响应不得中断整轮采集。这里显式记录异常类型，
        # 使「解析器有 bug」与「源站坏掉」在报告里可区分。
        outcome.status = "failed"
        outcome.error = f"适配器异常 {type(exc).__name__}：{exc}"
        outcome.duration_ms = _elapsed_ms(started)
        return outcome, []

    outcome.http_status = result.http_status
    outcome.items_seen = result.seen
    if result.bozo:
        outcome.status = "parse_warning"
        outcome.error = f"XML 非法但已容错解析：{result.bozo_exception}"

    batch = normalize.normalize_batch(result.items, source, now)
    outcome.items_tz_corrected = batch.tz_corrected
    outcome.items_summary_dropped = batch.summary_dropped
    outcome.items_with_image = batch.with_image
    normalized = batch.articles
    if result.seen == 0:
        outcome.status = "parse_warning"
        outcome.error = outcome.error or "响应中没有任何条目（源格式可能已变）"
    elif not normalized:
        outcome.status = "parse_warning"
        outcome.error = outcome.error or f"{result.seen} 条原始条目全部缺少标题或链接"

    kept: list[Article] = []
    for article in normalized:
        if relevance.should_keep(article.title, article.summary, source):
            kept.append(article)
        else:
            outcome.items_filtered += 1

    outcome.items_kept = len(kept)
    published = [article.published_at for article in kept if article.published_at]
    outcome.latest_published_at = max(published) if published else None
    outcome.duration_ms = _elapsed_ms(started)
    return outcome, kept


def _finalize_outcomes(
    outcomes: list[SourceOutcome],
    offered_ids_by_source: dict[str, set[str]],
    out_of_window_by_source: dict[str, int],
    outcome_of_merge: store.MergeOutcome,
    now: datetime,
) -> None:
    """合并完成后回填每源的 新增/重复/窗口外 计数，并判定 `stale` 与 `no_new`。

    `stale` 优先于 `no_new`：源站还在响应但没有新内容，与源站内容已停更是两件事，
    必须分开呈现，否则第一个失效的源会被当成正常源悄悄漏掉。
    """
    stale_cutoff = now - timedelta(days=config.STALE_SOURCE_DAYS)

    for outcome in outcomes:
        outcome.items_out_of_window = out_of_window_by_source.get(outcome.id, 0)
        if outcome.status in ("failed", "parse_warning"):
            continue

        # 只按「窗口内提交给合并的条目」计新增/重复，窗口外条目不计入，
        # 否则每轮都会把同一批过期条目重复算成新增。
        offered_ids = offered_ids_by_source.get(outcome.id, set())
        outcome.items_new = len(offered_ids & outcome_of_merge.added_ids)
        outcome.items_dup = len(offered_ids) - outcome.items_new

        if outcome.latest_published_at is not None and outcome.latest_published_at < stale_cutoff:
            outcome.status = "stale"
            days = (now - outcome.latest_published_at).days
            outcome.error = (
                f"最新条目发布于 {outcome.latest_published_at:%Y-%m-%d}（{days} 天前），"
                f"超过 {config.STALE_SOURCE_DAYS} 天阈值"
            )
        elif outcome.items_new == 0:
            outcome.status = "no_new"


def _overall(outcomes: list[SourceOutcome], added: int) -> str:
    """整次运行的结论。部分源失败仍算成功，因为其余源的数据是有效的。"""
    if outcomes and all(outcome.status == "failed" for outcome in outcomes):
        return "failed"
    if any(outcome.status == "failed" for outcome in outcomes):
        return "partial"
    if added == 0:
        return "no_new"
    return "success"


def _counts(articles: list[Article], now: datetime) -> dict[str, int]:
    """存储统计，供前端筛选器直接显示条数而无需自己遍历。"""
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    published = [article.published_at for article in articles if article.published_at]
    return {
        "total": len(articles),
        "zh": sum(1 for article in articles if article.lang == "zh"),
        "en": sum(1 for article in articles if article.lang == "en"),
        "official": sum(1 for article in articles if article.source_official),
        "with_published_at": len(published),
        "without_published_at": len(articles) - len(published),
        "clustered": sum(1 for article in articles if article.cluster_id),
        # 「有图显图」的实际覆盖率：前端占位块的比例应等于 1 - with_image/total，
        # 把它写进产物是为了让这个比例可核对，而不是只能靠肉眼数卡片。
        "with_image": sum(1 for article in articles if article.image_url),
        "last_24h": sum(1 for moment in published if moment >= day_ago),
        "last_7d": sum(1 for moment in published if moment >= week_ago),
    }


def _source_payload(source: config.SourceConfig) -> dict:
    """写进 `news.json` 的源元信息。**不含任何展示属性**（颜色/色相等由前端派生）。"""
    return {
        "id": source.id,
        "name": source.name,
        # 界面一律读这一项：源标签全站统一为英文形态，中文名仅用于溯源。
        "name_en": source.display_name,
        "lang": source.lang,
        "kind": source.kind,
        "url": source.url,
        "weight": source.weight,
        "official": source.official,
    }


def _load_runs(path: Path) -> list[dict]:
    """读取历史运行记录。无法解析时按空历史处理，不阻断本次运行。"""
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    runs = payload.get("runs")
    return runs if isinstance(runs, list) else []


def _outcome_warnings(outcomes: list[SourceOutcome]) -> list[str]:
    """把需要人工介入的源汇总成一句话，写进 runs.json 便于事后检索。"""
    degraded = [o for o in outcomes if o.status in ("failed", "parse_warning", "stale")]
    return [
        f"{o.name}[{o.status}]" + (f" {o.error}" if o.error else "") for o in degraded
    ]


def _run_id(
    now: datetime,
    *,
    frozen: bool,
    trigger: str,
    sources: list[config.SourceConfig],
    offline: bool,
) -> str:
    """运行 id：时间戳 + 短后缀。

    传入 `--now`（冻结时刻）时后缀由输入指纹决定，使整次运行的**全部产物逐字节可复现**；
    否则用随机后缀，让同一秒内重跑两次的运行可区分。可复现性对验收测试是硬要求 ——
    评审必须能在别的机器上跑出与提交材料完全一致的输出。
    """
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    if not frozen:
        return f"{stamp}-{uuid.uuid4().hex[:6]}"
    fingerprint = "|".join((trigger, ",".join(s.id for s in sources), str(offline)))
    return f"{stamp}-{hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()[:6]}"


def run(args: argparse.Namespace) -> tuple[RunReport, list[Highlight], dict[str, str]]:
    """执行一次完整采集，返回 `(运行报告, 今日重点, 已写入文件)`。"""
    reference_now = _resolve_now(args.now)
    wall_started = time.perf_counter()
    offline = bool(args.input)
    trigger = args.trigger or ("fixture" if offline else "manual")

    data_dir = Path(args.data_dir) if args.data_dir else Path(config.DATA_DIR)
    news_path = data_dir / "news.json"
    runs_path = data_dir / "runs.json"
    status_path = data_dir / "update-status.json"
    meta_path = data_dir / "meta.json"

    existing_store = store.load_store(news_path)
    existing_articles = list(existing_store.articles) if existing_store else []
    runs_history = _load_runs(runs_path)

    warnings: list[str] = []
    if args.rebuild:
        existing_articles = []
        if existing_store is not None:
            warnings.append("--rebuild：已丢弃现有存储重建（幂等性保证重建不产生重复记录）")
    elif existing_store is None and news_path.exists():
        warnings.append(
            f"{news_path} 无法解析，本次按首次运行处理（历史去重锚点丢失，可能重复导入）"
        )

    fetcher: fetch.FetcherProtocol = (
        fetch.FixtureFetcher(args.input) if offline else fetch.Fetcher()
    )
    outcomes: list[SourceOutcome] = []
    incoming_by_source: dict[str, list[Article]] = {}

    selected_sources = _select_sources(args.only)

    try:
        for source in selected_sources:
            if isinstance(fetcher, fetch.FixtureFetcher):
                fetcher.bind(source.id)
            outcome, articles = _collect_one(source, fetcher, reference_now)
            outcomes.append(outcome)
            incoming_by_source[source.id] = articles
    finally:
        fetcher.close()

    incoming = [article for articles in incoming_by_source.values() for article in articles]
    # 窗口外条目在合并前就丢弃：它们进了合并也会立刻被 retention 淘汰，
    # 结果每轮都把同一批过期条目重新算成「新增」。计数保留下来作为证据。
    incoming, out_of_window = store.split_by_window(
        incoming, reference_now, args.window_days
    )
    offered_ids_by_source: dict[str, set[str]] = {}
    for article in incoming:
        offered_ids_by_source.setdefault(article.source_id, set()).add(article.id)
    out_of_window_by_source: dict[str, int] = {}
    for article in out_of_window:
        out_of_window_by_source[article.source_id] = (
            out_of_window_by_source.get(article.source_id, 0) + 1
        )

    if args.now is not None:
        # 冻结时刻时清掉每个源的墙钟耗时，使重放产物与运行机器的快慢无关。
        # 与整体 `duration_ms` 置零同因：可复现性优先于诊断信息。
        for outcome in outcomes:
            outcome.duration_ms = 0
    merge_outcome = store.merge(
        existing_articles,
        incoming,
        reference_now,
        window_days=args.window_days,
        max_items=args.max_items,
    )
    articles = merge_outcome.articles
    # 展示名从配置重派生到**全部在库条目**，而不是只在合并候选上更新 —— 否则源站本轮
    # 没回的条目（已滚出 feed 首页、或该源整体抓取失败）会一直留着旧名，页面上露出
    # 中文源名。放在聚合与打分之前，重点条目才能一并拿到正确的展示名。
    store.sync_source_display_names(articles)

    # 聚合与打分必须在**合并后的最终集合**上做：跨源命中度取决于当前窗口内是否存在
    # 其它源的报道，只看向本轮入参会把跨源事件判成孤立条目。
    clusters = cluster_module.build_clusters(articles, reference_now)
    cluster_of = cluster_module.cluster_index(clusters)
    for article in articles:
        article.cluster_id = cluster_of.get(article.id)
    for article in articles:
        cluster = clusters.get(article.cluster_id) if article.cluster_id else None
        breakdown = score.evaluate(article, cluster, reference_now)
        article.score = breakdown.score
        article.reasons = breakdown.reasons
        article.cluster_size = breakdown.cluster_size
        article.independent_sources = breakdown.independent_sources

    highlights = score.select_highlights(articles, clusters, reference_now)
    _finalize_outcomes(
        outcomes, offered_ids_by_source, out_of_window_by_source, merge_outcome, reference_now
    )

    duration_ms = _elapsed_ms(wall_started)
    started_at = reference_now
    finished_at = reference_now if args.now else _utcnow()
    counts = _counts(articles, reference_now)

    run_report = RunReport(
        run_id=_run_id(
            reference_now,
            frozen=args.now is not None,
            trigger=trigger,
            sources=selected_sources,
            offline=offline,
        ),
        started_at=started_at,
        finished_at=finished_at,
        # 冻结时刻时耗时恒为 0，让重放结果与机器快慢无关。
        duration_ms=0 if args.now else duration_ms,
        trigger=trigger,
        offline=offline,
        sources=outcomes,
        overall=_overall(outcomes, merge_outcome.added),
        total_in_store=len(articles),
        total_new=merge_outcome.added,
        total_dup=merge_outcome.refreshed,
        highlights=[highlight.article_id for highlight in highlights],
        warnings=warnings + _outcome_warnings(outcomes),
    )

    if args.dry_run:
        return run_report, highlights, {}

    news_store = NewsStore(
        schema_version=1,
        generated_at=reference_now,
        window_days=args.window_days,
        sources=[_source_payload(source) for source in config.SOURCES],
        counts=counts,
        articles=articles,
    )
    store.write_json_atomic(news_path, store.dump_store(news_store))

    runs_all = (runs_history + [run_report.model_dump(mode="json")])[-config.RETAIN_RUNS :]
    store.write_json_atomic(
        runs_path,
        json.dumps({"schema_version": 1, "runs": runs_all}, ensure_ascii=False, indent=2),
    )

    status_payload = report_module.build_status_payload(
        run_report, runs_all, reference_now, highlights
    )
    store.write_json_atomic(
        status_path, json.dumps(status_payload, ensure_ascii=False, indent=2)
    )

    meta = Meta.model_validate(
        report_module.build_meta_payload(
            run_report, counts, highlights, runs_all, reference_now, len(config.SOURCES)
        )
    )
    store.write_json_atomic(
        meta_path, json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, indent=2)
    )

    written = {
        "条目数据": str(news_path),
        "运行历史": str(runs_path),
        "更新状态": str(status_path),
        "页面摘要": str(meta_path),
    }
    return run_report, highlights, written


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    args = build_parser().parse_args(argv)

    try:
        run_report, highlights, written = run(args)
    except KeyboardInterrupt:
        print("\n已中断，未写入任何文件。", file=sys.stderr)
        return 130

    if not args.quiet:
        print(report_module.render_console(run_report, highlights))
        print()
        if written:
            for label, path in written.items():
                print(f"已写入  {label:<8} {path}")
        else:
            print("--dry-run：未写入任何文件。")

    return 1 if run_report.overall == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())