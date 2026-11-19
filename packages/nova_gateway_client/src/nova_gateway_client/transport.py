"""HTTP 请求装配、重试、超时与连接资源管理。"""

from __future__ import annotations

import asyncio
import inspect
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx
from nova_gateway_protocol import (
    GATEWAY_IDEMPOTENCY_HEADER,
    GATEWAY_PROTOCOL_HEADER,
    GATEWAY_PROTOCOL_VERSION,
    GATEWAY_REQUEST_ID_HEADER,
    StreamRequest,
)

from .errors import GatewayConnectionError, GatewayTimeoutError

ResponseObserver = Callable[[httpx.Response], Any]
"""响应观察钩子；返回普通值或 awaitable（仅观察，不改写响应）。"""
SleepFn = Callable[[float], Awaitable[None]]
JitterFn = Callable[[], float]

_CONNECT_TIMEOUT_S = 10.0
_DEFAULT_STREAM_TIMEOUT_S = 300.0
_INITIAL_RETRY_DELAY_S = 0.5
_MAX_RETRY_DELAY_S = 60.0


def request_timeout(knob_s: Optional[float]) -> httpx.Timeout:
    """缺省 300s，0 关闭 read/write 相，connect/pool 仍有界。"""
    read = _DEFAULT_STREAM_TIMEOUT_S if knob_s is None else knob_s or None
    return httpx.Timeout(
        connect=_CONNECT_TIMEOUT_S,
        pool=_CONNECT_TIMEOUT_S,
        read=read,
        write=read,
    )


def retry_after_ms(headers: httpx.Headers) -> Optional[int]:
    """按毫秒、秒、HTTP 日期顺序解析 Retry-After。"""
    milliseconds = headers.get("retry-after-ms")
    if milliseconds is not None:
        try:
            return max(0, int(float(milliseconds)))
        except (TypeError, ValueError):
            pass
    seconds = headers.get("retry-after")
    if seconds is not None:
        try:
            return max(0, int(float(seconds) * 1000))
        except (TypeError, ValueError):
            pass
        try:
            parsed = parsedate_to_datetime(seconds)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(
                0, int((parsed - datetime.now(timezone.utc)).total_seconds() * 1000)
            )
        except (TypeError, ValueError):
            pass
    return None


@dataclass(frozen=True, kw_only=True)
class TransportResult:
    response: httpx.Response
    retries_taken: int


class RequestExecutor:
    """可变 HTTP 执行器；持有共享 client、在途响应与关闭事件。"""

    def __init__(
        self,
        *,
        api_key: Optional[str],
        http_client: Optional[httpx.AsyncClient],
        default_timeout: Optional[float],
        default_headers: Optional[Dict[str, str]],
        max_retries: int,
        max_retry_delay_ms: int,
        sleep_fn: SleepFn,
        jitter_fn: JitterFn,
    ) -> None:
        if max_retries < 0 or max_retry_delay_ms < 0:
            raise ValueError("Retry counts and delays must be non-negative")
        self.api_key = api_key
        self._client = http_client or httpx.AsyncClient(
            timeout=request_timeout(default_timeout)
        )
        self._owns_client = http_client is None
        self.default_timeout = default_timeout
        self.default_headers = dict(default_headers or {})
        self.max_retries = max_retries
        self.max_retry_delay_ms = max_retry_delay_ms
        self._sleep_fn = sleep_fn
        self._jitter_fn = jitter_fn
        self._responses: set[httpx.Response] = set()
        self._closed_event = asyncio.Event()
        self._closed = False

    @property
    def closed_event(self) -> asyncio.Event:
        return self._closed_event

    def check_open(self) -> None:
        if self._closed:
            raise RuntimeError("NovaGatewayClient is closed")

    def _headers(self, method: str, extra: Optional[Dict[str, str]]) -> Dict[str, str]:
        request_id = uuid.uuid4().hex
        headers = {
            **self.default_headers,
            "Accept": "application/json",
            GATEWAY_PROTOCOL_HEADER: GATEWAY_PROTOCOL_VERSION,
            GATEWAY_REQUEST_ID_HEADER: request_id,
        }
        if method.lower() != "get":
            headers[GATEWAY_IDEMPOTENCY_HEADER] = request_id
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if extra:
            headers.update(extra)
        return headers

    def _should_retry(self, response: httpx.Response, attempt: int) -> bool:
        if attempt >= self.max_retries:
            return False
        directive = response.headers.get("x-should-retry")
        if directive == "true":
            return True
        if directive == "false":
            return False
        return response.status_code in (408, 409, 429) or response.status_code >= 500

    async def _sleep(self, attempt: int, headers: Optional[httpx.Headers]) -> None:
        retry_after = retry_after_ms(headers) if headers is not None else None
        if retry_after is not None:
            delay = retry_after / 1000
        else:
            delay = min(
                _INITIAL_RETRY_DELAY_S * (2**attempt),
                _MAX_RETRY_DELAY_S,
            )
            delay *= 1 - 0.25 * self._jitter_fn()
        if self.max_retry_delay_ms > 0:
            delay = min(delay, self.max_retry_delay_ms / 1000)
        await self._sleep_fn(max(0, delay))

    async def execute(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> TransportResult:
        """执行完整响应请求；只对瞬时失败重试。"""
        self.check_open()
        request_headers = self._headers(method, headers)
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=request_headers,
                    params=params,
                    timeout=request_timeout(self.default_timeout),
                )
            except httpx.TimeoutException as error:
                if attempt < self.max_retries:
                    await self._sleep(attempt, None)
                    continue
                request = self._client.build_request(
                    method, url, headers=request_headers, params=params
                )
                raise GatewayTimeoutError(request) from error
            except httpx.TransportError as error:
                if attempt < self.max_retries:
                    await self._sleep(attempt, None)
                    continue
                request = self._client.build_request(
                    method, url, headers=request_headers, params=params
                )
                raise GatewayConnectionError(request) from error
            if self._should_retry(response, attempt):
                await response.aclose()
                await self._sleep(attempt, response.headers)
                continue
            return TransportResult(response=response, retries_taken=attempt)
        raise AssertionError("unreachable")

    async def open_stream(
        self,
        url: str,
        request: StreamRequest,
        *,
        on_response: Optional[ResponseObserver],
    ) -> TransportResult:
        """打开 SSE 响应；仅在读取事件前重试，绝不重放已消费事件。"""
        self.check_open()
        http_request = self._client.build_request(
            "POST",
            url,
            headers={
                **self._headers("post", None),
                "Content-Type": "application/json",
            },
            json=request.dump_wire(exclude_none=True),
            timeout=request_timeout(
                self.default_timeout
                if request.options.timeout is None
                else request.options.timeout
            ),
        )
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.send(http_request, stream=True)
            except httpx.TimeoutException as error:
                if attempt < self.max_retries:
                    await self._sleep(attempt, None)
                    continue
                raise GatewayTimeoutError(http_request) from error
            except httpx.TransportError as error:
                if attempt < self.max_retries:
                    await self._sleep(attempt, None)
                    continue
                raise GatewayConnectionError(http_request) from error
            if on_response is not None:
                try:
                    observed = on_response(response)
                    if inspect.isawaitable(observed):
                        await observed
                except BaseException:
                    await response.aclose()
                    raise
            if self._should_retry(response, attempt):
                await response.aclose()
                await self._sleep(attempt, response.headers)
                continue
            self._responses.add(response)
            return TransportResult(response=response, retries_taken=attempt)
        raise AssertionError("unreachable")

    def discard_response(self, response: httpx.Response) -> None:
        """流驱动结束时解除在途登记，关闭仍由驱动负责。"""
        self._responses.discard(response)

    async def close(self) -> None:
        """幂等中断全部在途响应；外部注入的 client 由调用方关闭。"""
        if self._closed:
            return
        self._closed = True
        self._closed_event.set()
        responses = list(self._responses)
        if responses:
            await asyncio.gather(
                *(response.aclose() for response in responses),
                return_exceptions=True,
            )
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "JitterFn",
    "RequestExecutor",
    "SleepFn",
    "ResponseObserver",
    "TransportResult",
    "request_timeout",
    "retry_after_ms",
]
