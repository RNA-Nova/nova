"""构建期 HTTP 薄原语：单次 GET、非 200 抛错（pi 源加载同款）。"""

from __future__ import annotations

from typing import Any, Optional

import httpx

_CONNECT_POOL_TIMEOUT_S = 10.0
_READ_WRITE_TIMEOUT_S = 30.0
_REQUEST_TIMEOUT = httpx.Timeout(
    connect=_CONNECT_POOL_TIMEOUT_S,
    pool=_CONNECT_POOL_TIMEOUT_S,
    read=_READ_WRITE_TIMEOUT_S,
    write=_READ_WRITE_TIMEOUT_S,
)


class FetchError(RuntimeError):
    """非 200 响应。"""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


async def fetch_json(url: str) -> Any:
    """执行一次 GET JSON；固定分相超时，不内置重试或额外退避策略。"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url, headers={"Accept": "application/json"}, timeout=_REQUEST_TIMEOUT
        )
    if response.status_code != 200:
        raise FetchError(
            f"GET {url} returned {response.status_code}",
            status_code=response.status_code,
        )
    return response.json()


__all__ = ["FetchError", "fetch_json"]
