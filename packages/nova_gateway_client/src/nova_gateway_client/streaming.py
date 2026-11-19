"""公开 SSE 事件流对象、协议校验与独立生命周期。"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Awaitable, Callable, Optional

import httpx
from nova_gateway_protocol import (
    GATEWAY_EVENT_ADAPTER,
    GATEWAY_PROTOCOL_HEADER,
    GATEWAY_PROTOCOL_MAJOR,
    GATEWAY_REQUEST_ID_HEADER,
    GatewayDone,
    GatewayError,
    StreamRequest,
    protocol_major,
)
from nova_gateway_protocol.events import GatewayAssistantMessageEvent

from .errors import GatewayDisconnectedError, GatewayProtocolError
from .transport import RequestExecutor, ResponseObserver


class GatewayEventStream:
    """单次 SSE 调用的公开句柄；首次消费时建连，支持独立关闭。"""

    def __init__(
        self,
        *,
        executor: RequestExecutor,
        url: str,
        request: StreamRequest,
        on_response: Optional[ResponseObserver],
        status_handler: Callable[[httpx.Response], Awaitable[None]],
    ) -> None:
        self.response: Optional[httpx.Response] = None
        self.retries_taken = 0
        self._executor = executor
        self._url = url
        self._request = request
        self._on_response = on_response
        self._status_handler = status_handler
        self._close_event = asyncio.Event()
        self._closed = False
        self._iterator: Optional[AsyncIterator[GatewayAssistantMessageEvent]] = None

    @property
    def request(self) -> Optional[httpx.Request]:
        return self.response.request if self.response is not None else None

    @property
    def status_code(self) -> Optional[int]:
        return self.response.status_code if self.response is not None else None

    @property
    def request_id(self) -> Optional[str]:
        if self.response is None:
            return None
        return self.response.headers.get(GATEWAY_REQUEST_ID_HEADER)

    @property
    def closed(self) -> bool:
        return self._closed or (self.response is not None and self.response.is_closed)

    def __aiter__(self) -> "GatewayEventStream":
        return self

    async def __anext__(self) -> GatewayAssistantMessageEvent:
        if self._closed:
            raise StopAsyncIteration
        if self._iterator is None:
            self._iterator = self._iterate()
        return await self._iterator.__anext__()

    async def __aenter__(self) -> "GatewayEventStream":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        """幂等关闭本次流，不影响同 client 的其他请求。"""
        if self._closed:
            return
        self._closed = True
        self._close_event.set()
        if self.response is not None:
            await self.response.aclose()

    def _check_protocol(self, response: httpx.Response) -> None:
        value = response.headers.get(GATEWAY_PROTOCOL_HEADER)
        if value is None:
            return
        try:
            major = protocol_major(value)
        except ValueError as error:
            raise GatewayProtocolError("Invalid gateway protocol version") from error
        if major != GATEWAY_PROTOCOL_MAJOR:
            raise GatewayProtocolError(f"Unsupported gateway protocol major: {major}")

    async def _iterate(self) -> AsyncIterator[GatewayAssistantMessageEvent]:
        result = await self._executor.open_stream(
            self._url, self._request, on_response=self._on_response
        )
        response = result.response
        self.response = response
        self.retries_taken = result.retries_taken
        saw_terminal = False
        client_closed = asyncio.create_task(self._executor.closed_event.wait())
        stream_closed = asyncio.create_task(self._close_event.wait())
        try:
            if not 200 <= response.status_code < 300:
                await self._status_handler(response)
            self._check_protocol(response)
            lines = response.aiter_lines()
            while True:
                next_line = asyncio.ensure_future(lines.__anext__())
                finished, _ = await asyncio.wait(
                    {next_line, client_closed, stream_closed},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stream_closed in finished:
                    next_line.cancel()
                    try:
                        await next_line
                    except asyncio.CancelledError:
                        pass
                    return
                if client_closed in finished:
                    next_line.cancel()
                    try:
                        await next_line
                    except asyncio.CancelledError:
                        pass
                    raise GatewayDisconnectedError(
                        "Client closed before the response completed"
                    )
                try:
                    line = next_line.result()
                except StopAsyncIteration:
                    break
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if not data:
                    continue
                try:
                    event = GATEWAY_EVENT_ADAPTER.validate_python(json.loads(data))
                except (json.JSONDecodeError, ValueError) as error:
                    raise GatewayProtocolError("Invalid gateway event") from error
                if isinstance(event, (GatewayDone, GatewayError)):
                    saw_terminal = True
                yield event
                if saw_terminal:
                    break
            if not saw_terminal:
                raise GatewayDisconnectedError(
                    "Connection closed by gateway before the response completed"
                )
        finally:
            for task in (client_closed, stream_closed):
                task.cancel()
            await asyncio.gather(client_closed, stream_closed, return_exceptions=True)
            self._executor.discard_response(response)
            await response.aclose()


__all__ = ["GatewayEventStream"]
