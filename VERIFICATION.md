# 验证记录（Verification Record）

> 对应测试题「验证说明」。要求：**真实数据获取**、**固定输入重复导入**、**单个来源失败**等场景的验证结果，
> 任务配置及运行记录，并**明确列出「已验证」「未验证」「未完成」**。
>
> 本文所有数字都是**本次实测输出**，不是估计值。每条结论都给出可复现命令。
> 最后更新：2026-09-16。

## 0. 口径声明（先说清楚每个数字怎么算）

| 项目 | 口径 |
| --- | --- |
| 时间存储 | 一律 UTC；前端按浏览器本地时区显示 |
| `published_at` | **只来自源站自称的发布时间**。解析不出或不采信时置 `null` 并记录原因 |
| `first_seen_at` | 抓取时刻。**绝不用它顶替发布时间** |
| 源级时区更正 | InfoQ 中文站把北京时间标成 `GMT`，用源级 `declared_utc_offset_minutes=480` 更正，并标 `published_note="tz_corrected"` |
| 日期范围 | 滚动 **30 天**窗口（`WINDOW_DAYS=30`），更早条目在合并前丢弃 |
| 「有图」 | 条目带 `image_url`。**仅从 feed 载荷已下载字段提取，零额外请求** |
| 本机环境 | Windows 10 · Python 3.11 · 中国大陆直连网络（无代理时部分境外源不可达） |

---

## 1. 已验证（Verified）

### 1.1 真实数据获取 —— 已验证 ✅

**命令**

```bash
python collect.py --trigger manual
```

**结果**：16 个生产源全部参与，写入库中 **231 条**。

| 指标 | 实测值 |
| --- | --- |
| 库中条目 | **231** |
| 中文源条目 / 英文源条目 | 102 / 129 |
| 官方一手源条目 | 35 |
| 带发布时间（`published_at` 非空） | **231 / 231（100%）** |
| 无发布时间 | 0 |
| 跨源聚合（被并入事件簇） | 7 |
| 有配图 | **49 / 231（21.2%）**，来自 **8 个源** |
| 近 24 小时 / 近 7 天 | 114 / 210 |
| 源英文展示名缺失 | **0**（曾出现 19 条，已修，见 DEVLOG 8.3） |

提供配图的 8 个源：Leiphone 13、The Verge AI 11、iFanr 11、TMTPost 5、
Simon Willison 3、MIT Tech Review AI 3、QbitAI 2、OpenAI News 1。

**真实数据来源核对**（`tools/audit_sources.py` 输出）：16 个源的 HTTP 状态、条目数、
最新条目时间、日期格式与时间可信度均已实测记录在 `data/runs.json` 的 `sources[]` 中。

### 1.2 固定输入重复导入 —— 已验证 ✅（两条不变量）

**（a）同输入 + 同冻结时刻 → 产物逐字节相同**

```bash
python collect.py --input tests/fixtures/replay --now 2026-09-16T12:00:00Z --data-dir /tmp/a
python collect.py --input tests/fixtures/replay --now 2026-09-16T12:00:00Z --data-dir /tmp/b
cmp /tmp/a/news.json /tmp/b/news.json   # 同理 runs.json / update-status.json / meta.json
```

实测结果：**4 个产物全部逐字节一致**（`news.json`、`runs.json`、`update-status.json`、`meta.json`）。

实现要点：冻结时刻时墙钟耗时清零、`run_id` 由输入指纹派生（不再用随机 uuid）。

**（b）把同一批输入再合并一次 → 条目集合不变**

| 次数 | `run_id` | 新增 | 去重 | 存储 |
| --- | --- | --- | --- | --- |
| 第 1 次 | `20260916T120000Z-c1eeec` | 184 | 22 | **184** |
| 第 2 次 | `20260916T120000Z-c1eeec` | **0** | 206 | **184** |

幂等的三条不变量（由 `pipeline/store.py::merge` 保证，`test_store.py` 覆盖）：

1. `first_seen_at` 一经写入永不改变，重复导入只推进 `last_seen_at`；
2. 已存在条目不会被新副本替代，也不会产生第二条记录；
3. 易变字段（热度、摘要）的更新全部是**单调**操作（`max` / 更长者胜 / 补空），
   且合并前对入参做确定性排序 —— 因此结果与调用顺序无关。

### 1.3 单个来源失败 —— 已验证 ✅（离线确定性复现）

把离线样本集复制一份，人为破坏其中一个源，与干净基线对照（同一批输入、同一冻结时刻）：

| 场景 | `overall` | 库中条目 | 受损源 `qbitai` 状态 |
| --- | --- | --- | --- |
| 基线（干净） | `success` | 184 | `ok`，保留 10 条 |
| 该源文件内容被损坏 | `success` | 176 | `parse_warning`，0 条（非法 XML 已容错但无条目） |
| 该源文件缺失 | `partial` | 176 | `failed`，0 条 |

**其它 15 个源在三种场景下完全一致 = True** —— 单个源出问题不影响任何其它源。

**退出码语义验证**：含失败源时退出码 **0**（`partial` 是正常结论）；
仅当「全部源都失败」才返回 **1**（实测 `--only google-ai` 且网络不可达时返回 1）。

**生产环境真实发生过的失败隔离**（`data/runs.json` 原始记录）：

- 共 **6 次** `overall=partial`；
- 最早一次 `run_id=20260916T115115Z-31c36c`，失败源 **1 个**：
  `google-ai`，`error="timeout：请求超时"`（中国大陆直连 `blog.google` TLS 握手无响应）；
- **其余 15 个源照常工作**，该轮仍正常写入库中 226 条。

> ⚠️ 注意：`google-ai` 的失败是**间歇性**的。本次复测时它返回 `http=200`、抓回 20 条
> （网络/代理状态变化）。因此「失败隔离」用上面的**离线确定性实验**作为主要证据，
> 生产记录只作为真实发生过的佐证。

### 1.4 任务配置及运行记录 —— 配置已验证，定时触发未验证 ⚠️

**配置**（`.github/workflows/update.yml`，已写入仓库）：

| 项 | 值 |
| --- | --- |
| 触发 | `schedule: cron "0 22 * * *"`（UTC）= **北京时间每日 06:00** + `workflow_dispatch` 手动触发 |
| 并发 | `group: daily-update`、`cancel-in-progress: false`（两个实例同写 `data/` 会互相覆盖，故排队而不取消） |
| 权限 | `contents: write`、`pages: write`、`id-token: write` |
| 超时 | `timeout-minutes: 30`（避免单个源卡死拖到 6 小时默认上限） |
| 步骤 | 检出 → 装 Python 3.11 → 装依赖 → **采集** → **跑测试** → **构建并校验单文件** → 提交 `data/` → 上传 Pages 产物 → 部署 Pages |
| 时区换算 | `0 22 * * *` UTC = 北京次日 06:00，在中文读者起床前完成当天更新 |

**运行记录**：`data/runs.json` 保留**开发期间全部 15 次运行**，未做任何清理。
每次运行含 `run_id`、`started_at`/`finished_at`/`duration_ms`、`trigger`、`offline`、
逐源结果（`status`/`http_status`/`items_*`/`error`）与整轮计数。

**已验证的是**：手动触发时执行的**同一条命令**（`python collect.py --trigger cron`）已在本地跑通，
且 `trigger` 字段被如实写为 `cron`。
**未验证的是**：GitHub 上的 `schedule` 真正按点触发过（需要在仓库上线后才生效）。

### 1.5 单元与集成测试 —— 已验证 ✅

```bash
python -m pytest tests/ -q
# → 205 passed in 5.19s
```

**205 项全部离线**（不访问网络），约 5.5 秒跑完。覆盖：日期解析（5 种实测格式）、去重、
幂等与原子写、事件聚合（含误聚回归用例）、打分与入选规则、配置健全性、配图提取、
离线逐字节可复现。CI 中每次 push 都会跑（`.github/workflows/check.yml`）。

### 1.6 前端真实交互 —— 已验证 ✅

```bash
python tools/verify_ui.py
# → 39 / 39 通过
```

用无头 Chrome 渲染真实页面后**真的去点**：筛选标签、来源、语言徽标、主题按钮、「清除全部」，
断言**实际筛出来的集合**是否正确（而不是只看 DOM 里有没有生成元素）。
样例断言：`清除后回到全量 [231 / 231]`、`搜索生效且为子集 [GPT → 21]`、
`所有缩略图盒子尺寸一致 [96x72]`、`没有标题渲染超过两行 [44px ≤ 47px]`、`主题可切换 [dark → light]`。

另做了一次针对性的渲染核对：**231 张卡片的时间文案各出现恰好 1 次**，重点区 5 条各 1 次，
确认「时间显示两遍」缺陷已消除（曾有两个疑似命中，核实为标题/摘要正文里的「刚刚」等词，非缺陷）。

### 1.7 单文件交付物 —— 已验证 ✅

```bash
python build_site.py --check
# → 已生成 dist/index.html（371.7 KB）
# → 自包含校验通过：单文件无外部本地资源引用，数据已内联。
```

校验内容包括：占位符已被替换、产物中确实含条目数据、**不存在指向本地文件的 `<script src>`/`<link href>`**、
HTTP 取数兜底代码仍在（`dist/` 整体作为静态站目录时使用）。
双击 `dist/index.html` 与通过 HTTP 访问均能正常渲染（已实测）。

---

## 2. 未验证（Not Yet Verified）

| 项 | 为什么未验证 | 现有替代证据 |
| --- | --- | --- |
| **定时触发（`schedule`）真正按点运行** | 仓库上线前 GitHub 不会注册 cron | 手动触发的**同一条命令**已跑通；离线重放已验证幂等与确定性 |
| **GitHub Pages 线上可访问** | 尚未部署 | 本地 HTTP 预览已验证（4 个 URL 均 200）；`build_site --check` 已验证产物自包含 |
| **代理环境下的 `google-ai`** | 该源失败是间歇性的，本次复测恰好通了 | 失败路径已由离线确定性实验覆盖；生产记录里有 6 次真实的 `partial` |
| **大规模源下线时的观感** | 未构造该场景 | 无图源占多数时用源色占位块兜底（已实测 231 张卡片） |
| **CDN 防盗链下的外链图片** | 未穷举 | 前端对图片挂了 `onerror` 回退占位块；`loading=lazy` + `referrerPolicy=no-referrer` |
| **多源同时长时间挂死** | 只有 1 个源受影响，未构造 | `timeout-minutes: 30` 兜住整轮 |

---

## 3. 未完成（Not Done）

均为**有意不做**，不是遗漏。砍单依据见 README「时间预算与砍单顺序」。

| 项 | 为什么不做 |
| --- | --- |
| **AI 摘要生成** | 砍单顺序第 1 位。它是唯一会引入外部 API 依赖与密钥的功能，与「零成本、零密钥、干净环境可跑」冲突 |
| **抓取文章页 `og:image`** | 需要 231 次额外请求，并引入图片体积、防盗链、被反爬三类问题。当前配图率 21.2% 是现状约束下的天花板 |
| **源级超时预算 / 熔断** | 当前只有一个源受影响（约 +65 秒/轮）。多源出现该情况时应引入 |
| **中文纯标题的事件聚合** | 见 README 已知限制第 3 条：跨源互证目前依赖拉丁专名。宁可漏聚，也不虚报「多来源报道」 |
| **实时搜索 / 分页** | 静态站形态的固有限制，数据全量内联（231 条约 371.7 KB） |
| **测试题 §11 的两个待确认决策** | 「摘要先关后加」与「今日重点」入选口径已按计划实现，未再另行确认 |

---

## 4. 复现步骤（面试官可逐条执行）

```bash
# 0) 准备
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1) 真实采集（需要外网；大陆直连时 google-ai 可能失败，属正常）
python collect.py --trigger manual

# 2) 幂等与确定性（全离线，不访问网络）
python collect.py --input tests/fixtures/replay --now 2026-09-16T12:00:00Z --data-dir /tmp/a
python collect.py --input tests/fixtures/replay --now 2026-09-16T12:00:00Z --data-dir /tmp/b
cmp /tmp/a/news.json /tmp/b/news.json && echo 逐字节一致
python collect.py --input tests/fixtures/replay --now 2026-09-16T12:00:00Z --data-dir /tmp/a
#   → 新增 0 条，存储条数不变

# 3) 测试
pip install -r requirements-dev.txt
python -m pytest tests/ -q                        # → 205 passed

# 4) 构建单文件交付物并校验自包含
python build_site.py --check                      # → dist/index.html，校验通过

# 5) 前端真实点击校验（需要 Chrome；无 Chrome 时该步跳过，不影响其它步骤）
python tools/verify_ui.py                         # → 39 / 39 通过
```

> 说明：第 5 步依赖本机 Chrome（`C:\Program Files\Google\Chrome\Application\chrome.exe`）。
> 它是**可选的验收手段**，不属于运行时依赖。

---

## 5. 原始证据索引

| 文件 / 路径 | 内容 | 提交 |
| --- | --- | --- |
| `data/runs.json` | **15 次运行的完整记录**（逐源状态、错误、计数、耗时） | ✅ 已提交 |
| `data/update-status.json` | 最近一轮的整轮结论与逐源健康度 | ✅ 已提交 |
| `data/news.json` | 231 条归一化条目（含 `published_at` / `first_seen_at` / 配图 / 聚合） | ✅ 已提交 |
| `data/meta.json` | 页面摘要：最近运行、条目总数、源数、近 7 天成功率、5 条「今日重点」 | ✅ 已提交 |
| `tests/fixtures/replay/` | 17 个真实样本 + `manifest.json`（含 sha256），离线复现的基础 | ✅ 已提交 |
| `tools/audit_sources.py` | 源站审计：HTTP 状态、条目数、最新条目时间、时间可信度 | ✅ 已提交 |
| `tools/capture_fixtures.py` | 重新抓取离线样本并更新清单 | ✅ 已提交 |
| `tools/verify_ui.py` | 无头 Chrome 真实点击校验（39 项） | ✅ 已提交 |
| `dist/index.html` | 单文件交付物（数据已内联） | ✅ 已提交 |
| `DEVLOG.md` | 实际耗时、返工与排查过程 | ✅ 已提交 |