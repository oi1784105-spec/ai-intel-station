#!/usr/bin/env python3
"""抓取真实响应并存为离线重放样本，同时生成可核对的来源清单。

为什么需要离线样本：

1. **验收可复现** —— `python collect.py --input tests/fixtures/replay --now <固定时刻>`
   在无网络环境下跑出**逐字节确定**的结果，评审在任何机器上都能复现同一份输出；
2. **回归可定位** —— 抽取的样本覆盖了实测到的全部边界：非法 XML、`bozo` 容错、
   4 种日期格式、纯导航摘要、同源陈旧停更。这些边界一旦回归，单元测试立刻失败；
3. **来源可核对** —— `manifest.json` 记录每个样本的 URL、HTTP 状态、字节数与 SHA256，
   使「这份数据真的来自那个源」可以被独立验证，而不是靠自述。

用法:

    python tools/capture_fixtures.py                 # 抓取全部样本源
    python tools/capture_fixtures.py --only qbitai
    python tools/capture_fixtures.py --trim-items 25 # arxiv 只保留前 25 条（控制体积）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config, fetch

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "tests" / "fixtures" / "replay"

#: 进入离线样本集的源。
#: - 覆盖全部**生产源**，使 `--input` 重放能跑通完整链路而不是只跑一部分；
#: - `venturebeat-ai` 实测最新条目停在 2026-08-27，是「疑似停更」判定的真实样本；
#: - `hn` 是唯一的 JSON 端点，验证非 feed 适配器；
#: - `arxiv` 覆盖 `-0400` 时区与超大响应裁剪。
FIXTURE_SOURCES: tuple[str, ...] = tuple(
    source.id for source in config.SOURCES
) + ("venturebeat-ai",)

#: 需要裁剪 item 数量的源。样本体积必须可控（真题要求「依赖越少越好」，
#: 仓库也不该被几 MB 的 feed 撑爆），裁剪后格式与字段完全不变，
#: 解析路径的覆盖面不受影响。
TRIM_ITEMS: dict[str, int] = {
    "arxiv": 25, "leiphone": 8, "ifanr": 8, "tmtpost": 8,
    "oschina": 20, "openai": 15, "simonw": 15,
}

_EXTENSIONS = {"hn": ".json", "arxiv": ".xml"}
_MIME = {"hn": "application/json", "arxiv": "application/rss+xml"}


def _extension(source: config.SourceConfig) -> str:
    return _EXTENSIONS.get(source.kind, ".xml")


def _trim(content: bytes, kind: str, limit: int) -> bytes:
    """只保留前 `limit` 个 `<item>`，并把原始条数记进注释。"""
    text = content.decode("utf-8", errors="replace")
    parts = text.split("<item>")
    if len(parts) <= 1:
        return content
    head, items = parts[0], parts[1:]
    kept = items[:limit]
    return (head + "<item>".join([""] + kept)).encode("utf-8")


def capture(
    source: config.SourceConfig, out_dir: Path, now: datetime
) -> dict:
    """抓取单个源并落盘，返回 manifest 条目。"""
    fetcher = fetch.Fetcher()
    record: dict = {"id": source.id, "name": source.name, "url": source.url,
                    "fetched_at": now.isoformat(), "kind": source.kind}
    try:
        if source.kind == "hn":
            # HN 适配器一次发两个请求，样本只取高信号轨即可覆盖解析路径。
            payload = fetcher.get_json(
                f"{source.url}?query=AI&tags=story&hitsPerPage=20"
                "&numericFilters=points%3E80"
            )
            content = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
            record["http_status"] = 200
        else:
            result = fetcher.get(source.url)
            content = result.content
            record["http_status"] = result.status
        trim = TRIM_ITEMS.get(source.id)
        if trim:
            original = content
            content = _trim(content, source.kind, trim)
            record["trimmed_to_items"] = trim
            record["original_bytes"] = len(original)
    except Exception as exc:  # noqa: BLE001
        record["ok"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record
    finally:
        fetcher.close()

    path = out_dir / f"{source.id}{_extension(source)}"
    path.write_bytes(content)
    record.update(
        ok=True,
        file=path.name,
        bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content_type=_MIME.get(source.kind, "application/rss+xml"),
    )
    return record


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(prog="capture_fixtures.py", description="抓取离线重放样本")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--only", default=None, help="逗号分隔的源 id")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    wanted = FIXTURE_SOURCES
    if args.only:
        wanted = tuple(part.strip() for part in args.only.split(",") if part.strip())

    now = datetime.now(timezone.utc).replace(microsecond=0)
    records: list[dict] = []
    for source_id in wanted:
        source = config.SOURCES_BY_ID.get(source_id)
        if source is None:
            print(f"跳过未知源 {source_id}")
            continue
        record = capture(source, out_dir, now)
        records.append(record)
        if record.get("ok"):
            note = f"（裁剪至 {record['trimmed_to_items']} 条）" if "trimmed_to_items" in record else ""
            print(f"  {record['name']:<18} {record['bytes']:>8} 字节  "
                  f"HTTP {record['http_status']}  {record['file']}{note}")
        else:
            print(f"  {record['name']:<18} 失败：{record['error']}")

    manifest_path = out_dir / "manifest.json"
    previous: dict = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    merged = {entry["id"]: entry for entry in previous.get("sources", [])}
    for record in records:
        merged[record["id"]] = record

    manifest_path.write_text(
        json.dumps(
            {
                "note": (
                    "离线重放样本清单。每个样本的 sha256 可用于核对内容未被改写。"
                    "这些样本取自真实响应，是 collect.py --input 的输入。"
                ),
                "generated_at": now.isoformat(),
                "sources": [merged[key] for key in sorted(merged)],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n已写出 {manifest_path}")
    return 0 if all(record.get("ok") for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())