"""WebSocket 传输层：ws:// / wss:// 远程连接（websockets asyncio 新 API）

- WS 单消息上限 64MiB（对位 tungstenite 默认 max_message_size）；
  客户端不发 keepalive ping、不靠 pong 看门狗（对位 Rust connection.rs
  from_websocket 的 ping_interval None——pong watchdog 只接在 nova
  未镜像的 relay 面）
- 意外断线即收割底层承载（对位 Rust RpcClient::drop / 连接退役即
  transport.terminate()）：WS 后台关 socket——恢复层只造新候选，
  旧承载由本层收尾
"""

from __future__ import annotations

import asyncio
import json
import logging

import websockets.asyncio.client
import websockets.exceptions

from ..errors import AuthError, ConnectionError
from .base import _JsonRpcTransport
from .gate import _gate_inbound_message

logger = logging.getLogger(__name__)


class WebSocketTransport(_JsonRpcTransport):
    """WebSocket 传输层，负责连接管理和 JSON-RPC 消息收发"""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        max_payload: int = 64 * 1024 * 1024,
        request_timeout: float = 30.0,
    ):
        super().__init__(request_timeout)
        self.url = url
        self.token = token
        #: 传输层单消息上限（websockets max_size；默认对位 tungstenite 的
        #: max_message_size=64MiB；类别限流见 `_gate_inbound_message`——
        #: 两道口子语义不同，这里只兜底帧尺寸）
        self.max_payload = max_payload
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._receive_task: asyncio.Task | None = None
        self._abort_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            self._ws = await websockets.asyncio.client.connect(
                self.url,
                additional_headers=headers,
                max_size=self.max_payload,
                # 客户端不发 keepalive ping、不靠 pong 看门狗（对位 Rust
                # connection.rs from_websocket 的 ping_interval None——
                # websockets 库默认 20s 一发，须显式关掉；pong watchdog
                # 只接在 nova 未镜像的 relay 面）
                ping_interval=None,
            )
        except websockets.exceptions.InvalidStatus as e:
            if e.response.status_code == 401:
                raise AuthError(
                    "authentication failed: invalid or missing token"
                ) from e
            raise ConnectionError(f"failed to connect to {self.url}: {e}") from e
        except Exception as e:
            raise ConnectionError(f"failed to connect to {self.url}: {e}") from e

        self._closed = False
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def disconnect(self) -> None:
        """断开连接（主动关闭——不触发 on_disconnect）"""
        self._closed = True
        self._cancel_inbound_tasks()
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._abort_task is not None:
            try:
                await self._abort_task
            except Exception:
                pass
            self._abort_task = None
        await self._drain_inbound_tasks()
        self._fail_pending()

    async def _send_message(self, message: dict) -> None:
        if self._ws is None or self._closed:
            raise ConnectionError("not connected")
        # allow_nan=False：NaN/Infinity 非合法 JSON（serde_json 序列化非有限
        # 浮点为 null；py 的 json 默认印裸 NaN，服务端词法拒收判连接级致命）
        # ——本地抛错不上线
        await self._ws.send(json.dumps(message, allow_nan=False))

    async def _receive_loop(self) -> None:
        """接收消息循环；循环意外结束（对端关闭/读失败/限流/畸形）即断线"""
        reason: str | None = None
        try:
            assert self._ws is not None
            async for raw in self._ws:
                if self._closed:
                    return  # 主动 disconnect：收尾归 disconnect()，不触发回调
                payload = raw if isinstance(raw, (bytes, bytearray)) else raw.encode()
                message, fatal = _gate_inbound_message(payload)
                if fatal is not None:
                    reason = fatal
                    break
                if "method" in message:
                    if message.get("id") is not None:
                        # 服务端反向请求：准入 + 并发执行，接收循环不堵
                        self._dispatch_server_request(message)
                        if self._closed:
                            return  # 准入违规已标记断线（回调已触发）
                    else:
                        await self._run_notification_handlers(message)
                else:
                    self._resolve_response(message)
        except websockets.exceptions.ConnectionClosed as e:
            reason = str(e)
        except Exception as e:  # 读路径其他失败同样传播为断线
            reason = str(e)
            logger.warning("websocket receive loop failed: %s", e)
        finally:
            if not self._closed:
                self._mark_disconnected(reason)

    def _abort_connection(self) -> None:
        """后台关闭 socket，接收循环随即退出（对位 Rust close_transport 的
        transport task abort）"""
        ws = self._ws
        if ws is None or self._abort_task is not None:
            return

        async def close() -> None:
            try:
                await ws.close()
            except Exception:
                logger.debug("closing disconnected websocket failed", exc_info=True)

        self._abort_task = asyncio.create_task(close())

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._closed
