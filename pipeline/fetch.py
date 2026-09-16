"""HTTP 抓取层。

统一 UA、超时、重试退避与响应体大小上限，并把失败**分类**为可诊断的 `kind`，
使运行报告能区分「源挂了」与「源格式变了」——这两者的处置方式完全不同。

`FixtureFetcher` 提供同签名的离线实现，让整条管线可在无网络环境下确定性重放，
这也是验收测试不依赖外网的前提。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from . import config

#: 可重试的状态码。4xx（除 429）不重试 —— 重试改不了「源站已下线」。
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})


class FetchError(Exception):
    """抓取失败，带可控的分类信息供运行报告使用。"""

    def __init__(self, message: str, *, kind: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status


@dataclass(frozen=True)
class FetchResult:
    """一次成功抓取的结果。"""

    url: str
    status: int
    content: bytes
    content_type: str
    duration_ms: int

    def text(self, encoding: str = "utf-8") -> str:
        """按 UTF-8 解码，失败时回退到宽松模式（源站偶尔声明错误的字符集）。"""
        try:
            return self.content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            return self.content.decode("utf-8", errors="replace")


class _RetryableStatus(Exception):
    """内部信号：本次响应可重试。"""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class Fetcher:
    """基于 httpx 的抓取器，复用一个连接池。"""

    def __init__(
        self,
        *,
        timeout: float = config.HTTP_TIMEOUT_SECONDS,
        retries: int = config.HTTP_RETRIES,
    ) -> None:
        self._retries = retries
        self._client = httpx.Client(
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, "
                          "application/json, text/xml;q=0.9, */*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )

    def get(self, url: str) -> FetchResult:
        """抓取一个 URL，失败抛 `FetchError`。重试仅在可重试错误上发生。"""
        last_error: FetchError | None = None

        for attempt in range(self._retries + 1):
            try:
                started = time.perf_counter()
                with self._client.stream("GET", url) as response:
                    status = response.status_code
                    content_type = response.headers.get("content-type", "")
                    if status in RETRYABLE_STATUS:
                        raise _RetryableStatus(status)
                    if status >= 400:
                        raise FetchError(
                            f"HTTP {status}",
                            kind="http_client" if status < 500 else "http_server",
                            http_status=status,
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > config.HTTP_MAX_BYTES:
                            raise FetchError(
                                f"响应体超过 {config.HTTP_MAX_BYTES} 字节上限",
                                kind="too_large",
                            )
                elapsed = int((time.perf_counter() - started) * 1000)
                return FetchResult(url, status, bytes(body), content_type, elapsed)

            except _RetryableStatus as exc:
                last_error = FetchError(f"HTTP {exc.status}", kind="http_server",
                                        http_status=exc.status)
            except httpx.TimeoutException:
                last_error = FetchError("请求超时", kind="timeout")
            except httpx.ConnectError as exc:
                last_error = FetchError(f"连接失败：{exc}", kind="connect")
            except httpx.TooManyRedirects:
                last_error = FetchError("重定向次数过多", kind="redirect")
            except httpx.HTTPError as exc:
                last_error = FetchError(f"传输错误：{exc}", kind="transport")

            if attempt < self._retries:
                time.sleep(config.HTTP_BACKOFF_SECONDS * (2**attempt))

        assert last_error is not None
        raise last_error

    def get_json(self, url: str) -> Any:
        """抓取并解析 JSON。解析失败归类为 `bad_json`，与网络失败区分开。"""
        result = self.get(url)
        try:
            return json.loads(result.content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FetchError(f"JSON 解析失败：{exc}", kind="bad_json",
                             http_status=result.status) from exc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class FetcherProtocol(Protocol):
    """抓取器接口，供离线实现替换。"""

    def get(self, url: str) -> FetchResult: ...

    def get_json(self, url: str) -> Any: ...

    def close(self) -> None: ...


class FixtureFetcher:
    """从本地目录读取响应，替代网络抓取。

    文件按 `{source_id}.*` 命名（如 `qbitai.xml`、`hn.json`）。
    同一个源在一次运行中要发多个请求时（HN 的双轨查询就是这种），按文件名字典序
    依次返回同名多份样本；只有一份样本时所有请求复用它。

    `bind()` 指定当前正在采集的源：HN 的查询 URL 带基于当前时间的 `created_at_i`，
    按 URL 反查源 id 不可行，必须由调用方显式指定。

    缺失样本文件抛 `FetchError(kind="missing_fixture")`，与网络失败走同一条错误分支，
    使离线重放与在线运行的控制流完全一致。
    """

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._bound: str | None = None
        self._counters: dict[str, int] = {}

    def bind(self, source_id: str) -> None:
        """声明接下来要采集的源 id。"""
        self._bound = source_id

    def _variants(self, source_id: str) -> list[Path]:
        matches = sorted(
            path for path in self._directory.glob(f"{source_id}.*") if path.suffix != ".tmp"
        )
        if not matches:
            raise FetchError(
                f"fixtures 目录缺少 {source_id} 的样本文件（{self._directory}）",
                kind="missing_fixture",
            )
        return matches

    def get(self, url: str) -> FetchResult:
        source_id = self._bound or self._source_id_for(url)
        variants = self._variants(source_id)
        index = self._counters.get(source_id, 0)
        self._counters[source_id] = index + 1
        path = variants[min(index, len(variants) - 1)]
        suffix = path.suffix.lstrip(".").lower()
        content_type = "application/json" if suffix == "json" else "application/xml"
        return FetchResult(url, 200, path.read_bytes(), content_type, 0)

    def get_json(self, url: str) -> Any:
        result = self.get(url)
        try:
            return json.loads(result.content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FetchError(f"JSON 解析失败：{exc}", kind="bad_json") from exc

    def close(self) -> None:
        return None

    def _source_id_for(self, url: str) -> str:
        for source in config.SOURCES:
            if source.url == url:
                return source.id
        raise FetchError(f"fixtures 目录无法识别该 URL：{url}", kind="missing_fixture")