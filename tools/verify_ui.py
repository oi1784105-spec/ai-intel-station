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

  const hlIds = new Set(((INLINE_DATA.meta && INLINE_DATA.meta.highlights) || []).map((h) => h.article_id));
  const byId = new Map(articles.map((a) => [a.id, a]));
  const expectedZh = articles.filter((a) => a.lang === "zh" && !hlIds.has(a.id)).length;

  // 默认状态（默认语言中文 + 不在列表里重复重点条目）
  ok("默认只看中文，不再中英混排", cards() === expectedZh && cards() < articles.length,
     cards() + " / 中文 " + expectedZh + "，全量 " + articles.length);
  ok("默认筛选为中文且写入指示条", af().hidden === false && af().textContent.includes("中文"),
     af().textContent);
  ok("控制条有中/EN 切换胶囊",
     [...document.querySelectorAll("#filters button.chip")]
       .map((n) => n.textContent).some((t) => t.startsWith("中文"))
     && [...document.querySelectorAll("#filters button.chip")]
       .map((n) => n.textContent).some((t) => t.startsWith("英文")));
  ok("中文胶囊为默认选中态",
     (document.querySelector("#filters button.chip[aria-pressed='true']") || {}).textContent
       ?.startsWith("中文"));
  ok("默认不在列表里重复「今日重点」的条目",
     [...document.querySelectorAll("#cards li.card")].every((c) => !hlIds.has(c.dataset.id)));
  ok("汇总行说明了略过了多少条",
     document.getElementById("summaryLine").textContent.includes("已略过"),
     document.getElementById("summaryLine").textContent);

  // 字体令牌与正文字号：这三个变量只要漏声明一个，用到它的 font 简写就会在
  // 计算期整条失效 —— 字号偷偷回落到浏览器默认 16px、衬线/等宽层级塌成一种，
  // 页面上不报任何错，肉眼极易放过。所以必须问浏览器要「计算后的值」。
  const rootCS = getComputedStyle(document.documentElement);
  const fontVars = ["--serif", "--mono", "--sans"]
    .map((v) => rootCS.getPropertyValue(v).trim());
  ok("字体令牌 --serif/--mono/--sans 均已声明",
     fontVars.every((v) => v.length > 0),
     fontVars.map((v) => (v ? "已声明" : "<<未定义>>")).join(" / "));
  const famOf = (sel) => {
    const e = document.querySelector(sel);
    return e ? getComputedStyle(e).fontFamily : "<<缺元素 " + sel + ">>";
  };
  const fams = [famOf("body"), famOf(".brand-name"), famOf(".edition")];
  ok("正文 / 标题 / 元数据三层字体彼此不同（无衬线·衬线·等宽）",
     new Set(fams).size === 3,
     fams.map((f) => f.split(",")[0]).join("  |  "));
  ok("正文计算字号为设计值 13px（font 简写未失效）",
     getComputedStyle(document.body).fontSize === "13px",
     getComputedStyle(document.body).fontSize);
  // 通用护栏：把页面样式表里所有 var(--x) 引用与所有 --x 声明对一遍。
  // 只要有一个变量「被引用但未声明」，用到它的属性就会在计算期整条失效，
  // 而浏览器不报任何错 —— 字体那次就是这样静默丢掉的。
  const usedVars = new Set();
  const declaredVars = new Set();
  const collect = (ruleList) => {
    for (const r of ruleList) {
      if (r.style && r.style.length) {
        for (const prop of r.style) {
          if (prop.startsWith("--")) declaredVars.add(prop);
          const value = r.style.getPropertyValue(prop);
          const re = /var\(\s*(--[A-Za-z0-9_-]+)/g;
          let m;
          while ((m = re.exec(value))) usedVars.add(m[1]);
        }
      }
      if (r.cssRules) collect(r.cssRules);
    }
  };
  for (const sheet of document.styleSheets) {
    try { collect(sheet.cssRules); } catch (error) { /* 内联样式表不会跨源失败 */ }
  }
  const undeclared = [...usedVars]
    .filter((v) => !declaredVars.has(v) && v !== "--src-hue");   // --src-hue 由卡片内联注入，豁免
  ok("样式表里不存在「引用但未声明」的 CSS 变量",
     undeclared.length === 0,
     undeclared.length ? "未声明: " + undeclared.join(" ")
                       : "引用 " + usedVars.size + " 个变量，全部已声明");

  // 头条的硬条件：必须有配图。重点条目按分数降序，头条要取「有配图」中分数最高的一条，
  // 而不是简单取分数第一名 —— 只靠肉眼看图很容易放过（配图缺失时版面还会空一大块）。
  const hls = (INLINE_DATA.meta && INLINE_DATA.meta.highlights) || [];
  const hlImg = (h) => h.image_url || (byId.get(h.article_id) || {}).image_url;
  const hlTitle = (h) => h.title || (byId.get(h.article_id) || {}).title || "";
  const imgHls = hls.filter(hlImg).slice()
    .sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0));
  const heroEl = document.querySelector("#highlights .lead-hero");
  const heroImg = heroEl && heroEl.querySelector("img");
  ok("头条特稿带配图（配图是头条的硬条件）",
     imgHls.length === 0 ? true : !!heroImg,
     imgHls.length ? (heroImg ? "有配图 " + (heroImg.getAttribute("src") || "").slice(0, 44)
                              : "<<特稿内没有 <img>>>")
                   : "本次重点条目均无配图，按规则退回分数最高者");
  const heroTitle = (heroEl && heroEl.querySelector("h3 a") || {}).textContent || "";
  ok("头条是「有配图」里分数最高的那一条（不是简单取分数第一名）",
     imgHls.length === 0 ? true : heroTitle === hlTitle(imgHls[0]),
     imgHls.length ? "头条=「" + heroTitle.slice(0, 22) + "」 期望=「" + hlTitle(imgHls[0]).slice(0, 22) + "」"
                   : "本次无带配图重点");

  // 数字列右对齐：td.num 的样式早就写在 CSS 里，但行生成代码漏挂类名，规则从未生效。
  const numCell = document.querySelector("#srcBody td.num");
  ok("源表数字列右对齐（td.num 规则真的生效）",
     !!numCell && getComputedStyle(numCell).textAlign === "right",
     numCell ? "text-align=" + getComputedStyle(numCell).textAlign + " 值=" + numCell.textContent.trim()
             : "<<一行都没有挂 .num>>");

  // 右栏不留空洞：侧栏高度不靠「刚好有多少内容」决定，而是按特稿高度等分，
  // 条目被拉满、内容垂直居中，余量均匀落在每条上下 —— 否则会在右栏底部攒成一整块纯背景空洞。
  const leadSide = document.querySelector("#highlights .lead-side");
  const sideRect = leadSide && leadSide.getBoundingClientRect();
  const heroRect = heroEl && heroEl.getBoundingClientRect();
  const sideBlocks = leadSide ? [...leadSide.children] : [];
  const lastBlock = sideBlocks[sideBlocks.length - 1];
  const bottomVoid = sideRect && lastBlock
    ? Math.round(sideRect.bottom - lastBlock.getBoundingClientRect().bottom) : null;
  ok("重点区右栏底部不留空洞（拉满到与特稿齐平）",
     !!sideRect && !!heroRect && Math.abs(sideRect.height - heroRect.height) <= 1 && bottomVoid <= 4,
     "特稿高 " + Math.round(heroRect ? heroRect.height : 0) + " / 侧栏高 "
       + Math.round(sideRect ? sideRect.height : 0) + " / 底部空洞 " + bottomVoid + "px");

  // 未读必须「读得清」：正文对比度 ≥ 7:1（AAA）。看淡是「读过了」的特权，不是默认状态。
  const lum = (c) => {
    const v = c.match(/[\d.]+/g).slice(0, 3).map(Number)
      .map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  };
  const ratio = (a, b) => { const s = [lum(a), lum(b)].sort((p, q) => q - p);
    return +((s[0] + 0.05) / (s[1] + 0.05)).toFixed(2); };
  const contrastOf = () => {
    const c = document.querySelector("#cards li.card");
    const bg = getComputedStyle(document.querySelector(".cards")).backgroundColor;
    return { t: ratio(getComputedStyle(c.querySelector("h3")).color, bg),
             s: ratio(getComputedStyle(c.querySelector("p") || c).color, bg) };
  };
  const themeEl = document.documentElement;
  const prevTheme = themeEl.getAttribute("data-theme");
  const cDark = contrastOf();
  themeEl.setAttribute("data-theme", "light");
  const cLight = contrastOf();
  if (prevTheme) themeEl.setAttribute("data-theme", prevTheme);
  else themeEl.removeAttribute("data-theme");
  ok("未读卡片正文对比度 ≥ 7:1（深色 · 浅色）",
     Math.min(cDark.t, cDark.s, cLight.t, cLight.s) >= 7,
     "深色 标题 " + cDark.t + ":1 摘要 " + cDark.s + ":1 · 浅色 标题 " + cLight.t + ":1 摘要 " + cLight.s + ":1");

  // 已读淡出契约：未读是正常色，只有读过才淡 —— 顺序反了就成了「满屏灰」。
  const probeCard = document.querySelector("#cards li.card:not(.read)");
  const unreadOp = getComputedStyle(probeCard).opacity;
  probeCard.classList.add("read");
  const readOp = getComputedStyle(probeCard).opacity;
  probeCard.classList.remove("read");
  ok("未读卡片不淡 / 读过才淡出", unreadOp === "1" && parseFloat(readOp) < 1,
     "未读 opacity=" + unreadOp + " → 已读 opacity=" + readOp);

  const expectedImg = articles
    .filter((a) => a.lang === "zh" && !hlIds.has(a.id) && a.image_url).length;
  ok("已渲染配图数 = 当前列表内含图条目数", thumbs() === expectedImg,
     thumbs() + " / " + expectedImg);
  ok("占位块数 = 列表条数 - 配图数", places() === cards() - expectedImg,
     places() + " / " + (cards() - expectedImg));
  ok("配图卡 + 占位卡 = 全部卡片", thumbs() + places() === cards(), thumbs() + places());
  ok("每张卡片都有语言标签",
     document.querySelectorAll("#cards li.card .card-foot .lang").length === cards());

  // badge 收敛：脚注除语言外最多再给一个（其余收进悬浮提示）
  const feet = [...document.querySelectorAll("#cards li.card .card-foot")];
  ok("每卡 badge ≤ 1（语言徽标另计）",
     feet.every((f) => f.querySelectorAll(".badge").length <= 1),
     Math.max(...feet.map((f) => f.querySelectorAll(".badge").length)));
  ok("热度只以「HN 数字」形式出现，且仅限 HN",
     [...document.querySelectorAll("#cards li.card .card-foot .badge")]
       .filter((n) => /^\d+$/.test(n.textContent.trim()) || /HN\s/.test(n.textContent))
       .every((n) => /^HN \d+$/.test(n.textContent.trim())),
     [...document.querySelectorAll("#cards li.card .card-foot .badge")]
       .map((n) => n.textContent.trim()).filter((t) => /HN|^\d/.test(t)).slice(0, 5).join(" / "));
  // 判据只扫真正的徽标元素：不能扫 document.body.textContent —— 注入的校验脚本
  // 本身也是 body 的文本节点，扫全文会把断言里的字面量算成「页面上出现了」。
  ok("界面上不再出现裸「热度 N」徽标",
     [...document.querySelectorAll("#cards .card-foot .badge, #highlights .badge")]
       .every((n) => !/^热度/.test(n.textContent.trim())));

  ok("卡片源名全为英文（无中文字符）",
     [...document.querySelectorAll("#cards .src .nm")].every((n) => !/[\u4e00-\u9fff]/.test(n.textContent)));
  ok("源表源名全为英文",
     [...document.querySelectorAll("#srcBody td button.chip")].every((n) => !/[\u4e00-\u9fff]/.test(n.textContent)));
  ok("重点区源名为英文",
     [...document.querySelectorAll("#highlights .hl-src")].every((n) => !/[\u4e00-\u9fff]/.test(n.textContent)));
  ok("界面文案仍为中文", (document.querySelector("h1") || {}).textContent === "每日 AI 情报站");
  ok("主题已显式设置",
     ["dark", "light"].includes(document.documentElement.getAttribute("data-theme")),
     document.documentElement.getAttribute("data-theme"));
  ok("无数据加载错误框", !document.querySelector(".err"));

  // 健康度指标：标签与数值的口径必须一致（曾把官方源数标成「覆盖来源」）
  const pills = [...document.querySelectorAll("#health .pill")].map((n) => n.textContent);
  ok("健康度只保留三项（状态灯 / 最后更新 / 条目总数）", pills.length === 3,
     pills.join(" / "));
  ok("健康度含整体状态灯", pills.some((t) => t.includes("整体状态：")));
  ok("健康度含最后更新时间与条目总数",
     pills.some((t) => t.includes("最后更新")) && pills.some((t) => t.includes(String(counts.total))));
  ok("健康度不再暴露内部指标",
     !pills.some((t) => /成功率|含配图|官方源|发布时间未知|近 24 小时/.test(t)),
     pills.join(" / "));
  const dots = [...document.querySelectorAll("#health .dot")];
  ok("状态灯取值为 ok/warn/bad 之一",
     dots.length >= 1 && ["ok", "warn", "bad"].some((c) => dots[0].classList.contains(c)),
     dots.map((n) => n.className).join(","));
  ok("顶栏不再重复出现时间戳", !document.getElementById("stamp"));

  // 生成方式标注：哪些是规则算的、哪些是别人的原文，页脚必须自报家门
  const footText = document.getElementById("foot").textContent;
  ok("页脚标注评分/理由/标签为规则计算",
     footText.includes("本地规则计算") && footText.includes("重要度评分"), null);
  // 这一句是对外承诺，标点也算文案：整句字面单独钉住，避免「分数：理由」被悄悄改回顿号
  ok("页脚自报家门那句字面未走样（「重要度评分」：）",
     footText.includes("「重要度评分」：入选理由与主题标签均由本地规则计算"),
     footText.slice(0, 32));
  ok("页脚标注摘要取自来源原文且不改写",
     footText.includes("来源 feed 的原文摘要") && footText.includes("不改写"));
  ok("页脚声明不调用外部接口", footText.includes("不调用任何外部接口"));
  ok("页脚声明已读/主题只存本机", footText.includes("localStorage"));

  // 宽屏布局：重点区排成两列（末尾允许空半格 —— 这是网格排布的常规形态）
  ok("校验视口已达宽屏断点（≥1024px）", window.innerWidth >= 1024, window.innerWidth + "px");
  const hlBoxes = [...document.querySelectorAll("#highlights .hl")];
  if (hlBoxes.length >= 2 && window.innerWidth >= 1024) {
    const firstTop = hlBoxes[0].getBoundingClientRect().top;
    // 本轮按视觉稿把重点区从「等宽两列」改为「主编特稿（宽）+ 侧栏（窄）」的非对称版式，
    // 断言随之改成新规格：特稿在左且更宽，完整侧栏条目共用同一列且等宽。
    const hero = document.querySelector("#highlights .lead-hero.hl");
    const fullSides = [...document.querySelectorAll("#highlights .lead-side .hl:not(.compact)")];
    if (hero && fullSides.length) {
      const heroBox = hero.getBoundingClientRect();
      const sideBox = fullSides[0].getBoundingClientRect();
      ok("重点区顶行是「特稿 + 侧栏」并排", Math.abs(heroBox.top - sideBox.top) < 4,
         Math.round(heroBox.top) + " / " + Math.round(sideBox.top));
      ok("特稿在左、侧栏在右", heroBox.right <= sideBox.left + 1,
         Math.round(heroBox.right) + " ≤ " + Math.round(sideBox.left));
      ok("特稿比侧栏宽（不是等宽网格）", heroBox.width > sideBox.width + 1,
         Math.round(heroBox.width) + " > " + Math.round(sideBox.width));
      const sideWidths = new Set(fullSides.map((n) => Math.round(n.getBoundingClientRect().width)));
      const sideLefts = new Set(fullSides.map((n) => Math.round(n.getBoundingClientRect().left)));
      ok("完整侧栏条目共用一列且等宽（紧凑条目在侧栏内再分两小列）",
         sideWidths.size === 1 && sideLefts.size === 1,
         [...sideWidths].join(", ") + " / " + [...sideLefts].join(", "));
    }
  }

  // 「清除全部」之后的可见全量：全语言，且默认仍略过「今日重点」条目。
  // 注意不能写成 `full = cards()` —— 默认已是中文筛选，那样会把「恢复全量」的基准钉错。
  const full = articles.length - hlIds.size;
  const byUrl = new Map(articles.map((a) => [a.url, a]));
  const visible = () => [...document.querySelectorAll("#cards li.card h3 a")].map((a) => a.getAttribute("href"));

  // 上次来访与未读：无头 Chrome 每次都是全新 profile，初始必为「首次访问」
  const bar = () => document.getElementById("visitBar");
  ok("首次访问也显示访问条（功能可发现），但不显示「新增 0 篇」",
     bar().hidden === false && bar().textContent.includes("首次访问")
     && !bar().textContent.includes("新增 0 篇"), bar().textContent.trim());
  ok("首次访问即写入上次来访时间", !!localStorage.getItem("ai-intel-last-visit"));
  const firstCard = document.querySelector("#cards li.card");
  const firstId = firstCard.dataset.id;
  ok("初始卡片均为未读（无变淡）",
     document.querySelectorAll("#cards li.card.read").length === 0);
  const firstLink = firstCard.querySelector("h3 a");
  // 拦住默认跳转，只验证点击后的标记行为（否则会在无头环境里开新标签页）。
  firstLink.addEventListener("click", (event) => event.preventDefault(), { once: true });
  firstLink.click(); await wait();
  ok("点标题后该卡标记为已读（变淡）", firstCard.classList.contains("read"));
  ok("已读记录写入本机 localStorage",
     (JSON.parse(localStorage.getItem("ai-intel-read") || "[]")).includes(firstId));
  ok("访问条显示已读条数且注明只存本机",
     bar().hidden === false && bar().textContent.includes("已读 1 条")
     && bar().textContent.includes("本机浏览器"), bar().textContent.trim());
  const unreadChip = [...document.querySelectorAll("#visitBar button.chip")]
    .find((n) => n.textContent === "只看未读");
  unreadChip.click(); await wait();
  ok("「只看未读」滤掉已读条目，且列表内不再有变淡卡片",
     document.querySelectorAll("#cards li.card.read").length === 0
     && !visible().some((u) => (byUrl.get(u) || {}).id === firstId),
     cards() + " / " + full);
  ok("未读筛选进入指示条", af().textContent.includes("只看未读"), af().textContent);
  document.querySelector(".af-clear").click(); await wait();
  // 「清除全部」会连语言筛选一起清掉，所以这里回到的是**全量**而不是默认的中文列表。
  ok("清除「只看未读」后回到全量列表", cards() === full, cards() + " / " + full);

  // 交互 1：点「今日重点」里的**跨源理由**标签 —— 应锁定到同一条事件。
  // 这里必须按正则挑跨源标签，不能取「第一个」：头条一旦换成不带跨源理由的条目，
  // 首个标签就变成普通信号理由，断言会跟着误报（本轮就是这么暴露出来的）。
  const whyCross = [...document.querySelectorAll("#highlights button.why")]
    .find((n) => /独立来源/.test(n.textContent));
  const why = whyCross || document.querySelector("#highlights button.why");
  if (why) {
    const label = why.textContent;
    why.click(); await wait();
    ok("点重点区理由标签可筛选", cards() > 0 && cards() <= full, label + " → " + cards());
    ok("筛选后指示条出现且含该条件", af().hidden === false && af().textContent.includes(label));
    ok(whyCross ? "跨源标签锁定到同一事件簇" : "信号标签命中的条目条条带该理由", (() => {
      const rows = visible();
      if (!rows.length) return false;
      const ids = new Set(rows.map((u) => (byUrl.get(u) || {}).cluster_id));
      return whyCross
        ? (ids.size === 1 && !ids.has(undefined) && !ids.has(null))
        : rows.every((u) => ((byUrl.get(u) || {}).reasons || []).includes(label));
    })(), (whyCross ? "cluster=" : "理由=") +
         [...new Set(visible().map((u) => (byUrl.get(u) || {}).cluster_id))].join(","));
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

  // 交互 5b：跨源事件可追溯 —— 点脚注的跨源 badge 必须列出该事件全部来源链接，而不只是「N 个来源」
  const clusterChip = [...document.querySelectorAll("#filters button.chip")]
    .find((n) => n.textContent.startsWith("多来源报道"));
  if (clusterChip) {
    clusterChip.click(); await wait();
    const findClusterCard = () => [...document.querySelectorAll("#cards li.card")]
      .find((c) => (byId.get(c.dataset.id) || {}).cluster_id);
    let clusterCard = findClusterCard();
    // 跨源条目往往也在「今日重点」里，默认被列表略过；必要时临时打开重复显示把它放回来。
    if (!clusterCard) {
      const dup = document.getElementById("dupToggle");
      if (dup && !dup.checked) { dup.click(); await wait(); }
      clusterCard = findClusterCard();
    }
    ok("能筛出跨源事件卡片", !!clusterCard, clusterChip.textContent);
    if (clusterCard) {
      const clusterId = byId.get(clusterCard.dataset.id).cluster_id;
      const expectMembers = articles.filter((a) => a.cluster_id === clusterId).length;
      const badge = clusterCard.querySelector(".card-foot button.badge.cluster");
      ok("跨源 badge 是可点的按钮", !!badge, badge ? badge.textContent : "");
      badge.click(); await wait();
      ok("点跨源 badge 打开来源清单浮层", document.getElementById("modal").hidden === false);
      const rows = [...document.querySelectorAll("#modal .modal-item")];
      ok("清单列出该事件簇的全部成员（不再只是一个数字）",
         rows.length === expectMembers, rows.length + " / " + expectMembers);
      const hrefs = rows.map((r) => {
        const a = r.querySelector("a.mi-title");
        return a ? a.getAttribute("href") || "" : "";
      });
      ok("清单每条都带可点原文链接",
         rows.length > 0 && hrefs.every((h) => /^https?:\/\//.test(h)));
      const srcNames = rows.map((r) => (r.querySelector(".mi-src") || {}).textContent);
      ok("清单逐条标出来源名（才能核对是不是同一件事）",
         rows.length > 0 && new Set(srcNames).size === rows.length, srcNames.join(" / "));
      document.getElementById("modalClose").click(); await wait();
      ok("点关闭按钮可收起浮层", document.getElementById("modal").hidden === true);
      badge.click(); await wait();
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      await wait();
      ok("按 Esc 可收起浮层", document.getElementById("modal").hidden === true);
    }
    document.querySelector(".af-clear").click(); await wait();
    const dupReset = document.getElementById("dupToggle");
    if (dupReset && dupReset.checked) { dupReset.click(); await wait(); }
    // 此时筛选已是「全部」，所以基准是全量；这里验证的是关掉重复显示后重点条目仍被略过。
    ok("关掉重复显示后重点条目仍被略过（回到全量）", cards() === full, cards() + " / " + full);
  } else {
    ok("控制条存在「多来源报道」胶囊", false, "未找到");
  }

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

# 「回访」阶段要跨两次页面加载才测得出：第一次加载伪造「上次来访早于最早入库时间」
# 并把一条标为已读，第二次加载（同一个 --user-data-dir，localStorage 因此保留）再断言。
# 单次加载的 harness 无论怎么写都验证不了「刷新后仍保留」这件事。
RETURN_SEED = r"""
<script>
(async function () {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms || 300));
  await wait(600);
  const articles = INLINE_DATA.news.articles || [];
  const hlIds = new Set(((INLINE_DATA.meta && INLINE_DATA.meta.highlights) || []).map((h) => h.article_id));
  // 目标条目必须落在默认可见列表里（中文、且不是重点条目），第二次加载才能看到它变淡。
  const target = articles.filter((a) => a.lang === "zh" && !hlIds.has(a.id))[0];
  const stamps = articles.map((a) => Date.parse(a.first_seen_at || "") || Infinity);
  const earliest = Math.min(...stamps);
  // 伪造的「上次来访」早于最早入库时间一天 ⇒ 除已读那条外，全部条目都算「新增」，可精确核对。
  localStorage.setItem("ai-intel-last-visit", new Date(earliest - 86400000).toISOString());
  localStorage.setItem("ai-intel-read", JSON.stringify([target.id]));
  localStorage.setItem("ai-intel-return-expect", String(articles.length - 1));
  localStorage.setItem("ai-intel-return-target", target.id);
})().catch(() => {});
</script>
"""

RETURN_HARNESS = r"""
<script>
(async function () {
  const out = [];
  const ok = (name, pass, extra) => out.push([name, !!pass, extra == null ? "" : String(extra)]);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms || 250));
  await wait(900);

  const bar = document.getElementById("visitBar");
  const expect = Number(localStorage.getItem("ai-intel-return-expect") || "-1");
  const target = localStorage.getItem("ai-intel-return-target");
  const flat = bar.textContent.replace(/\s+/g, "");

  ok("回访时顶部显示「自上次来访新增 N 篇」",
     bar.hidden === false && bar.textContent.includes("自上次来访新增"), bar.textContent.trim());
  ok("新增条数与入库时间口径一致",
     flat.includes("新增" + expect + "篇"), "期望 " + expect + " 条 / " + flat);
  const dim = [...document.querySelectorAll("#cards li.card.read")];
  ok("已读状态在刷新后依然保留（卡片继续变淡）",
     dim.length === 1 && dim[0].dataset.id === target,
     dim.length + " 张变淡，目标 " + String(target).slice(0, 8));
  ok("回访时一并显示已读计数", bar.textContent.includes("已读 1 条"), flat);

  const pre = document.createElement("pre");
  pre.id = "TESTREPORT";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})().catch((error) => {
  const pre = document.createElement("pre");
  pre.id = "TESTREPORT";
  pre.textContent = JSON.stringify([["回访 harness 崩溃", false, String(error && error.message || error)]]);
  document.body.appendChild(pre);
});
</script>
"""


def _run_page(page: Path, profile: Path | None = None) -> tuple[str, str]:
    """跑一次无头 Chrome，返回 (dump 出的 DOM, stderr)。"""
    args = [
        str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
        # 视口必须够宽，否则宽屏断点（两列重点区、三列卡片）根本不会生效，
        # 校验脚本就会在窄布局上「全部通过」而漏掉真正的排版缺陷。
        "--window-size=1500,1250",
        "--virtual-time-budget=15000", "--dump-dom",
    ]
    if profile is not None:
        args.append("--user-data-dir=" + str(profile))
    args.append(page.as_uri())
    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    return proc.stdout or "", proc.stderr or ""


def _report(dom: str):
    match = re.search(r'<pre id="TESTREPORT">(.*?)</pre>', dom, re.S)
    return json.loads(match.group(1)) if match else None


def main() -> int:
    html = DIST.read_text(encoding="utf-8")
    marker = "</body>"
    if marker not in html:
        print("dist/index.html 里找不到 </body>")
        return 2
    # 带插桩的副本写到系统临时目录，跑完即删 —— 不在仓库里留任何残留物。
    # （之前写在 build/ 下，属被 gitignore 的目录但仍会堆积，且文档声称「跑完即删」却不符。）
    scratch_dir = Path(tempfile.mkdtemp(prefix="hermes-verify-ui-"))
    # 回访阶段的两次加载共用同一个 profile，localStorage 才留得下来；用完即删。
    profile = Path(tempfile.mkdtemp(prefix="hermes-verify-ui-profile-"))

    def stage(name: str, harness: str) -> Path:
        page = scratch_dir / name
        page.write_text(html.replace(marker, harness + marker), encoding="utf-8")
        return page

    try:
        dom, err = _run_page(stage("interactions.html", HARNESS))
        rows = _report(dom)
        if rows is None:
            print("未取到测试报告；DOM 长度 =", len(dom))
            print(err[-2000:])
            return 1

        # 第二段：回访。同一个 profile 连开两次页面，验证「刷新后仍保留」。
        _run_page(stage("return-seed.html", RETURN_SEED), profile=profile)
        dom2, err2 = _run_page(stage("return-check.html", RETURN_HARNESS), profile=profile)
        rows2 = _report(dom2)
        if rows2 is None:
            print("回访阶段未取到测试报告；DOM 长度 =", len(dom2))
            print(err2[-2000:])
            return 1
        rows += rows2
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)
        shutil.rmtree(profile, ignore_errors=True)

    failed = [row for row in rows if not row[1]]
    for name, passed, extra in rows:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}" + (f"   [{extra}]" if extra else ""))
    print(f"\n{len(rows) - len(failed)} / {len(rows)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())