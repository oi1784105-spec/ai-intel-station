#!/usr/bin/env python3
"""把 `web/index.html` 与 `data/*.json` 打包成**单个自包含 HTML**。

为什么需要它：`web/index.html` 通过 `fetch('data/news.json')` 取数，浏览器会因同源策略
拒绝 `file://` 下的这类请求，双击打开就是白屏。把数据内联进 HTML 后，单文件既能双击打开，
也能直接发给面试官，还能原样丢到 GitHub Pages。

产物:

- `dist/index.html` —— 单文件，数据内联（提交与双击用）
- `dist/data/*.json` —— 同时拷贝数据（`dist/` 整体作为静态站目录时，走 HTTP 也能取数）

用法:

    python build_site.py            # 构建
    python build_site.py --check    # 构建后校验自包含性
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

#: 与 `web/index.html` 中的常量完全一致，改一处必须改两处 —— 由 --check 兜住。
PLACEHOLDER = "/*__INLINE_DATA__*/ null"
DATA_FILES = (
    ("news", "news.json"),
    ("status", "update-status.json"),
    ("meta", "meta.json"),
    ("runs", "runs.json"),
)
#: 指向本地文件的资源引用，出现即说明产物不再自包含。
_EXTERNAL_RE = re.compile(r"""<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*["'](?!https?:|data:|//)""", re.I)


def _load_payload(data_dir: Path) -> tuple[dict, list[str]]:
    """读取全部数据文件。缺失的附属文件按 `None` 处理，但 `news.json` 必需。"""
    payload: dict = {}
    warnings: list[str] = []
    for key, name in DATA_FILES:
        path = data_dir / name
        if not path.exists():
            payload[key] = None
            warnings.append(f"缺少 {path}（前端会以最小可用模式渲染）")
            continue
        payload[key] = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("news") is None:
        raise SystemExit(f"缺少必需的数据文件 {data_dir / 'news.json'}，请先运行 python collect.py")
    return payload, warnings


def _inline_json(payload: dict) -> str:
    """序列化并转义，使其可安全嵌入 `<script>` 块。

    JSON 字符串里出现 `</script>` 会提前闭合脚本块；把 `</` 写成 `<\\/` 是 JSON 允许的
    转义，既不改变解析结果，也消除了闭合风险。
    """
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return blob.replace("</", "<\\/")


def build(
    template_path: Path, data_dir: Path, out_dir: Path
) -> tuple[Path, int, list[str]]:
    """构建单文件站点，返回 `(产物路径, 字节数, 警告列表)`。"""
    template = template_path.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise SystemExit(
            f"模板 {template_path} 缺少内联占位符 {PLACEHOLDER}；"
            "若前端常量被改名，请同步本文件的 PLACEHOLDER"
        )

    payload, warnings = _load_payload(data_dir)
    article_count = len((payload["news"] or {}).get("articles") or [])
    if article_count == 0:
        warnings.append("内联的条目数为 0，页面会是空的")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(
        template.replace(PLACEHOLDER, _inline_json(payload)), encoding="utf-8", newline="\n"
    )

    for _key, name in DATA_FILES:
        source = data_dir / name
        if source.exists():
            (out_dir / "data").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, out_dir / "data" / name)

    return out_path, out_path.stat().st_size, warnings


def check(out_path: Path, template_path: Path) -> list[str]:
    """校验产物确实自包含。自包含被破坏时前端会在面试现场白屏，必须机械校验。"""
    html = out_path.read_text(encoding="utf-8")
    problems: list[str] = []
    if PLACEHOLDER in html:
        problems.append("占位符未被替换，数据没有内联")
    if '"articles":' not in html:
        problems.append("产物中找不到 article 数据，内联可能失败")
    external = _EXTERNAL_RE.search(html)
    if external:
        problems.append(f"存在指向本地文件的资源引用，产物不自包含：{external.group(0)[:60]}")
    # 数据已内联，离线续跑不应再触发网络取数；这里确认兜底代码仍在，供 dist 走 HTTP 时使用。
    if "fetch(p" not in html and "fetch(" not in html:
        problems.append("产物缺少 HTTP 取数兜底代码（dist 作为静态站目录时会取不到数据）")
    if not template_path.exists():
        problems.append(f"模板不存在：{template_path}")
    return problems


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(prog="build_site.py", description="构建单文件站点")
    parser.add_argument("--template", default=str(ROOT / "web" / "index.html"))
    parser.add_argument("--data", default=str(ROOT / "data"))
    parser.add_argument("--out", default=str(ROOT / "dist"))
    parser.add_argument("--check", action="store_true", help="构建后校验自包含性")
    args = parser.parse_args(argv)

    out_path, size, warnings = build(Path(args.template), Path(args.data), Path(args.out))
    print(f"已生成 {out_path}（{size / 1024:.1f} KB）")
    for warning in warnings:
        print(f"  提示：{warning}")

    if args.check:
        problems = check(out_path, Path(args.template))
        if problems:
            print("自包含校验未通过：")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("自包含校验通过：单文件无外部本地资源引用，数据已内联。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())