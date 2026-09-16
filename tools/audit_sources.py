#!/usr/bin/env python3
"""源站审计工具：一次性核对全部源的可用性、字段质量与时间可信度。

这是提交材料里的「验证记录」生成器。它回答四个问题：

1. **可达性** —— 每个源当前是 200 还是挂了；
2. **字段质量** —— 条目数、标题/链接/摘要/日期的实际覆盖率；
3. **时间可信度** —— 源站声称的发布时间是否晚于真实当下（feed 不可能预知未来，
   任何未来时间都说明源站的时区标注有问题，应配置 `declared_utc_offset_minutes` 更正）；
4. **格式概览** —— 实际出现的日期格式与摘要字段长度。

用法:

    python tools/audit_sources.py                 # 审计全部源
    python tools/audit_sources.py --only qbitai   # 只看指定源
    python tools/audit_sources.py --json out.json # 同时输出机器可读结果
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import adapters, config, fetch
from pipeline.parse import parse_published

# 未来时间超过该小时数即判定为源站时区标注错误，而不是「时钟轻微超前」。
TZ_ANOMALY_HOURS = 4
# 判定为「疑似北京时间被标成其他时区」的偏移容差（小时）。
BEIJING_OFFSET_HOURS = 8

_DATE_SHAPE = re.compile(r"[+-]\d{4}$|\bGMT\b|\bUTC\b|[+-]\d{2}:\d{2}$")


def _display_width(text: str) -> int:
    """按东亚字符宽度计算显示宽度，保证中英混排表格不错位。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _date_shape(raw: str | None) -> str:
    """提取日期字符串的格式特征，用于确认覆盖了实测到的所有格式。"""
    if not raw:
        return "(无)"
    if _DATE_SHAPE.search(raw):
        return _DATE_SHAPE.search(raw).group(0)  # type: ignore[union-attr]
    if "T" in raw:
        return "ISO8601"
    return "(未知)"


def audit_source(source: config.SourceConfig, now: datetime) -> dict:
    """审计单个源，返回结构化结果。"""
    record: dict = {
        "id": source.id,
        "name": source.name,
        "lang": source.lang,
        "kind": source.kind,
        "url": source.url,
    }
    started = time.perf_counter()
    fetcher = fetch.Fetcher()
    try:
        fetcher.bind(source.id) if hasattr(fetcher, "bind") else None
        result = adapters.collect_source(source, fetcher, now)
    except Exception as exc:  # noqa: BLE001
        record.update(
            reachable=False,
            error=f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return record
    finally:
        fetcher.close()

    record["duration_ms"] = int((time.perf_counter() - started) * 1000)
    record.update(reachable=True, bozo=result.bozo, entries=result.seen)

    with_title = sum(1 for item in result.items if item.title.strip())
    with_url = sum(1 for item in result.items if (item.url or "").strip())
    summaries = [len(item.summary_raw or "") for item in result.items]
    record.update(
        with_title=with_title,
        with_url=with_url,
        with_summary=sum(1 for length in summaries if length > 0),
        summary_min=min(summaries) if summaries else 0,
        summary_max=max(summaries) if summaries else 0,
        heat_field=result.items[0].heat if result.items and result.items[0].heat is not None else None,
    )

    shapes: dict[str, int] = {}
    moments: list[datetime] = []
    for item in result.items:
        shape = _date_shape(item.published_raw)
        shapes[shape] = shapes.get(shape, 0) + 1
        # 审计时不受「未来时间」限制，否则恰恰会漏掉要查的那批数据。
        moment, _ = parse_published(item.published_raw, datetime(2100, 1, 1, tzinfo=timezone.utc))
        if moment is not None:
            moments.append(moment)

    record["date_shapes"] = shapes
    if moments:
        newest = max(moments)
        delta = (newest - now).total_seconds() / 3600
        record["newest_published"] = newest.isoformat()
        record["newest_ahead_hours"] = round(delta, 2)
        if delta > TZ_ANOMALY_HOURS:
            record["time_verdict"] = "tz_mislabelled"
            if abs(delta - BEIJING_OFFSET_HOURS) < 1.5:
                record["suggested_offset_minutes"] = 480
        elif delta > 0:
            record["time_verdict"] = "slightly_ahead"
        else:
            record["time_verdict"] = "ok"
    else:
        record["time_verdict"] = "no_dates"
    return record


def render(records: list[dict], now: datetime) -> str:
    """渲染人类可读的审计报告。"""
    lines = [
        "=" * 96,
        "  源站审计报告",
        f"  审计时刻(UTC) {now:%Y-%m-%d %H:%M:%S}   等价北京时间 {now.astimezone():%H:%M:%S}",
        "=" * 96,
        "",
        f"{_pad('源', 20)}{_pad('状态', 10)}{'条目':>5}{'标题':>6}{'链接':>6}{'摘要':>6}"
        f"{'日期格式':>12}{'超前':>9}  判定",
        "-" * 96,
    ]
    for record in records:
        if not record.get("reachable"):
            lines.append(
                f"{_pad(record['name'], 20)}{_pad('不可达', 10)}{'':>5}{'':>6}{'':>6}{'':>6}"
                f"{'':>12}{'':>9}  {record.get('error', '')[:40]}"
            )
            continue
        verdict = {
            "ok": "正常",
            "slightly_ahead": "轻微超前",
            "tz_mislabelled": "时区标注错误",
            "no_dates": "无可用时间",
        }.get(record["time_verdict"], record["time_verdict"])
        ahead = record.get("newest_ahead_hours")
        ahead_text = f"{ahead:+.1f}h" if ahead is not None else "-"
        shapes = ",".join(sorted(record["date_shapes"])) or "-"
        lines.append(
            f"{_pad(record['name'], 20)}{_pad('正常', 10)}"
            f"{record['entries']:>5}{record['with_title']:>6}{record['with_url']:>6}"
            f"{record['with_summary']:>6}{shapes:>12}{ahead_text:>9}  {verdict}"
            + (f" (建议 offset {record['suggested_offset_minutes']} 分钟)"
               if "suggested_offset_minutes" in record else "")
        )
    lines.append("-" * 96)
    reachable = sum(1 for record in records if record.get("reachable"))
    mislabelled = [record["name"] for record in records
                   if record.get("time_verdict") == "tz_mislabelled"]
    lines.append(f"合计 {len(records)} 个源，可达 {reachable} 个。")
    if mislabelled:
        lines.append(
            "时区标注错误（需在 config.SOURCES 中配置 declared_utc_offset_minutes）："
            + "、".join(mislabelled)
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(prog="audit_sources.py", description="源站审计")
    parser.add_argument("--only", default=None, help="逗号分隔的源 id")
    parser.add_argument("--json", default=None, metavar="PATH", help="同时写出 JSON 结果")
    args = parser.parse_args(argv)

    if args.only:
        wanted = [part.strip() for part in args.only.split(",") if part.strip()]
        sources = [config.SOURCES_BY_ID[wid] for wid in wanted if wid in config.SOURCES_BY_ID]
    else:
        sources = list(config.SOURCES)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    records = [audit_source(source, now) for source in sources]
    print(render(records, now))

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"audited_at": now.isoformat(), "sources": records}, ensure_ascii=False, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\n已写出 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())