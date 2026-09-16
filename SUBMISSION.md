# 提交消息（按测试题 §07 格式）

> 直接复制下面代码块的内容作为提交消息。`<...>` 处需要替换成实际值。

```
姓名：wys　　实际投入时间：7.1 小时
演示地址：https://<GitHub 用户名>.github.io/ai-intel-station/
源码地址／文件：https://github.com/<GitHub 用户名>/ai-intel-station
未完成或未验证事项：
  1. 定时触发未验证 —— GitHub Actions 的 schedule(每日 UTC 22:00) 已配置好，
     但仓库上线前不会真正触发。手动执行同一条命令(collect.py --trigger cron)已跑通。
  2. Pages 线上可访问性未验证 —— 本地 HTTP 预览已验证(4 个 URL 均 200)，
     线上部署后需再看一眼。
  3. 有意未做：AI 摘要生成(砍单顺序第 1 位，避免引入外部 API 与密钥)、
     抓取文章页 og:image(需 231 次额外请求)、源级超时熔断、
     中文纯标题的跨源事件聚合。
  4. 已知限制：配图率 21.2%(49/231) 是「零额外请求」约束下的天花板；
     近 7 天成功率 0.6 是被 Google AI Blog 在中国大陆直连不可达拖低的(间歇性)。
```

---

## 各项依据（供核对，不必提交）

| 字段 | 值 | 依据 |
| --- | --- | --- |
| 实际投入时间 | **7.1 小时** | P0–P4 首版 5.1h + P5 体验改造 2.0h，逐段记录在 `DEVLOG.md` 总览表 |
| 演示地址 | GitHub Pages | `.github/workflows/update.yml` 末两步上传并部署 Pages 产物 |
| 源码地址 | GitHub 仓库 | 含 `README.md`、`DEVLOG.md`、`VERIFICATION.md`、`docs/PLAN.md` 与全部源码、测试、离线样本 |
| 未完成或未验证 | 见上 | 逐条对应 `VERIFICATION.md` 第 2、3 节 |

## 交付材料对照（测试题 §05 提交清单）

| 测试题要求 | 对应文件 |
| --- | --- |
| ① 可访问的网站 | GitHub Pages 演示地址（`dist/index.html`） |
| ② 完整源码 | 仓库根目录；依赖 `requirements.txt` / `requirements-dev.txt`；更新任务配置 `.github/workflows/`；**不含任何密钥**（本项目零配置、零密钥，故无需环境变量示例） |
| ③ 简短 README | `README.md` —— 本地运行、部署、技术选型理由、数据来源、更新方式、已知限制、成本与外部依赖 |
| ④ 开发说明 | `DEVLOG.md` —— 实际投入时间、主要开发工具与外部资源、两个关键决策、问题定位与返工过程 |
| ⑤ 验证记录 | `VERIFICATION.md` —— 真实数据获取、固定输入重复导入、单个来源失败、任务配置与运行记录，并明确列出「已验证 / 未验证 / 未完成」 |
| ⑥ 打包 html | `dist/index.html`（单文件，数据已内联，双击即可打开） |