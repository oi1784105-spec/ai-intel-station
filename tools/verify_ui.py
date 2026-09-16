"""无头渲染 + 交互校验：临时文件，跑完即删。

做法：把一段测试脚本追加到 `dist/index.html` 的副本末尾，让浏览器**真的去点击**
筛选标签、来源、语言徽标与主题按钮，把断言结果写进 DOM，再用 `--dump-dom` 读回来。
比只看静态 DOM 强的地方在于：能验证「点一下到底有没有筛出正确子集」。

不放进 tests/ 的原因：它需要已构建的 dist 与无头 Chrome，不属于 pytest 的单元测试范畴。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "index.html"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

HARNESS = r"""
<script>
(async function () {
  const out = [];
  const ok = (name, pass, extra) => out.push([name, !!pass, extra == null ? "" : String(extra)]);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms || 250));
  await wait(600);

  const cards = () => document.querySelectorAll("#cards li.card").length;
  const thumbs = () => document.querySelectorAll("#cards li.card .thumb img").length;
  const places = () => document.querySelectorAll("#cards li.card .thumb.ph").length;
  const af = () => document.getElementById("activeFilters");
  const articles = INLINE_DATA.news.articles || [];
  const counts = INLINE_DATA.news.counts || {};

  ok("卡片数 = 数据条数", cards() === articles.length, cards() + " / " + articles.length);
  ok("已渲染配图数 = counts.with_image", thumbs() === counts.with_image,
     thumbs() + " / " + counts.with_image);
  ok("占位块数 = 总数 - 配图数", places() === articles.length - counts.with_image,
     places() + " / " + (articles.length - counts.with_image));
  ok("配图卡 + 占位卡 = 全部卡片", thumbs() + places() === cards(), thumbs() + places());
  ok("每张卡片都有语言标签",
     document.querySelectorAll("#cards li.card .card-foot .lang").length === cards());
  ok("卡片源名全为英文（无中文字符）",
     [...document.querySelectorAll("#cards .src .nm")].every((n) => !/[\u4e00-\u9fff]/.test(n.textContent)));
  ok("源表源名全为英文",
     [...document.querySelectorAll("#srcBody td button.chip")].every((n) => !/[\u4e00-\u9fff]/.test(n.textContent)));
  ok("重点区源名为英文",
     [...document.querySelectorAll("#highlights .hl-src")].every((n) => !/[\u4e00-\u9fff]/.test(n.textContent)));
  ok("界面文案仍为中文", (document.querySelector("h1") || {}).textContent === "每日 AI 情报站");
  ok("初始无筛选指示条", af().hidden === true);
  ok("主题已显式设置",
     ["dark", "light"].includes(document.documentElement.getAttribute("data-theme")),
     document.documentElement.getAttribute("data-theme"));
  ok("无数据加载错误框", !document.querySelector(".err"));

  // 健康度指标：标签与数值的口径必须一致（曾把官方源数标成「覆盖来源」）
  const pills = [...document.querySelectorAll("#health .pill")].map((n) => n.textContent);
  ok("健康度无与筛选条重复的「近 24 小时」", !pills.some((t) => t.includes("近 24 小时")),
     pills.join(" / "));
  ok("「官方源」胶囊数值 = counts.official",
     pills.some((t) => t.includes("官方源") && t.includes(String(counts.official))));

  // 宽屏布局：重点区排成两列（末尾允许空半格 —— 这是网格排布的常规形态）
  ok("校验视口已达宽屏断点（≥1024px）", window.innerWidth >= 1024, window.innerWidth + "px");
  const hlBoxes = [...document.querySelectorAll("#highlights .hl")];
  if (hlBoxes.length >= 2 && window.innerWidth >= 1024) {
    const firstTop = hlBoxes[0].getBoundingClientRect().top;
    const secondTop = hlBoxes[1].getBoundingClientRect().top;
    ok("重点区宽屏下排成两列", Math.abs(firstTop - secondTop) < 4,
       Math.round(firstTop) + " / " + Math.round(secondTop));
    const widths = new Set(hlBoxes.map((n) => Math.round(n.getBoundingClientRect().width)));
    ok("重点条目宽度一致（无跨列拉伸）", widths.size === 1, [...widths].join(", "));
  }

  const full = cards();
  const byUrl = new Map(articles.map((a) => [a.url, a]));
  const visible = () => [...document.querySelectorAll("#cards li.card h3 a")].map((a) => a.getAttribute("href"));

  // 交互 1：点「今日重点」里的跨源理由标签 —— 应锁定到同一条事件
  const why = document.querySelector("#highlights button.why");
  if (why) {
    const label = why.textContent;
    why.click(); await wait();
    ok("点重点区理由标签可筛选", cards() > 0 && cards() <= full, label + " → " + cards());
    ok("筛选后指示条出现且含该条件", af().hidden === false && af().textContent.includes(label));
    ok("跨源标签锁定到同一事件簇", (() => {
      const ids = new Set(visible().map((u) => (byUrl.get(u) || {}).cluster_id));
      return ids.size === 1 && !ids.has(undefined) && !ids.has(null);
    })(), [...new Set(visible().map((u) => (byUrl.get(u) || {}).cluster_id))].join(","));
    document.querySelector(".af-clear").click(); await wait();
    ok("清除全部后恢复全量", cards() === full, cards() + " / " + full);
    ok("清除后指示条隐藏", af().hidden === true);
  } else {
    ok("重点区存在可点理由标签", false, "未找到 button.why");
  }

  // 交互 1b：点信号类理由（`涉及X` / `命中多重信号：X`）—— 应精确按该理由筛
  const sigChip = [...document.querySelectorAll("#highlights button.why")]
    .find((n) => /^(涉及|命中多重信号：)/.test(n.textContent));
  if (sigChip) {
    const label = sigChip.textContent;
    sigChip.click(); await wait();
    ok("点信号类理由精确筛选", cards() > 0 && af().textContent.includes(label),
       label + " → " + cards() + " 条");
    ok("信号筛选结果条条命中该理由",
       visible().length > 0 && visible().every((u) => ((byUrl.get(u) || {}).reasons || []).includes(label)));
    document.querySelector(".af-clear").click(); await wait();
    ok("清除信号筛选恢复全量", cards() === full);
  } else {
    ok("重点区存在可点信号类理由", false, "未找到信号类 button.why");
  }

  // 交互 2：点重点区来源名 —— 应只剩该来源
  const hlSrc = document.querySelector("#highlights .hl-src");
  if (hlSrc) {
    const name = hlSrc.textContent;
    hlSrc.click(); await wait();
    const same = [...document.querySelectorAll("#cards .src .nm")].every((n) => n.textContent === name);
    ok("点重点区来源名只显示该源", cards() > 0 && same, name + " → " + cards());
    document.querySelector(".af-clear").click(); await wait();
  } else {
    ok("重点区存在可点来源名", false, "未找到 .hl-src");
  }

  // 交互 3：点源表来源 —— 应只剩该来源
  const srcChip = document.querySelector("#srcBody td button.chip");
  const srcName = srcChip.textContent;
  srcChip.click(); await wait();
  const onlyOne = new Set([...document.querySelectorAll("#cards .src .nm")].map((n) => n.textContent));
  ok("点源表来源只显示该源", cards() > 0 && onlyOne.size === 1 && onlyOne.has(srcName),
     srcName + " → " + cards() + " 条");
  document.querySelector(".af-clear").click(); await wait();

  // 交互 4：点卡片语言徽标 —— 应只剩该语言
  const langBtn = document.querySelector("#cards li.card .card-foot button.lang");
  const langTxt = langBtn.textContent;
  langBtn.click(); await wait();
  const langBadges = new Set([...document.querySelectorAll("#cards li.card .card-foot .lang")].map((n) => n.textContent));
  ok("点语言标签只显示该语言", cards() > 0 && langBadges.size === 1 && langBadges.has(langTxt),
     langTxt + " → " + cards() + " 条");
  ok("语言筛选写入指示条", af().textContent.includes("中文") || af().textContent.includes("英文"),
     af().textContent);
  document.querySelector(".af-clear").click(); await wait();
  ok("清除后回到全量", cards() === full, cards() + " / " + full);

  // 交互 5：搜索 + 一键清除
  const q = document.getElementById("q");
  q.value = "GPT";
  q.dispatchEvent(new Event("input"));
  await wait();
  ok("搜索生效且为子集", cards() > 0 && cards() <= full, "GPT → " + cards());
  ok("搜索条件进入指示条", af().textContent.includes("GPT"));
  document.querySelector(".af-clear").click(); await wait();
  ok("清除搜索恢复全量", cards() === full && q.value === "", cards() + " / " + full);

  // 交互 6：主题切换
  const t0 = document.documentElement.getAttribute("data-theme");
  document.getElementById("themeToggle").click(); await wait();
  const t1 = document.documentElement.getAttribute("data-theme");
  ok("主题可切换", t0 !== t1, t0 + " → " + t1);
  document.getElementById("themeToggle").click(); await wait();
  ok("主题可切回", document.documentElement.getAttribute("data-theme") === t0, t1 + " → " + t0);

  // 结构：缩略图盒子尺寸固定，卡片因此等高
  const sizes = new Set([...document.querySelectorAll("#cards li.card .thumb")]
    .map((n) => n.getBoundingClientRect().width.toFixed(0) + "x" + n.getBoundingClientRect().height.toFixed(0)));
  ok("所有缩略图盒子尺寸一致", sizes.size === 1, [...sizes].join(", "));

  // 标题截断：判据不能是 `scrollHeight <= clientHeight` —— 被 line-clamp 截断的元素，
  // 其 scrollHeight 本来就是完整内容高度、大于 clientHeight，那样写会把「截断成功」
  // 判成失败。正确的判据是「确实应用了 line-clamp，且渲染高度不超过两行」。
  const h3s = [...document.querySelectorAll("#cards li.card h3")];
  const clampApplied = h3s.every((n) => {
    const cs = getComputedStyle(n);
    return cs.overflow === "hidden"
      && (cs.getPropertyValue("-webkit-line-clamp") || cs.webkitLineClamp) === "2";
  });
  ok("标题应用了 2 行 line-clamp", clampApplied && h3s.length > 0, h3s.length + " 个标题");
  const lineH = parseFloat(getComputedStyle(h3s[0]).lineHeight) || 22;
  const tallest = Math.max(...h3s.map((n) => n.getBoundingClientRect().height));
  ok("没有标题渲染超过两行", tallest <= lineH * 2 + 3,
     Math.round(tallest) + "px ≤ " + Math.round(lineH * 2 + 3) + "px");
  const ps = [...document.querySelectorAll("#cards li.card p")];
  const tallestP = ps.length ? Math.max(...ps.map((n) => n.getBoundingClientRect().height)) : 0;
  ok("没有摘要渲染超过两行", tallestP <= lineH * 2 + 3,
     Math.round(tallestP) + "px ≤ " + Math.round(lineH * 2 + 3) + "px");

  const pre = document.createElement("pre");
  pre.id = "TESTREPORT";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})().catch((error) => {
  const pre = document.createElement("pre");
  pre.id = "TESTREPORT";
  pre.textContent = JSON.stringify([["harness crashed", false, String(error && error.message || error)]]);
  document.body.appendChild(pre);
});
</script>
"""


def main() -> int:
    html = DIST.read_text(encoding="utf-8")
    marker = "</body>"
    if marker not in html:
        print("dist/index.html 里找不到 </body>")
        return 2
    # 带插桩的副本写到系统临时目录，跑完即删 —— 不在仓库里留任何残留物。
    # （之前写在 build/ 下，属被 gitignore 的目录但仍会堆积，且文档声称「跑完即删」却不符。）
    scratch_dir = Path(tempfile.mkdtemp(prefix="hermes-verify-ui-"))
    scratch = scratch_dir / "index.html"
    try:
        scratch.write_text(html.replace(marker, HARNESS + marker), encoding="utf-8")
        proc = subprocess.run(
            [
                str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
                # 视口必须够宽，否则宽屏断点（两列重点区、三列卡片）根本不会生效，
                # 校验脚本就会在窄布局上「全部通过」而漏掉真正的排版缺陷。
                "--window-size=1500,1250",
                "--virtual-time-budget=15000", "--dump-dom", scratch.as_uri(),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        dom = proc.stdout or ""
        match = re.search(r'<pre id="TESTREPORT">(.*?)</pre>', dom, re.S)
        if not match:
            print("未取到测试报告；DOM 长度 =", len(dom))
            print((proc.stderr or "")[-2000:])
            return 1
        rows = json.loads(match.group(1))
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    failed = [row for row in rows if not row[1]]
    for name, passed, extra in rows:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}" + (f"   [{extra}]" if extra else ""))
    print(f"\n{len(rows) - len(failed)} / {len(rows)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())