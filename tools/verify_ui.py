"""无头渲染 + 交互校验：临时文件，跑完即删。

做法：把一段测试脚本追加到 `dist/index.html` 的副本末尾，让浏览器**真的去点击**
筛选标签、来源、语言徽标与主题按钮，把断言结果写进 DOM，再用 `--dump-dom` 读回来。
比只看静态 DOM 强的地方在于：能验证「点一下到底有没有筛出正确子集」。

不放进 tests/ 的原因：它需要已构建的 dist 与无头 Chrome，不属于 pytest 的单元测试范畴。

共三段：① 1500px 交互（点筛选/来源/徽标/主题）② 1500px 回访（同一 profile 连开两次，
验「刷新后仍保留」）③ 390px 手机（iframe 内嵌窄视口，验窄屏不横向滚动、触控尺寸等）。
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

  // 第五版给窄屏加的横向轮播，绝不能在电脑端留痕：桌面仍是 12 栏网格，
  // 控制条恒为 display:none（轮播只在 <1024px 存在）。像素级不变量另有截图哈希比对。
  const leadGrid = document.getElementById("highlights");
  const gcs = leadGrid ? getComputedStyle(leadGrid) : null;
  const hlBar = document.getElementById("hlBar");
  ok("电脑端「今日重点」仍是网格版式（轮播的 flex 轨道只在窄屏）",
   !!gcs && gcs.display === "grid" && gcs.scrollSnapType === "none",
   gcs ? gcs.display + " / snap=" + gcs.scrollSnapType : "找不到 #highlights");
  ok("电脑端不显示轮播控制条（箭头/指示点/暂停全藏）",
   !hlBar || getComputedStyle(hlBar).display === "none",
   hlBar ? getComputedStyle(hlBar).display : "控制条没渲染");

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
  // 验收条款 02：每条资讯展示标题 +「简短摘要或来源简介」。源站 feed 未给摘要的条目
  // （实测 85 / 398）必须退到来源简介，且该行以「来源简介：」开头 —— 否则读者会把
  // 我们对这个媒体的说明误当成文章摘要。判据只看卡片内的真实节点，不扫全文。
  const cardDescs = [...document.querySelectorAll("#cards li.card")];
  const noDesc = cardDescs.filter((li) => !li.querySelector(".card-txt p") && !li.querySelector(".card-txt .sum-src"));
  ok("每张卡片都带摘要或来源简介（验收条款 02）", noDesc.length === 0,
     cardDescs.length + " 张卡片，缺描述 " + noDesc.length + " 张");
  const blurbLines = [...document.querySelectorAll("#cards li.card .card-txt .sum-src")]
    .filter((n) => n.textContent.startsWith("来源简介："));
  ok("来源简介兜底行带显式前缀，与「摘要：来源 feed 原文」区分",
     blurbLines.length > 0 && blurbLines.every((n) => n.textContent.length > 5),
     "本页 " + blurbLines.length + " 条来源简介行");
  const srcIds = [...new Set(articles.map((a) => a.source_id))];
  ok("数据里出现的每个来源都能查到来源简介",
     srcIds.every((id) => ((SOURCE_BLURB || {})[id] || "").length > 0),
     srcIds.filter((id) => !((SOURCE_BLURB || {})[id] || "")).join(",") || (srcIds.length + " 个来源全覆盖"));
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

# ---------------------------------------------------------------- 移动端阶段
# 前两段都在 1500px 视口上跑，窄屏缺陷在那里根本不会出现。手机这一段要真的在窄视口里跑：
# headless 的窗口宽度最小只有 512px（flags 也绕不过），所以用 390px 宽的 iframe 内嵌，
# 媒体查询才会按手机宽度求值；探针的报告写在 iframe 里，得回写父文档，--dump-dom 才读得到。
# 两个宽度都跑：390px 是 iPhone 常见宽度，360px 是安卓常见宽度。
# 上一版只在 390px 上验，结果 360px 下 .cards 的 1fr 轨道被 min-content 顶到 352px、
# 整页溢出 9px 没被发现 —— 窄屏断言只在窄视口里才有意义，且要覆盖真实的两种窄。
MOBILE_WIDTHS = (360, 390)

MOBILE_HARNESS = r"""
<script>
// 同步探针，挂在 load 事件里 —— 不依赖任何定时器，也就不会跟 --virtual-time-budget 抢时间。
// （原先写成 async IIFE + setTimeout：父文档没有待办任务时虚拟时间会立刻耗尽，
//   --dump-dom 在探针写完之前就抓取，报告时有时无。）
window.addEventListener("load", function () {
  const out = [];
  const ok = (name, pass, extra) => out.push([name, !!pass, extra == null ? "" : String(extra)]);
  // 同一份 harness 在两个宽度下各跑一次，标签带上真实视口宽度，报告里能分清是哪一档红的。
  const TAG = "手机 " + innerWidth + "px：";
  try {
    const root = document.documentElement;
    const cw = root.clientWidth;
    const vis = (el) => {
      if (!el) return false;
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
    };
    // 被祖先裁剪（overflow 非 visible）或落在折叠 <details> 里的元素不算版面溢出 ——
    // Chrome 对折叠的 details 用 content-visibility 跳过渲染并裁剪，那些盒子位置是假的。
    const clipped = (el) => {
      let c = el.parentElement;
      while (c && c !== root) {
        if (getComputedStyle(c).overflowX !== "visible") return true;
        if (c.tagName === "DETAILS" && !c.open) return true;
        c = c.parentElement;
      }
      return false;
    };
    const overflow = () => {
      const bad = [];
      document.querySelectorAll("body *").forEach((el) => {
        if (!vis(el) || clipped(el)) return;
        const r = el.getBoundingClientRect();
        if (r.right > cw + 0.5 || r.left < -0.5) bad.push(el.tagName.toLowerCase());
      });
      return { scrollW: root.scrollWidth, bad };
    };

    const closed = overflow();
    ok(TAG + "页面不横向滚动", closed.scrollW <= cw + 1,
       "文档宽 " + closed.scrollW + "px / 视口 " + cw + "px");
    ok(TAG + "没有元素越过右缘被裁", closed.bad.length === 0,
       closed.bad.length ? closed.bad.slice(0, 5).join(" / ") : "0 个");

    const panel = document.querySelector("details.panel");
    if (panel) panel.open = true;
    const opened = overflow();   // 读几何会强制同步重排，不需要等
    if (panel) panel.open = false;
    ok(TAG + "展开「数据面板」源表后仍不横向滚动", opened.scrollW <= cw + 1,
       "文档宽 " + opened.scrollW + "px / 视口 " + cw + "px");

    const mast = document.querySelector(".masthead");
    ok(TAG + "粘性报头高度 ≤80px（不抢屏）",
       !!mast && mast.getBoundingClientRect().height <= 80,
       mast ? Math.round(mast.getBoundingClientRect().height) + "px" : "找不到报头");

    const kbd = document.querySelector(".kbd");
    ok(TAG + "隐藏桌面快捷键提示 Ctrl K", !vis(kbd),
       kbd ? "display=" + getComputedStyle(kbd).display : "无该元素");

    const inp = document.querySelector("#q");
    let fits = false;
    let detail = "找不到搜索框";
    if (inp) {
      const cs = getComputedStyle(inp);
      const ctx = document.createElement("canvas").getContext("2d");
      ctx.font = cs.fontSize + " " + cs.fontFamily;
      const need = ctx.measureText(inp.placeholder).width;
      const avail = inp.getBoundingClientRect().width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      fits = need <= avail;
      detail = "占位文字需 " + Math.round(need) + "px / 可放 " + Math.round(avail) + "px";
    }
    ok(TAG + "搜索框放得下占位文字", fits, detail);

    const deck = document.querySelector(".deck");
    const sub = document.querySelector("#hlSub");
    const stacked = !!deck && getComputedStyle(deck).flexDirection === "column";
    ok(TAG + "「今日重点」标题区竖排，说明文字占满整行",
       stacked && !!sub && sub.getBoundingClientRect().width >= cw - 2 * 16 - 2,
       (stacked ? "竖排" : "仍是横排") + "，说明宽 " +
       (sub ? Math.round(sub.getBoundingClientRect().width) : 0) + "px");

    // 触控高度只算真正的控件。卡片标题那种行内文字链不算 —— WCAG 2.5.8 对行内文本有豁免。
    const small = [];
    document.querySelectorAll("button, select, input:not([type=checkbox]), summary").forEach((el) => {
      if (!vis(el)) return;
      const h = el.getBoundingClientRect().height;
      if (h < 32) small.push(el.tagName.toLowerCase() + " " + Math.round(h) + "px");
    });
    ok(TAG + "可点控件触控高度 ≥32px（WCAG 2.5.8 的 24px 之上）", small.length === 0,
       small.length ? small.slice(0, 5).join(" / ") : "全部达标");

    const thumb = document.querySelector("#cards li.card .thumb");
    ok(TAG + "卡片缩略图仍在（没为了挤宽度砍配图）",
       !!thumb && thumb.getBoundingClientRect().height >= 60,
       thumb ? Math.round(thumb.getBoundingClientRect().width) + "×" +
               Math.round(thumb.getBoundingClientRect().height) : "无缩略图");

    const unread = [...document.querySelectorAll("#visitBar button")]
      .find((b) => b.textContent.includes("只看未读"));
    ok(TAG + "阅读账本没被窄屏藏掉（「只看未读」在且够大）",
       vis(unread) && unread.getBoundingClientRect().height >= 32,
       unread ? Math.round(unread.getBoundingClientRect().height) + "px" : "找不到「只看未读」");
    // 报头两行：站名与搜索框不再争同一行的宽度。
    // 真机 393px 曾实测到站名压住输入框 12px —— .mast-left{min-width:0} 允许左栏被压扁，
    // 而里面的 .brand{flex-shrink:0} 拒绝收缩，内容溢出父盒照样绘制。
    const brand = document.querySelector(".brand-name");
    const sbox = document.querySelector(".searchbox");
    let overlap = true, gapDetail = "找不到站名或搜索框";
    if (brand && sbox) {
      const a = brand.getBoundingClientRect(), b = sbox.getBoundingClientRect();
      overlap = !(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top);
      const rows = new Set([...document.querySelectorAll(".mast-in > *")]
        .map((e) => Math.round(e.getBoundingClientRect().y))).size;
      gapDetail = overlap
        ? "矩形相交：站名 right=" + Math.round(a.right) + " / 搜索框 left=" + Math.round(b.left)
        : "垂直间隔 " + Math.round(b.top - a.bottom) + "px，报头分 " + rows + " 行";
    }
    ok(TAG + "报头里站名与搜索框不重叠", !overlap, gapDetail);

    // 「被别的元素盖住」是看不见的坏：命中测试直接判它还能不能点到。
    const covered = [];
    ["#q", "#reloadBtn", "#themeToggle"].forEach((sel) => {
      const el = document.querySelector(sel);
      if (!el) { covered.push(sel + " 不存在"); return; }
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (top !== el && !el.contains(top)) {
        covered.push(sel + " 被 " + (top ? top.tagName.toLowerCase() : "空") + " 盖住");
      }
    });
    ok(TAG + "搜索框与两个图标按钮都点得到（没被压住）", covered.length === 0,
       covered.join(" / ") || "三个都命中自己");

    // 手机字号体系：桌面那套（头条 27px、行高 1.28）搬到 358px 栏宽里就是「巨字密排」。
    const fs = (el) => (el ? parseFloat(getComputedStyle(el).fontSize) : 0);
    const lhr = (el) => {
      if (!el) return 0;
      const cs = getComputedStyle(el);
      const f = parseFloat(cs.fontSize);
      return f ? parseFloat(cs.lineHeight) / f : 0;
    };
    const heroH3 = document.querySelector(".lead-hero h3");
    const heroSum = document.querySelector(".lead-hero .lead-sum");
    const deckH2 = document.querySelector(".deck h2");
    const brandName = document.querySelector(".brand-name");
    ok(TAG + "头条标题 ≤20px 且行高 ≥1.35（不是巨字密排）",
       !!heroH3 && fs(heroH3) <= 20 && lhr(heroH3) >= 1.35,
       heroH3 ? fs(heroH3) + "px / 行高比 " + lhr(heroH3).toFixed(2) : "找不到头条标题");
    ok(TAG + "「今日重点」不比站名大（层级不倒挂）",
       !!deckH2 && fs(deckH2) <= fs(brandName) + 2,
       deckH2 ? "区块标题 " + fs(deckH2) + "px vs 站名 " + fs(brandName) + "px" : "找不到区块标题");
    // 头条正文的类名是 lead-sum，而 CSS 里那条 .lead-hero .sum 从未生效 —— 这条断言盯死它。
    ok(TAG + "头条正文按真实类名拿到了排版（13.5–15.5px 衬线）",
       !!heroSum && fs(heroSum) >= 13.5 && fs(heroSum) <= 15.5,
       heroSum ? fs(heroSum) + "px " + getComputedStyle(heroSum).fontFamily.split(",")[0].replace(/["']/g, "")
                      : "找不到头条正文");

    // 验收条款 02 在窄屏同样成立：手机卡片也不能只剩一个标题。
    const mDescs = [...document.querySelectorAll("#cards li.card")];
    const mNoDesc = mDescs.filter((li) => !li.querySelector(".card-txt p") && !li.querySelector(".card-txt .sum-src"));
    ok(TAG + "窄屏卡片同样带摘要或来源简介",
       mDescs.length > 0 && mNoDesc.length === 0,
       mDescs.length + " 张卡片，缺描述 " + mNoDesc.length + " 张");

    // ---------- 「今日重点」横向轮播（第五版）----------
    // 坏的样子有两种：一种点不动（假按钮），一种点得动但看不见在动。
    // 这里两种都盯：控件必须真能切，且切换必须真的改变了滚动位置。
    const track = document.getElementById("highlights");
    const slides = track ? [...track.querySelectorAll(".hl")] : [];
    const tcs = track ? getComputedStyle(track) : null;
    const bar = document.getElementById("hlBar");
    const dots = document.getElementById("hlDots");
    const prevBtn = document.getElementById("hlPrev");
    const nextBtn = document.getElementById("hlNext");
    const toggleBtn = document.getElementById("hlToggle");
    const HL = window.__HL__ || {};
    ok(TAG + "「今日重点」是横向吸附轨道（不是原来那摞竖排卡片）",
       !!tcs && tcs.display === "flex" && tcs.scrollSnapType.indexOf("x") === 0 &&
       tcs.overflowX === "auto",
       tcs ? tcs.display + " / snap=" + tcs.scrollSnapType + " / overflow-x=" + tcs.overflowX
           : "找不到 #highlights");
    ok(TAG + "轨道留得住手指滑动（touch-action 没被禁掉）",
       !!tcs && tcs.touchAction !== "none", tcs ? "touch-action=" + tcs.touchAction : "");
    ok(TAG + "屏数 = 重点条目数 = 指示点数",
       slides.length >= 3 && !!dots && dots.children.length === slides.length,
       "屏 " + slides.length + " / 点 " + (dots ? dots.children.length : 0));
    ok(TAG + "一次主要展示一条：屏宽 = 轨道宽 − 32px",
       slides.length > 0 && slides.every((s) =>
         Math.abs(s.getBoundingClientRect().width - (track.clientWidth - 32)) <= 2),
       slides.map((s) => Math.round(s.getBoundingClientRect().width)).join("/") +
       " vs 轨道 " + Math.round(track.clientWidth));
    const peek = track.clientWidth - slides[0].getBoundingClientRect().width - 12;
    ok(TAG + "静止时露出下一屏的边（看得出左右还有内容）", peek >= 8 && peek <= 40,
       "露出 " + Math.round(peek) + "px");
    // 「一大片空白」是这一版最容易犯的错：各屏高度被最高那屏顶齐，
    // 内容少的屏中间就会空出半张卡。这里量「内容占框高的比例」。
    const fills = slides.map((s) => {
      const cs2 = getComputedStyle(s);
      const inner = [...s.children].reduce((a, c) => a + c.getBoundingClientRect().height, 0);
      const own = inner + parseFloat(cs2.paddingTop || 0) + parseFloat(cs2.paddingBottom || 0);
      return Math.round(own / s.getBoundingClientRect().height * 100);
    });
    ok(TAG + "每屏内容都填得住（占比 ≥75%，不是半张白卡）", fills.every((f) => f >= 75),
       "各屏 " + fills.join("/") + "%");
    ok(TAG + "每屏等高（矮的几屏不塌成矮条）",
       slides.every((s) => Math.abs(s.getBoundingClientRect().height -
         slides[0].getBoundingClientRect().height) <= 2),
       slides.map((s) => Math.round(s.getBoundingClientRect().height)).join("/"));
    ok(TAG + "内容没有被硬切（框高 ≥ 内容高，超出会画到框外）",
       slides.every((s) => s.scrollHeight <= Math.round(s.getBoundingClientRect().height) + 2),
       slides.map((s) => s.scrollHeight + "/" + Math.round(s.getBoundingClientRect().height)).join(" "));
    ok(TAG + "滑动的动画由浏览器原生平滑滚动给（轨道声明 scroll-behavior:smooth）",
       !!tcs && tcs.scrollBehavior === "smooth", tcs ? "scroll-behavior=" + tcs.scrollBehavior : "");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    ok(TAG + "减弱动效时改走一跳到位（代码确实读了这条偏好，不是写死 smooth）",
       HL.smooth === !reduce, "smooth=" + HL.smooth + " reduce=" + reduce);
    ok(TAG + "控制条可见：左右箭头 + 指示点 + 暂停", !!bar && getComputedStyle(bar).display === "flex",
       bar ? getComputedStyle(bar).display : "找不到 #hlBar");
    ok(TAG + "箭头与暂停按钮触控尺寸 ≥32px",
       [prevBtn, nextBtn, toggleBtn].every((b) => !!b && b.getBoundingClientRect().height >= 32 &&
         b.getBoundingClientRect().width >= 32),
       [prevBtn, nextBtn, toggleBtn].map((b) => b ? Math.round(b.getBoundingClientRect().width) + "×" +
         Math.round(b.getBoundingClientRect().height) : "缺").join(" / "));
    const dotBad = [];
    [...(dots ? dots.children : [])].forEach((d, i) => {
      const r = d.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (r.height < 32) dotBad.push("第" + (i + 1) + "颗只有 " + Math.round(r.height) + "px 高");
      else if (!d.contains(hit)) dotBad.push("第" + (i + 1) + "颗被别的元素盖住");
    });
    ok(TAG + "每颗指示点都点得到（≥32px 且没被压住）", dotBad.length === 0,
       dotBad.join(" / ") || dots.children.length + " 颗全部可点");
    ok(TAG + "当前屏有且只有一颗指示点高亮",
       [...dots.children].filter((d) => d.getAttribute("aria-current") === "true").length === 1, "");
    // 真点一遍：箭头 / 指示点 / 暂停都得「点哪去哪」，不是摆设。
    const start = HL.target;
    if (nextBtn) nextBtn.click();
    const afterNext = HL.target;
    if (prevBtn) { prevBtn.click(); prevBtn.click(); }
    const afterPrev = HL.target;
    if (dots && dots.children[2]) dots.children[2].click();
    ok(TAG + "点「下一条」真的切到下一屏", afterNext === start + 1, start + " → " + afterNext);
    ok(TAG + "点「上一条」首尾相接（第 1 屏往回 = 最后一屏）", afterPrev === slides.length - 1,
       afterNext + " → " + afterPrev + "，共 " + slides.length + " 屏");
    ok(TAG + "点第 3 颗指示点真的切到第 3 屏", HL.target === 2, afterPrev + " → " + HL.target);
    ok(TAG + "自动轮播默认是开着的（不是摆个轮播壳子）", HL.playing === true && HL.paused === false,
       "playing=" + HL.playing + " paused=" + HL.paused);
    if (toggleBtn) toggleBtn.click();
    ok(TAG + "按「暂停」真的停下并换成「播放」",
       HL.playing === false && HL.paused === true && toggleBtn.textContent.indexOf("播放") >= 0,
       "playing=" + HL.playing + " 按钮=" + toggleBtn.textContent);
    if (toggleBtn) toggleBtn.click();
    ok(TAG + "再按一下真的继续", HL.playing === true && HL.paused === false,
       "playing=" + HL.playing);
      } catch (error) {
    out.push(["移动端 harness 崩溃", false, String((error && error.message) || error)]);
  }
  // 结果挂到全局，由父页在它自己的 load 里取走：父页的 load 必然晚于 iframe 的 load，
  // 「谁先好」就不再靠时间赌。
  window.__MOBILE_REPORT__ = JSON.stringify(out);
});
</script>
"""

MOBILE_FRAME = """<!doctype html><meta charset="utf-8">
<style>html,body{{margin:0}}iframe{{width:{width}px;height:2400px;border:0;display:block}}</style>
<iframe src="{src}"></iframe>
<script>
window.addEventListener("load", function () {{
  var f = document.querySelector("iframe");
  var rep = null;
  try {{ rep = f.contentWindow.__MOBILE_REPORT__; }} catch (e) {{ rep = null; }}
  var pre = document.createElement("pre");
  pre.id = "TESTREPORT";
  pre.textContent = rep == null
    ? JSON.stringify([["移动端探针没写出结果（iframe 未加载或被同源策略挡住）", false, ""]])
    : rep;
  document.body.appendChild(pre);
}});
</script>
"""


def _run_mobile_frame(page: Path, profile: Path) -> tuple[str, str]:
    """跑一次无头 Chrome，但视口按手机给（靠 390px 宽的 iframe）。

    必须带独立 --user-data-dir：不带的话，这一轮会附着到上一轮还没退干净的 Chrome 实例上，
    --dump-dom 直接交空（前两段在 1500px 跑得通，第三段就「未取到测试报告」）。
    """
    args = [
        str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
        "--window-size=512,2440", f"--user-data-dir={profile}",
        # 探针要把报告写进父文档；滚动条要隐藏，iOS Safari 用的是悬浮滚动条，不该吃掉宽度。
        "--allow-file-access-from-files", "--hide-scrollbars", "--force-device-scale-factor=1",
        "--virtual-time-budget=20000", "--dump-dom", page.as_uri(),
    ]
    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    return proc.stdout or "", proc.stderr or ""


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

        # 第三段：手机端。前两段都在 1500px 视口上跑，窄屏缺陷在那里不会出现。
        inner = stage("mobile-inner.html", MOBILE_HARNESS)
        for width in MOBILE_WIDTHS:
            frame = scratch_dir / f"mobile-frame-{width}.html"
            frame.write_text(MOBILE_FRAME.format(width=width, src=inner.as_uri()), encoding="utf-8")
            # 每次 Chrome 调用都换一个独立 profile：共用会附着到上一轮没退干净的实例，
            # --dump-dom 交空（这个坑前两段都踩过）。顺带保证是「干净访客」，没有已读状态。
            profile_i = Path(tempfile.mkdtemp(prefix=f"hermes-verify-ui-mobile-{width}-"))
            try:
                dom3, err3 = _run_mobile_frame(frame, profile_i)
            finally:
                shutil.rmtree(profile_i, ignore_errors=True)
            rows3 = _report(dom3)
            if rows3 is None:
                print(f"移动端阶段（{width}px）未取到测试报告；DOM 长度 =", len(dom3))
                print(err3[-2000:])
                return 1
            rows += rows3
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