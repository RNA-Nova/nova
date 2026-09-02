"""WebSocket 传输层：连接管理、JSON-RPC 消息收发"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.client import WebSocketClientProtocol

from .errors import AuthError, ConnectionError, ProtocolError, TimeoutError


class WebSocketTransport:
    """WebSocket 传输层，负责连接管理和 JSON-RPC 消息收发"""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        max_payload: int = 100 * 1024 * 1024,
        request_timeout: float = 30.0,
    ):
        self.url = url
        self.token = token
        self.max_payload = max_payload
        self.request_timeout = request_timeout
        self._ws: WebSocketClientProtocol | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._notification_handlers: list[Callable[[dict], Awaitable[None]]] = []
        self._receive_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            self._ws = await websockets.connect(
                self.url,
                additional_headers=headers,
                max_size=self.max_payload,
            )
        except websockets.exceptions.InvalidStatusCode as e:
            if e.status_code == 401:
                raise AuthError(
                    "authentication failed: invalid or missing token"
                ) from e
            raise ConnectionError(f"failed to connect to {self.url}: {e}") from e
        except Exception as e:
            raise ConnectionError(f"failed to connect to {self.url}: {e}") from e

        self._closed = False
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def disconnect(self) -> None:
        """断开连接"""
        self._closed = True
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None

        # 取消所有 pending 请求
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("connection closed"))
        self._pending.clear()

    async def send_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        """发送 JSON-RPC 请求并等待响应"""
        if not self._ws:
            raise ConnectionError("not connected")

        self._request_id += 1
        request_id = self._request_id

        message = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        try:
            await self._ws.send(json.dumps(message))
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise TimeoutError(f"request {method} timed out") from None
        except Exception as e:
            self._pending.pop(request_id, None)
            raise ConnectionError(f"failed to send request {method}: {e}") from e

    async def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        """发送 JSON-RPC 通知（无响应）"""
        if not self._ws:
            raise ConnectionError("not connected")

        message = {
            "method": method,
            "params": params or {},
        }
        await self._ws.send(json.dumps(message))

    def on_notification(self, handler: Callable[[dict], Awaitable[None]]) -> None:
        """注册通知处理器"""
        self._notification_handlers.append(handler)

    async def _receive_loop(self) -> None:
        """接收消息循环"""
        try:
            async for raw in self._ws:
                if self._closed:
                    break
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if "id" in message and message["id"] is not None:
                    # 响应
                    request_id = message["id"]
                    future = self._pending.pop(request_id, None)
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(
                                ProtocolError(
                                    f"JSON-RPC error {error.get('code')}: {error.get('message')}"
                                )
                            )
                        else:
                            future.set_result(message.get("result"))
                elif "method" in message:
                    # 通知
                    for handler in self._notification_handlers:
                        try:
                            await handler(message)
                        except Exception:
                            pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._closed = True
            # 清理所有 pending
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("connection closed"))
            self._pending.clear()

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._closed
