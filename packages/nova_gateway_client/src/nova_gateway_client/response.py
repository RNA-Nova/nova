"""带 HTTP 元数据的响应值对象。"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Generic, Optional, TypeVar

import httpx
from nova_gateway_protocol import GATEWAY_REQUEST_ID_HEADER

T = TypeVar("T")


@dataclass(frozen=True, kw_only=True)
class GatewayResponse(Generic[T]):
    """解析结果、原始响应与实际重试次数；不再返回散装元数据。"""

    data: T
    response: httpx.Response
    retries_taken: int = 0

    @property
    def status_code(self) -> int:
        return self.response.status_code

    @property
    def headers(self) -> httpx.Headers:
        return self.response.headers

    @property
    def request(self) -> httpx.Request:
        return self.response.request

    @property
    def request_id(self) -> Optional[str]:
        return self.response.headers.get(GATEWAY_REQUEST_ID_HEADER)

    @property
    def elapsed(self) -> datetime.timedelta:
        return self.response.elapsed


__all__ = ["GatewayResponse"]
