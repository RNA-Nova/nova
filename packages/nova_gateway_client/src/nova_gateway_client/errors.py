"""独立 SDK 的 HTTP、连接、协议与断连异常。"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from nova_gateway_protocol import GATEWAY_REQUEST_ID_HEADER
from nova_protocol import NovaErrorKind


class GatewayError(RuntimeError):
    """独立 SDK 的公共异常基类，不强制下游统一捕获。"""


class GatewayHttpError(GatewayError):
    """非 2xx HTTP 响应，保留响应、错误体、分类、退避与 request ID。"""

    def __init__(
        self,
        message: str,
        response: httpx.Response,
        *,
        body: Any = None,
        kind: Optional[NovaErrorKind] = None,
        retry_after_ms: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = response.status_code
        self.response = response
        self.body = body
        self.kind = kind
        self.retry_after_ms = retry_after_ms
        self.request_id = response.headers.get(GATEWAY_REQUEST_ID_HEADER)


class GatewayConnectionError(GatewayError):
    """连接阶段失败，保留原始请求与 cause 链。"""

    def __init__(
        self, request: httpx.Request, message: str = "Connection error."
    ) -> None:
        super().__init__(message)
        self.request = request


class GatewayTimeoutError(GatewayConnectionError):
    """连接、读取或写入超时。"""

    def __init__(self, request: httpx.Request) -> None:
        super().__init__(request, "Request timed out.")


class GatewayProtocolError(GatewayError):
    """响应体不符合公开协议。"""


class GatewayDisconnectedError(GatewayProtocolError):
    """流在公开终态前断开；AI 适配层据此映射 STREAM_DISCONNECTED。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.nova_error_kind = NovaErrorKind.STREAM_DISCONNECTED


class GatewayBadRequestError(GatewayHttpError):
    """HTTP 400。"""


class GatewayAuthenticationError(GatewayHttpError):
    """HTTP 401。"""


class GatewayPermissionDeniedError(GatewayHttpError):
    """HTTP 403。"""


class GatewayNotFoundError(GatewayHttpError):
    """HTTP 404。"""


class GatewayConflictError(GatewayHttpError):
    """HTTP 409。"""


class GatewayUnprocessableEntityError(GatewayHttpError):
    """HTTP 422。"""


class GatewayRateLimitError(GatewayHttpError):
    """HTTP 429。"""


class GatewayInternalServerError(GatewayHttpError):
    """HTTP 5xx。"""


def http_error(
    message: str,
    response: httpx.Response,
    *,
    body: Any = None,
    kind: Optional[NovaErrorKind] = None,
    retry_after_ms: Optional[int] = None,
) -> GatewayHttpError:
    """按状态码构造具名异常；未知 4xx 保留通用 HTTP 异常。"""
    error_type = {
        400: GatewayBadRequestError,
        401: GatewayAuthenticationError,
        403: GatewayPermissionDeniedError,
        404: GatewayNotFoundError,
        409: GatewayConflictError,
        422: GatewayUnprocessableEntityError,
        429: GatewayRateLimitError,
    }.get(
        response.status_code,
        GatewayInternalServerError if response.status_code >= 500 else GatewayHttpError,
    )
    return error_type(
        message,
        response,
        body=body,
        kind=kind,
        retry_after_ms=retry_after_ms,
    )


__all__ = [
    "GatewayAuthenticationError",
    "GatewayBadRequestError",
    "GatewayConflictError",
    "GatewayConnectionError",
    "GatewayDisconnectedError",
    "GatewayError",
    "GatewayHttpError",
    "GatewayInternalServerError",
    "GatewayNotFoundError",
    "GatewayPermissionDeniedError",
    "GatewayProtocolError",
    "GatewayRateLimitError",
    "GatewayTimeoutError",
    "GatewayUnprocessableEntityError",
    "http_error",
]
