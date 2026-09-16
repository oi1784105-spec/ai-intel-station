"""运行报告：控制台表格与 `data/update-status.json`。

控制台表格按**东亚字符宽度**计算列宽，中文列不会错位。
"""

from __future__ import annotations

import unicodedata
from datetime import datetime

from . import config
from .models import Highlight, RunReport, SourceOutcome

#: 源状态的中文标签。`parse_warning` 与 `stale` 是必须单独呈现的失效模式。
STATUS_LABEL: dict[str, str] = {
    "ok": "正常",
    "no_new": "无新增",
    "parse_warning": "解析异常",
    "stale": "疑似停更",
    "failed": "抓取失败",
}

OVERALL_LABEL: dict[str, str] = {
    "success": "成功",
    "partial": "部分成功",
    "no_new": "无新增",
    "failed": "失败",
}

TRIGGER_LABEL: dict[str, str] = {
    "manual": "手动运行",
    "cron": "定时任务",
    "fixture": "离线重放",
}


def _display_width(text: str) -> int:
    """按终端显示宽度计算字符串长度（全角字符占 2 列）。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int) -> str:
    """左侧对齐补空格到指定显示宽度。"""
    return text + " " * max(0, width - _display_width(text))


def _pad_left(text: str, width: int) -> str:
    """右侧对齐补空格到指定显示宽度。"""
    return " " * max(0, width - _display_width(text)) + text


def _short_time(value: datetime | None) -> str:
    """把时间压成 `MM-DD HH:MM` 以便在表格中显示。"""
    if value is None:
        return "—"
    return value.strftime("%m-%d %H:%M")


def _source_row(outcome: SourceOutcome) -> list[str]:
    return [
        outcome.name,
        STATUS_LABEL.get(outcome.status, outcome.status),
        str(outcome.items_seen),
        str(outcome.items_kept),
        str(outcome.items_filtered),
        str(outcome.items_new),
        str(outcome.items_dup),
        # 提取到配图的条目数：控制台里就能看出「有图显图」的实际覆盖情况，
        # 不必去页面上数卡片。它同时也是「配图提取逻辑是否悄悄退化」的观测点。
        str(outcome.items_with_image),
        _short_time(outcome.latest_published_at),
        f"{outcome.duration_ms}ms",
    ]


def render_console(report: RunReport, highlights: list[Highlight]) -> str:
    """渲染运行报告文本。"""
    headers = ["来源", "状态", "抓取", "保留", "过滤", "新增", "重复", "配图", "最新条目", "耗时"]
    rows = [_source_row(outcome) for outcome in report.sources]

    widths = [max(_display_width(headers[i]), *(_display_width(row[i]) for row in rows)) if rows
              else _display_width(headers[i]) for i in range(len(headers))]

    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("  每日 AI 情报站 · 采集报告")
    lines.append("=" * 78)
    lines.append(f"运行 ID    {report.run_id}")
    lines.append(f"触发方式   {TRIGGER_LABEL.get(report.trigger, report.trigger)}"
                 + ("（离线重放，未访问网络）" if report.offline else ""))
    lines.append(f"开始时间   {report.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"耗时       {report.duration_ms / 1000:.1f}s")
    lines.append(f"结论       {OVERALL_LABEL.get(report.overall, report.overall)}")
    lines.append("")
    lines.append("  ".join(_pad(headers[i], widths[i]) for i in range(len(headers))))
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  ".join(_pad(row[i], widths[i]) for i in range(len(headers))))
    lines.append("")
    out_of_window = sum(o.items_out_of_window for o in report.sources)
    lines.append(
        f"合计：{len(report.sources)} 个源 · 原始 {sum(o.items_seen for o in report.sources)} 条"
        f" · 保留 {sum(o.items_kept for o in report.sources)} 条"
        f" · 新增 {report.total_new} 条 · 去重 {report.total_dup} 条"
        + (f" · 窗口外丢弃 {out_of_window} 条" if out_of_window else "")
        + f" · 提取配图 {sum(o.items_with_image for o in report.sources)} 条"
        + f" · 存储共 {report.total_in_store} 条"
    )

    degraded = [o for o in report.sources if o.status not in ("ok", "no_new")]
    if degraded:
        lines.append("")
        lines.append("需要关注的源：")
        for outcome in degraded:
            detail = f"：{outcome.error}" if outcome.error else ""
            lines.append(
                f"  - {outcome.name} [{STATUS_LABEL.get(outcome.status, outcome.status)}]"
                f" 最新条目 {_short_time(outcome.latest_published_at)}{detail}"
            )

    tz_fixed = [o for o in report.sources if o.items_tz_corrected]
    summary_fixed = [o for o in report.sources if o.items_summary_dropped]
    if tz_fixed or summary_fixed:
        lines.append("")
        lines.append("源站数据缺陷处理记录：")
        for outcome in tz_fixed:
            lines.append(
                f"  - {outcome.name}：更正时区标注 {outcome.items_tz_corrected} 条"
                "（源站钟面时间按 config 中的偏移重新解释）"
            )
        for outcome in summary_fixed:
            lines.append(
                f"  - {outcome.name}：丢弃无效摘要 {outcome.items_summary_dropped} 条"
                "（纯导航样板或过短，不当作摘要展示）"
            )

    if highlights:
        lines.append("")
        lines.append(f"今日重点（{len(highlights)} 条）：")
        for index, highlight in enumerate(highlights, start=1):
            reasons = " / ".join(highlight.reasons[:3])
            lines.append(f"  {index}. [{highlight.score:.2f}] {highlight.title}")
            lines.append(f"     {highlight.source_name} · {reasons}")

    return "\n".join(lines)


def compute_health(outcomes: list[SourceOutcome]) -> dict[str, int]:
    """按状态统计源数量，用于前端健康度横幅。"""
    health = {key: 0 for key in STATUS_LABEL}
    for outcome in outcomes:
        health[outcome.status] = health.get(outcome.status, 0) + 1
    return health


def success_rate_7d(runs: list[dict], now: datetime) -> float | None:
    """近 7 天非失败运行的比例。无历史数据时返回 `None`（而不是 0 或 1）。"""
    from datetime import timedelta

    from .parse import parse_published

    recent = []
    for run in runs:
        started_at, _ = parse_published(run.get("started_at"), now)
        if started_at is not None and now - started_at <= timedelta(days=7):
            recent.append(run)
    if not recent:
        return None
    healthy = sum(1 for run in recent if run.get("overall") in ("success", "no_new"))
    return round(healthy / len(recent), 4)


def build_status_payload(
    report: RunReport, runs: list[dict], now: datetime, highlights: list[Highlight]
) -> dict:
    """构造 `data/update-status.json`。前端据此展示「本次更新了 N 条 / 哪些源异常」。"""
    return {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "run_id": report.run_id,
        "trigger": report.trigger,
        "offline": report.offline,
        "overall": report.overall,
        "total_in_store": report.total_in_store,
        "new_this_run": report.total_new,
        "deduped_this_run": report.total_dup,
        "window_days": config.WINDOW_DAYS,
        "health": {
            **compute_health(report.sources),
            "success_rate_7d": success_rate_7d(runs, now),
        },
        "sources": [
            {
                "id": outcome.id,
                "name": outcome.name,
                # 与 `news.json` 源表同字段：前端展示源名只认 `name_en`，全站因此统一。
                "name_en": outcome.name_en,
                "status": outcome.status,
                "http_status": outcome.http_status,
                "items_seen": outcome.items_seen,
                "items_kept": outcome.items_kept,
                "items_filtered": outcome.items_filtered,
                "items_tz_corrected": outcome.items_tz_corrected,
                "items_summary_dropped": outcome.items_summary_dropped,
                "items_with_image": outcome.items_with_image,
                "items_out_of_window": outcome.items_out_of_window,
                "items_new": outcome.items_new,
                "items_dup": outcome.items_dup,
                "latest_published_at": (
                    outcome.latest_published_at.isoformat(timespec="seconds")
                    if outcome.latest_published_at
                    else None
                ),
                "duration_ms": outcome.duration_ms,
                "error": outcome.error,
            }
            for outcome in report.sources
        ],
        "highlights": [highlight.model_dump(mode="json") for highlight in highlights],
    }


def build_meta_payload(
    report: RunReport, store_counts: dict[str, int], highlights: list[Highlight],
    runs: list[dict], now: datetime, source_count: int,
) -> dict:
    """构造 `data/meta.json`：页脚与「今日重点」摘要所需的最小信息。"""
    return {
        "schema_version": 1,
        "last_run_id": report.run_id,
        "last_run_at": now.isoformat(timespec="seconds"),
        "last_success_at": _last_success(runs, now),
        "total_articles": store_counts.get("total", 0),
        "source_count": source_count,
        "success_rate_7d": success_rate_7d(runs, now),
        "window_days": config.WINDOW_DAYS,
        "highlights": [highlight.model_dump(mode="json") for highlight in highlights],
    }


def _last_success(runs: list[dict], now: datetime) -> str | None:
    """最近一次非失败运行的时间（ISO 字符串），无记录时为 `None`。"""
    from .parse import parse_published

    latest: datetime | None = None
    for run in runs:
        if run.get("overall") not in ("success", "no_new"):
            continue
        started_at, _ = parse_published(run.get("started_at"), now)
        if started_at is not None and (latest is None or started_at > latest):
            latest = started_at
    return latest.isoformat(timespec="seconds") if latest else None