# 每日 AI 情报站 · 合并修订版实施计划（v2）

> **基线来源**：本计划合并两份方案 ——
> ① 《AI Radar Phase0 技术设计文档 / Agent 执行总计划（修订版）》的**工程骨架**（状态机、CI、工程纪律、风险左移）；
> ② 本地实测侦察得出的**数据源与算法方案**。
> 凡与基线文档冲突处，均标注 **[修订]** 并附实测证据。

> **实施状态：已完成。** 本文件是动手前定稿的计划，保留原样以便对照。
> 计划与实际实现的差异（含 4 处真实缺陷的定位与修复、6 项被测试发现的返工）
> 记录在 [`DEVLOG.md`](../DEVLOG.md)，两份文件一起看才是完整过程。

---

## 1. 目标与评分映射

| 交付要求（原文） | 本计划对应机制 | 分值落点 |
|---|---|---|
| 看起来好看、舒服，愿意每天打开 | 深色情报站视觉 + 来源色相编码 + 今日重点简报条 | 视觉体验 25 |
| 数据真实、来源清晰、时间可靠 | 16 个实测源 + 五态状态机 + 时间口径三段分离 | 可靠性 25 |
| 开发过程与现场迭代 | 纯函数下沉 + fixtures 回放 + 明确砍单顺序 | 过程 15 |
| 额外亮点 + 产品判断 | 「今日重点」可解释聚合 + 已知限制披露 | 各 15/5 + 亮点 10 |

---

## 2. 数据源（本地实测，2026-09-16）

### 2.1 生产源清单（16 个，全部 HTTP 200 实测通过）

**中文 8 个**

| id | 名称 | feed 类型 | 条数 | 日期格式 | 备注 |
|---|---|---|---|---|---|
| `qbitai` | 量子位 | RSS | 10 | `+0000` | AI 垂类 |
| `infoq-ai` | InfoQ 中国 AI 频道 | RSS | 20 | `GMT` | AI 垂类 |
| `leiphone` | 雷锋网 | RSS | 20 | `+0800` | 需相关性过滤；feed 494KB |
| `oschina` | 开源中国 | RSS | 50 | `+0800` | 需相关性过滤 |
| `ifanr` | 爱范儿 | RSS | 20 | `+0000` | 需相关性过滤；feed 501KB |
| `sspai` | 少数派 | RSS | 10 | `+0800` | 需相关性过滤 |
| `solidot` | Solidot | RSS | 20 | `+0800` | 需相关性过滤 |
| `tmtpost` | 钛媒体 | RSS | 19 | `+0800` | 需相关性过滤；feed 259KB |

**英文 6 个**

| id | 名称 | 类型 | 条数 | 日期格式 | 备注 |
|---|---|---|---|---|---|
| `openai` | OpenAI News | RSS | **1193** | `GMT` | 官方源；必须按窗口裁剪 |
| `google-ai` | Google AI Blog | RSS | 20 | `+0000` | 官方源 |
| `techcrunch-ai` | TechCrunch AI | RSS | 20 | `+0000` | AI 垂类 |
| `verge-ai` | The Verge AI | **Atom** | 10 | ISO8601 `+00:00` | AI 垂类 |
| `mit-techreview-ai` | MIT Tech Review AI | RSS | 10 | `+0000` | AI 垂类 |
| `simonw` | Simon Willison | **Atom** | 30 | ISO8601 `+00:00` | 需相关性过滤 |

**API 型 2 个**

| id | 名称 | 端点 | 备注 |
|---|---|---|---|
| `hn` | Hacker News | Algolia `search_by_date` | **双轨**，见 2.4 |
| `arxiv` | arXiv cs.AI | `rss.arxiv.org/rss/cs.AI` | **必须限流**，见 2.3 |

**总计 16 源**，达成「≥2 独立信息源」的 8 倍冗余。任一单源失败不影响主链路。

### 2.2 已排除的源（附理由 —— 这段本身就是工程质量证据）

| 源 | 实测结果 | 处置 |
|---|---|---|
| 机器之心 `/rss` | 返回「数据服务」HTML 页，**RSS 已废弃** | 排除 |
| 36 氪 `/feed` | JS 反爬页，非 XML（brief 明示无需绕反爬） | 排除 |
| 品玩 | **XML 非法**：`not well-formed (invalid token): line 56` | 排除，并作为解析容错 fixture |
| DeepMind / DeepLearning.AI | 404 / TLS 超时 | 排除 |
| HuggingFace / Reddit | 需代理，违背「链路越简单越可信」 | 排除 |
| Google News | 聚合器，跳转链接受控、去重困难 | 排除 |
| VentureBeat AI | HTTP 200 但**最新一条为 08-27（距今 20 天）** | **[修订]** 移出生产源，保留为「陈旧源检测」测试样本 |
| Ars Technica | 通用技术 feed，非 AI 垂类 | 排除 |

### 2.3 [修订] arXiv 必须限流 —— 否则与 retention 规则直接冲突

**基线文档方案**：arXiv 作为主源之一，retention = 近 60 天或最多 1000 条取更严格者。

**实测**：`rss.arxiv.org/rss/cs.AI` 单次响应 **1.34 MB / 634 条 item**，且 **RSS 无 `max_results` 参数可限制**。

> 634 条/天 ÷ 1000 条上限 ≈ **1.5 天**
> 而「近 7 天」筛选需要约 **4,500 条**
> **⇒ 基线的两条规则不可同时成立**，面试官点开「近 7 天」只会看到 1.5 天数据。

**修订**：

- arXiv **降级为第二源**，单次摄入上限 `max_items: 20`（按 pubDate 倒序取最新）
- retention 上限提升至 `MAX_STORE_ITEMS = 4000`，窗口 `WINDOW_DAYS = 30`
- 预估存储：非 arXiv 源约 60 条/天 + arXiv 20 条/天 = **80 条/天**，30 天 ≈ 2,400 条，落在上限内 ⇒ 「近 7 天」有约 560 条，功能成立
- 预估 JSON 体积：约 1.5 MB（gzip 后约 300KB），可接受

**为什么 arXiv 不能做主源**：brief 的定位是「面向 AI 爱好者、开发者和**内容创作者**，快速浏览**近期资讯**」。arXiv cs.AI 是学术论文库（标题如 `Agentic Societies Need a Social Harness`，摘要 1000+ 字，每天 634 篇），对内容创作者几乎不可用；若按 634:60 的比例摄入，列表会退化成「论文流」。限流到 20 条/天既满足「≥2 独立源」，又保留「前沿论文」这个差异化价值。

### 2.4 [修订] HN 必须加数值过滤 —— 基线查询式实测信噪比为零

**基线文档方案**：`search_by_date?query=AI&tags=story`

**实测返回的 20 条**：

```
points<10 的有 20 条 | 最高仅 4 分
  [1分 0评] Show HN: Gescom MiniDisc Runner – 88 tracks, 88! possible orders   ← 与 AI 无关
  [1分 0评] HP ZBook Ultra G3a 16 Preview: 192GB Unified Memory
  [1分 0评] The New Chinese Way of Cyberwar
  [1分 0评] Show HN: Modern RSS reader with social capabilities
```

原因：`search_by_date` 按**时间**排序而非相关度，返回的是「最近提交的、文本命中 AI 的帖子」，多为刚提交尚未投票的噪音。而基线文档明确要展示 `metadata: {points, comments}` ⇒ 每张卡片都会显示「1 分 0 评论」，**比不显示更糟**。

**修订为双轨**（均已实测）：

| 轨道 | 查询 | 用途 | 实测 |
|---|---|---|---|
| 高信号轨 | `query=AI&tags=story&numericFilters=points>80` | 值得读 | 20/20 条 ≥80 分，最高 **1451 分** |
| 新鲜轨 | `query=AI&numericFilters=created_at_i>{now-6h},points>20` | 抢先看 | 过滤 6 小时内已有起色的帖子 |

高信号轨实测样本：`Introducing System One Models and Jev`(1451分/411评)、`Gemini 3.8 Live and 3.8 Live Extended Thinking`(426/278)、`We got admin access to Baseten's production GitHub`(286/162)。
`search_by_date` + points 阈值是**理想组合**：「最近达到过 80 分的故事」= 既新鲜又有热度。

### 2.5 [修订] 通用源需 AI 相关性过滤

雷锋网 / 开源中国 / 爱范儿 / 少数派 / Solidot / 钛媒体 / Simon Willison 是**通用科技源**，需按关键词过滤以保证「AI 话题纯度」。

**实现要点（易错）**：拉丁词必须用**词边界**正则，不能用子串匹配 —— 否则 `AI` 会命中 `said` / `email` / `chair` / `available` / `maintain`，`model` 会命中 `model citizen`。中文字词用子串匹配即可。

```python
# 拉丁词：\b 词边界；中文：子串
LATIN = r"\b(?:AI|LLM|GPT|AGI|RAG|ML|agentic|transformer|multimodal)\b"
CJK   = r"(?:人工智能|大模型|智能体|深度学习|机器学习|多模态|生成式|算力|微调|推理)"
```

---

## 3. 架构与数据流

```
GitHub Actions（唯一 workflow，cron 00:23 UTC = 北京 08:23，非整点错峰）
  │
  ├─ 1. 采集  16 源 → fetch(httpx, 超时/退避/条件请求)
  ├─ 2. 解析  → 适配器(rss/atom/hn/arxiv) → RawItem
  ├─ 3. 归一  → 日期多格式解析 + HTML→纯文本 + 截断 + 语言判定
  ├─ 4. 过滤  → AI 相关性 gate（仅通用源）+ 窗口裁剪
  ├─ 5. 去重  → 四段：canonical URL → 标题哈希 → 标题相似 → 事件聚合
  ├─ 6. 合并  → 幂等 merge 进 news.json（按 id upsert）+ retention
  ├─ 7. 打分  → 可解释 score + cluster 跨源命中数
  ├─ 8. 落盘  → data/news.json + data/runs.json（历史）+ data/update-status.json
  ├─ 9. 提交  → git diff 有变化则 commit/push data
  └─ 10. 部署 → vite build（构建期内联 JSON）→ Pages artifact → deploy-pages

本地双击可看：构建同时产出 dist/intel-single.html（单文件，数据与样式全内联）
```

**关键取舍**：为什么 JSON 文件而不是数据库 —— 静态站无后端，JSON 可 diff、可 review、可直接当「每天在跑」的证据；数据量 2,400 条量级，Git 完全扛得住。

---

## 4. 数据模型

```python
class Article:
    id: str                  # sha1(canonical_url)[:16] —— 稳定主键
    title: str
    url: str                 # 原文链接（展示用）
    canonical_url: str       # 归一后（去重键）
    source_id / source_name / source_lang / source_official
    published_at: datetime | None   # UTC；None = 源未提供或异常
    published_raw: str | None       # 原始字符串，保留供追溯与调试
    published_note: str | None      # unparseable | future_date | tz_assumed
    fetched_at / first_seen_at / last_seen_at: datetime   # 三者口径分离
    summary: str | None
    summary_source: Literal["feed", "none"]   # 恒为 feed ⇒ 无需 AI 标注
    lang: Literal["zh", "en", "other"]
    heat: int | None         # HN points，其他源为 None
    comments: int | None
    dup_of: str | None       # 被判为重复时指向主条目 id
    cluster_id: str | None   # 事件聚合簇
    score: float             # 「今日重点」打分
    reasons: list[str]       # 可解释推荐理由
```

**时间字段口径（三者绝不可混用）**：

| 字段 | 含义 | 用途 |
|---|---|---|
| `published_at` | **来源声称**的发布时间 | 唯一可用于「今天/近 7 天」筛选与展示 |
| `first_seen_at` | 本管线**首次看到**它的时间 | 判定 `no_new`（是否真有新内容） |
| `last_seen_at` | 最近一次仍在源中出现 | 判断源是否仍在更新它 |
| `fetched_at` | 本次抓取时刻 | 仅作运行诊断，**绝不冒充发布时间** |

---

## 5. [修订] 五态状态机（基线为四态，补第五态）

| 状态 | 判定条件 | 含义 |
|---|---|---|
| `ok` | HTTP 200 且解析出条目且存在新内容 | 正常 |
| `no_new` | HTTP 200、解析出条目，但 `first_seen_at` 无新增 | 源活着，只是今天没新东西 |
| `parse_warning` | HTTP 200，但**解析出 0 条** | ️ 源格式可能变了，解析器静默失效 |
| `stale` | HTTP 200、有内容，但 `latest_published_at` 距今 > 14 天 | ️ 端点活着但内容已死 |
| `failed` | 网络错误 / 非 2xx / 解码失败 / 解析抛异常 | 抓取失败 |

**为什么必须有 `parse_warning`**：HTTP 200 却解析出 0 条 **不等于**「没有新内容」，而是「源改版了，我的解析器瞎了」。这是采集系统最危险的**静默失效**——站点看起来一切正常，数据其实早断了。三态抓不到它。

**为什么必须有 `stale`**：实测 VentureBeat AI 的 feed 可解析、HTTP 200、有 7 条内容，但**最新一条是 20 天前**。四态会把它判为 `ok`。「官方博客改版后 feed 停更但端点仍活着」是非常真实的失效模式。

**`overall` 判定必须定死**（基线文档此处边界模糊）：

```
若全部源 failed                        → failed
否则若有任一 failed 或 parse_warning
      或 stale                        → partial
否则若全部源 no_new                    → no_new
否则                                   → success
```

---

## 6. 核心算法

### 6.1 URL 归一（去重第一段）

按顺序：强制 scheme 统一 → 小写 host → 去 `www.` → 去尾斜杠 → 去 fragment → **剥离追踪参数**。

追踪参数表：`utm_*`, `fbclid`, `gclid`, `spm`, `ref`, `source`, `from`, `share_token`, `mc_cid`, `mc_eid`, `igshid`, `_hsenc`。

> 实测必要性：中文源普遍带 `?utm_source=...&spm=...`，**第二次抓取 URL 与第一次不同** ⇒ 若不剥离，同一篇文章每天都会被当成新条目。这正是验收项「重复导入不应产生重复记录」的真实陷阱。

### 6.2 日期解析（四种实测格式）

| 实测格式 | 出现源 |
|---|---|
| `Wed, 16 Sep 2026 08:51:02 +0000` | 量子位、爱范儿、Google、TechCrunch、Verge |
| `Wed, 16 Sep 2026 19:11:10 GMT` | InfoQ、OpenAI News |
| `Wed, 16 Sep 2026 19:11:10 +0800` | 雷锋网、开源中国、少数派、Solidot、钛媒体 |
| `2026-09-16T00:03:22+00:00` | The Verge（Atom）、Simon Willison |

策略：`dateutil.parser.parse` 为基座，外加 4 类边界处理 ——

1. **epoch 数字**（秒/毫秒，≥10 位）单独处理
2. **无时区信息** → 按 UTC 处理但标记 `published_note="tz_assumed"`，UI 显示 `~` 前缀（**诚实原则：不假装知道**）
3. **未来时间**（> now + 2h）→ `published_at = None`，`note="future_date"`，UI 显示「发布时间异常（源标注 …）」
4. **晚于 2000 年之前** → 视为异常
5. **解析失败** → `published_at = None`，`note="unparseable"`，UI 显示「发布时间未知」

### 6.3 四段去重（幂等核心）

| 段 | 方法 | 动作 |
|---|---|---|
| 1 | `canonical_url` 精确匹配 | 判重 |
| 2 | 归一标题 SHA1（NFKC + 小写 + 去标点空白） | 判重 |
| 3 | 归一标题 **3-gram Jaccard ≥ 0.85** | 判重 |
| 4 | 标题 **1/2-gram + 实体词 Jaccard ≥ 0.34** | **不删**，归入同一 `cluster_id`（跨源事件聚合） |

选 3-gram 字符集合而非分词：**中英文通用、无需分词器、确定性、易解释、易单测**。

**幂等三条原则**（决定「重复导入」验收能否通过）：

1. 按 `id` **upsert**，不做「清空重建」
2. `published_at` 只保留**首次成功解析的非空值** —— 某次解析失败不得把已知时间抹成未知
3. `first_seen_at` 一经写入**永不变更**；重复命中只更新 `last_seen_at`

⇒ 同一份输入导入 N 次，条目数恒定。

### 6.4 「今日重点」可解释打分（自选亮点）

```
score = 3.0 × 来源权重
      + 2.5 × 跨源命中度 (min(簇内来源数, 4) / 4)
      + 1.5 × 时效衰减 (max(0, 1 - 小时数/72))
      + 0.8 × 信号词命中（发布/开源/融资/漏洞/榜单/收购）
      + 0.4 × 热度归一 (HN points)
```

全部权重集中在 `pipeline/config.py`，README 说明依据。

**为什么这是好亮点**：brief 要求「你发现了什么问题？改进让用户少做了什么、多获得了什么？」

> 用户面对 80 条标题的**真实痛点不是「读不完」，而是「不知道哪几条值得读」**。
> 本机制把「扫 80 条并自行判断」压缩为「读 3 条，且**知道自己没漏掉重要的**」。
> 关键在于**可解释**：每条显示「3 个来源报道 · 来自官方博客 · 2 小时前」——用户能验证推荐理由，而不是被一个黑盒排序摆布。
> 零 LLM、零成本、零幻觉风险，同时不违反「不得补造事实」。

**不接 LLM 摘要的理由**（与基线文档一致，但理由要说清）：源站自带摘要已满足 brief 要求「简短摘要**或来源简介**」；而一旦接入 LLM 生成内容，就必须承担**标注义务（05）**、**成本**、**幻觉风险**三重负担。当前选择让「AI 生成内容」这一栏天然干净。若后续要加，走 `SUMMARY_MODE = "feed" | "ai"` 配置开关，且 `ai` 模式必须打 🤖 徽章 + 降级回退。

---

## 7. 关键工程决策

### 7.1 [采纳基线] 为什么必须是**唯一 workflow**

GitHub 明确规定：**由 `GITHUB_TOKEN` 触发的 push 事件不会创建新的 workflow run**（防止递归，`workflow_dispatch` / `repository_dispatch` 除外）。

⇒ 若拆成「采集 workflow 用 `GITHUB_TOKEN` push data」+「部署 workflow on: push」，**部署永远不会被触发** —— 数据更新了，页面却永远停在旧版本。这个 bug 只看代码看不出来。

因此：**一个 workflow 内完成** `采集 → 提交数据 → 构建 → 部署`，顺序不可颠倒（构建必须读最新数据）。

**Workflow 必备项**（采纳基线 + 补充）：

```yaml
permissions:
  contents: write      # 提交 data/
  pages: write
  id-token: write
concurrency:
  group: intel-pipeline
  cancel-in-progress: false   # 防止两个 run 抢写 data/
timeout-minutes: 15           # 防止 job 挂死
on:
  schedule: [{cron: "23 0 * * *"}]   # UTC 00:23 = 北京 08:23，非整点错峰
  workflow_dispatch:
```

### 7.2 [修订] 构建期内联 + 单文件 HTML

基线文档决定「前端构建期内联 JSON」（正确，避免运行时 fetch），但它在已知限制里承认：
> 「双击 `index.html` 可能因 ES Module 的 CORS 限制打不开，需 `python -m http.server`。」

**修订**：构建同时产出 **`dist/intel-single.html`** —— 数据、样式、脚本全部内联进一个 HTML。收益：

1. **双击直接可用**，基线文档的痛点自动消失
2. 满足图片里的提交要求「**打包 html 文件发送给面试官**」（基线文档漏了这条）
3. 满足「无网络也能演示」——复试现场断网也不怕

### 7.3 [修订] 必须保留运行历史 `data/runs.json`

基线文档只有覆盖式的 `update-status.json`。但要求 06 是「配置**每天**自动运行的数据更新任务」——

> **运行历史本身就是「定时任务持续工作」的唯一证据。**
> 只有一个当前状态文件时，面试官问「你说每天跑，证据呢」无法回答。

**修订**：`data/runs.json` 保留最近 60 次运行记录（约 2 个月），首页展示「最近 7 天更新成功率」。

### 7.4 前端技术选型（保留基线选择，但认领成本）

基线选用 **React + Vite + Tailwind**，理由成立（纯函数可单测、为现场修改做准备），予以保留。但两个成本必须写进 README 已知限制：

1. 面试官「干净环境按 README 运行」需要 `npm ci` ⇒ Node 版本 / peer deps / 网络是**新增失败点**
2. `node_modules` 让「自己仍然改得动」更依赖工具链 —— 现场改一个筛选逻辑，需 `npm run test` + `npm run build` 才能看到结果

> **决策记录**：若复试时间压力大，可退回「零构建原生 HTML/CSS/JS」方案（改完刷新即见）。两方案的**数据契约完全相同**（都读 `data/news.json`），因此前端可整体替换而不动管线。

---

## 8. 前端设计规范

**定位**：情报站 / 雷达感。深色优先，配亮色主题跟随系统。

| 项 | 规范 |
|---|---|
| 深底 / 卡片 | `#0B0F14` / `#121820` |
| 主强调 / 亮点强调 | 电光青 `#22D3EE` / 琥珀 `#F59E0B`（仅用于「今日重点」） |
| 来源编码 | **来源色相 hash** ⇒ 同源恒同色，一眼分辨来源（视觉一致性 + 信息组织双赢） |
| 字体 | `-apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif` |
| 布局 | 最大宽 1100px 居中；列表 1 列（手机）/ 2 列（≥900px） |
| 卡片 | 来源徽章(色点+名) · 相对时间（hover 显示绝对时间+时区）· 标题（2 行 clamp）· 摘要（3 行 clamp）· 热度 · 原文链接 |
| **无图设计** | **不抓文章配图**。用「来源色相 + 字号排版」做视觉锚点 —— 规避版权/热链/失败率，并把验收要求的「无图片状态」从缺陷变成设计（这本身就是产品判断） |
| 交互态 | hover 抬升 / `focus-visible` 描边 / 按钮 loading / 搜索 200ms 防抖 + `aria-live` 结果数 / `Esc` 清空 / `/` 聚焦搜索 |
| 移动端 | 筛选栏吸顶可折叠；点击目标 ≥44px；回到顶部 |

**状态覆盖（验收必查）**：骨架屏（首加载）· 空结果（带「清空筛选」）· 源失败黄条 · 部分失败 · 未知日期 · 长标题 · 无图片。

---

## 9. 分阶段任务与停点

| Phase | 内容 | 产出 | 停点验证 |
|---|---|---|---|
| **P0** | 建仓、目录、`.gitignore`、依赖锁定、DEVLOG 计时起始 | 可 `pip install -r requirements.txt` | 干净 venv 安装通过 |
| **P1** | 采集管线全链路（fetch/adapt/normalize/filter/dedupe/merge/score/report）+ 首次真实运行 | `data/news.json` + 首次运行报告 | `python collect.py` 真实产出 ≥40 条；`--input` 回放幂等 |
| **P2** | pytest 单测 + fixtures（含：重复导入、单源失败、HTTP200 解析 0 条、缺日期、长标题、陈旧源） | `tests/` 全绿 | `pytest -q` |
| **P3** | 前端（搜索/来源/今天/近7天/清除/只看未读/今日重点/响应式/全状态） | 可本地预览的站点 | 移动端 375px 截图 |
| **P4** | 唯一 workflow + Pages + **单文件 HTML** 产出 + 真实 dispatch 跑通 | 线上地址 | Actions 绿 + Pages 可访问 |
| **P5** | 严格验收 8 项逐条执行 + `VALIDATION.md` | 验证矩阵 | 每项标 已验证/未验证/未完成 |
| **P6** | 交付材料（README/DEVELOPMENT/VALIDATION/运行记录/成本/已知限制）+ 提交消息 | 完整交付包 | 按 README 从零跑通 |

---

## 10. 时间预算与砍单顺序

| Phase | 预算 | 累计 |
|---|---|---|
| P0 | 0.5h | 0.5h |
| P1 | 1.5h | 2.0h |
| P2 | 1.0h | 3.0h |
| P3 | 2.0h | 5.0h |
| P4 | 1.0h | 6.0h |
| P5 | 0.75h | 6.75h |
| P6 | 0.75h | 7.5h |
| **buffer** | **0.5h** | **8.0h** |

**[修订] 砍单顺序（超时按此顺序放弃，绝不牺牲验收项）**

1. AI 摘要增强（本来就未启用）
2. 「只看未读」localStorage 已读态
3. 单文件 HTML 产出（保 GitHub Pages 地址）
4. 亮色主题（保深色）
5. 「近 7 天」筛选（保「今天」）

**永不砍**：真实数据、自动更新、单源失败处理、重复导入幂等、缺日期处理、长标题、搜索无结果、原文可打开、干净环境可运行 —— 这 9 项全是验收项。

> 超时不等于失败：brief 明示「到达时间上限可提交当前成果并说明取舍」。**如实记录耗时并说明取舍本身就是评分点。**

---

## 11. 硬性验收对照表

| # | 验收要求 | 实现机制 | 证据 |
|---|---|---|---|
| 1 | ≥2 独立信息源 | 16 源 | 运行报告 `sources[]` |
| 2 | **重复导入不重复计数** | 四段去重 + 幂等 upsert | `pytest` + `--input` 连续导入两次对比 |
| 3 | 单源不可用不影响全局 | 每源独立 try + 五态 | 「失败源隔离」测试截图 |
| 4 | HTTP 200 但解析 0 条 | `parse_warning` 态 | fixture `http-200-parse-zero` |
| 5 | 缺日期处理 | `published_note` 三段 | 截图 + 单测 |
| 6 | 超长标题 | 2 行 clamp + 完整 title 属性 | 131 字符标题截图 |
| 7 | 搜索无结果 | 空状态设计 | 截图 |
| 8 | 原文可打开 | 每卡链接直连原文 | 抽样脚本输出 HTTP 状态表 |
| 9 | 干净环境按 README 运行 | 依赖锁定 + README 步骤 | 全新 venv 实测记录 |
| 10 | 每天自动更新 | 唯一 workflow + 运行历史 | `data/runs.json` + Actions 记录 |
| 11 | 时间可靠 | 三段口径分离 | README 口径表 |

---

## 12. 验证矩阵（`VALIDATION.md` 模板）

```
| 验收项 | 状态 | 证据 | 备注 |
|---|---|---|---|
| 双源采集 | 已验证 | data/news.json 含 16 源 | |
| 重复导入幂等 | 已验证 | 导入 2 次均为 N 条 | |
| 单源失败隔离 | 已验证 | 注入 order 失败，其余 15 源正常 | |
| HTTP200 解析0条 | 已验证 | fixture http-200-parse-zero → parse_warning | |
| 缺日期 | 已验证 | 3 条无日期条目显示「发布时间未知」 | |
| 超长标题 | 已验证 | 131 字符标题 2 行截断 | |
| 搜索无结果 | 已验证 | 空状态截图 | |
| 原文抽查 | 已验证 | tools/verify_links.py：10/10 返回 200 | |
| 干净环境 | 已验证 | 全新 venv 按 README 跑通 | |
| 定时触发 | **未验证** | 需真实经过一次 cron | 若 72h 内未触发则如实标注 |
```

---

## 13. 风险与已知限制

| 风险 | 影响 | 缓解 |
|---|---|---|
| GitHub Actions `schedule` 不保证准点，新建仓库首次触发可能延迟数小时 | 「定时触发」可能来不及验证 | **P4 先用每分钟的临时 cron 验证 schedule 机制本身可用**，再改回每日 ⇒ 可分开陈述「schedule 机制已验证」/「每日频率未验证」 |
| 仓库 60 天无活动会自动禁用 schedule | 长期失效 | README 披露 |
| GitHub Pages 在国内可能不稳定 | 面试官打不开 | 同时交付单文件 HTML；README 说明可换 Cloudflare Pages |
| 中文源普遍共用同一批供稿/转载 | 去重压力 | 第 3 段 3-gram 相似度 + 第 4 段事件聚合 |
| 源格式变更 | 静默失效 | `parse_warning` 态 + `fixtures` 回归 |
| `npm ci` 在干净环境失败 | 面试官无法运行 | README 写清 Node 版本；单文件 HTML 作为零依赖降级路径 |
| pip 首次安装遇 `self-signed certificate in certificate chain` | 安装失败 | **实测确认为 Clash 系统代理切换期的瞬时 MITM**，关闭代理或重试即可；README 记录 |

---

## 14. 复试现场预案（30 分钟）

**预备的现场改动**（改完可立即验证，因为纯函数已下沉 + 有单测）：

| 可能需求 | 改哪里 |
|---|---|
| 调整「近 7 天」边界为 3 天 | `pipeline/config.py` 的窗口常量 + 前端 `lib/date.ts` |
| 新增一个来源筛选项 | `SOURCES` 增一条 + 前端自动生成 chip（无需改 UI 代码） |
| 修改「今日重点」权重 | `pipeline/config.py` 的权重表 |
| 增加按语言筛选（中/英） | `Article.lang` 已存在，前端加一维 filter |

**讲解提纲**

1. **数据流**：16 源 → 适配器 → 归一 → 相关性过滤 → 四段去重 → 幂等合并 → 可解释打分 → JSON → 单 workflow 构建/部署
2. **关键取舍**：为什么 JSON 而非数据库；为什么**必须单 workflow**（`GITHUB_TOKEN` 不触发新 run）；为什么 arXiv 限流到 20 条/天；为什么 HN 要加 `points>80`；为什么不接 LLM 摘要
3. **可靠性**：五态如何区分 `no_new` / `parse_warning` / `stale` / `failed`；为什么全失败也要写 status；为什么旧数据不能清空
4. **产品判断**：为什么无图设计；为什么「今日重点」要可解释；为什么披露已知限制

---

## 15. 项目完成判定

- **P0 完成**：设计已确认，P0/P1 变更进入基线
- **每个 Phase 有明确停点、验证命令、交付证据**
- **最终目标**：「好看、可信、能运行，而且自己仍改得动」

> 本计划依据用户上传的《开发实作测试 / Candidate Brief》整理；原题要求在 **72 小时内提交**、实际投入 **不超过 8 小时**，并明确检查：真实数据、自动更新、单源失败、重复导入、缺日期、长标题、搜索无结果、原文抽查、干净环境运行。