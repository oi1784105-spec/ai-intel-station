"""每日 AI 情报站的采集管线。

模块分工：

- `config`     源注册表与全部可调参数
- `fetch`      网络层（超时 / 重试 / 分类错误）与离线重放实现
- `adapters`   四类源（rss / atom / hn / arxiv）的取数与解析
- `parse`      时间与文本归一化
- `normalize`  `RawItem` → `Article`
- `relevance`  通用科技源的 AI 相关性过滤
- `dedupe`     去重与聚合的判定原语
- `store`      原子写、幂等合并、滚动窗口
- `cluster`    事件聚合
- `score`      可解释打分与「今日重点」入选
- `report`     运行报告与控制台渲染
"""

__all__ = [
    "adapters",
    "cluster",
    "config",
    "dedupe",
    "fetch",
    "models",
    "normalize",
    "parse",
    "relevance",
    "report",
    "score",
    "store",
]