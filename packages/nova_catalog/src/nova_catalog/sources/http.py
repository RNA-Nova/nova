"""构建期 HTTP 薄原语（GET + 有界重试 + 非 200 raise）。

对位运行期 ``nova_ai.utils.http.fetch_json`` 的主干语义（pi
``fetchWithRetry`` 同款）：单发 4s 超时，传输错误与 408/425/429/5xx
至多重试 2 次（指数退避 500ms 起、2000ms 封顶）。构建期无 AbortSignal
概念（脚本可 Ctrl-C），故不引运行期的取消机制件。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

_TIMEOUT_S = 4.0
_MAX_RETRIES = 2
_RETRYABLE_STATUS = {408, 425, 429} | set(range(500, 600))
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 2.0


class FetchError(RuntimeError):
    """非 200 或重试耗尽（构建期响亮失败）。"""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


async def fetch_json(url: str, *, api_key: Optional[str] = None) -> Any:
    """GET JSON：重试 2 次（退避 0.5s→1s→2s 封顶），非 200 抛 FetchError。"""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    last_error: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                response = await client.get(url, headers=headers)
        except (httpx.TransportError, httpx.TimeoutException) as error:
            last_error = error
            if attempt >= _MAX_RETRIES:
                raise FetchError(f"GET {url} 传输失败: {error}") from error
            await asyncio.sleep(min(_BACKOFF_BASE_S * (attempt + 1), _BACKOFF_CAP_S))
            continue
        if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
            await asyncio.sleep(min(_BACKOFF_BASE_S * (attempt + 1), _BACKOFF_CAP_S))
            continue
        if response.status_code != 200:
            raise FetchError(
                f"GET {url} returned {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()
    raise FetchError(f"GET {url} 重试耗尽: {last_error}")


__all__ = ["FetchError", "fetch_json"]
