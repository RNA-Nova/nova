"""跨连接反向原语路由（连接化重构后的 UIContext 实现）。

取代旧 ``TransportUIContext``（单传输直写）：现在 UI 请求/通知按连接
寻址，语义对齐 codex 的 server→client 请求扇出：

- **发起方优先**：当前请求上下文（``current_connection``）指向的连接
  宣告了该能力，则只发给它——TUI 发起的命令，确认框弹给 TUI；
- **无归属/发起方无能力**：广播给全部"已 initialize 且宣告该能力"的
  连接，**首响应胜出**，其余地址收到 ``ui/cancel`` 撤框；
- **无有能力连接**：按 cancelled 解决（基线降级语义不变）。

超时、abort 撤销、响应归一化语义与旧实现逐项一致。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional, Set

from nova_harness.core.rpc.connection import (
    Connection,
    ConnectionRegistry,
    current_connection,
)
from nova_harness.core.types.ui.context import UIContext
from nova_harness.core.types.ui.primitives import UIResponse


class _Pending:
    """一条在飞 UI 请求：应答 future + 已送达的连接 id 集（首响应胜出仲裁用）。"""

    __slots__ = ("future", "addressed")

    def __init__(self, future: asyncio.Future, addressed: Set[int]) -> None:
        self.future = future
        self.addressed = addressed


class RoutingUIContext(UIContext):
    """按连接路由的 UIContext（session 经 ServerState.ui_context 持有）。"""

    def __init__(
        self,
        connections: ConnectionRegistry,
        default_timeout: float = 300.0,
    ) -> None:
        self._connections = connections
        self._default_timeout = default_timeout
        self._pending: Dict[str, _Pending] = {}

    @property
    def capabilities(self) -> Set[str]:
        """全部已 initialize 连接的 UI 能力并集。"""
        caps: Set[str] = set()
        for conn in self._connections.initialized():
            caps |= conn.ui_capabilities
        return caps

    # ------------------------------------------------------------------
    # 寻址
    # ------------------------------------------------------------------

    def _pick_targets(self, method: str) -> List[Connection]:
        """有能力连接的寻址：发起方优先，否则全部候选（广播首响应胜出）。"""
        capable = [
            conn
            for conn in self._connections.initialized()
            if method in conn.ui_capabilities
        ]
        if not capable:
            return []
        origin = current_connection()
        if origin is not None and origin in capable:
            return [origin]
        return capable

    # ------------------------------------------------------------------
    # Core request/notify forwarding
    # ------------------------------------------------------------------

    async def request(
        self, method: str, params: Dict[str, Any], signal: Any = None
    ) -> UIResponse:
        """发送一个需要响应的 UI request（寻址见模块 docstring）。

        ``signal``（可选）为调用方的 abort 信号：abort 时向全部已送达
        连接发送 ``ui/cancel {id}`` 撤销帧，本次请求按 cancelled 解决。
        """
        targets = self._pick_targets(method)
        if not targets:
            return UIResponse(cancelled=True)

        # per-request 超时（params.timeout_ms，pi 对话框 timeout 语义的对位）
        timeout_ms = params.get("timeout_ms")
        timeout = (
            timeout_ms / 1000.0
            if isinstance(timeout_ms, (int, float)) and timeout_ms > 0
            else self._default_timeout
        )

        request_id = uuid.uuid4().hex
        payload = {
            "jsonrpc": "2.0",
            "method": "ui/request",
            "params": {
                "id": request_id,
                "component": {"componentType": method, **params},
            },
        }
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        addressed = {conn.id for conn in targets}
        self._pending[request_id] = _Pending(future, addressed)
        abort_task: Optional[asyncio.Task] = None

        async def _watch_abort() -> None:
            wait_fn = getattr(signal, "wait", None)
            if signal is None or not callable(wait_fn):
                return
            result = wait_fn()
            if result is not None and hasattr(result, "__await__"):
                await result
            # abort 胜出：撤销前端对话框并按 cancelled 收尾
            await self._send_cancel(request_id)
            if not future.done():
                future.set_result(UIResponse(cancelled=True))

        try:
            for conn in targets:
                await conn.send(payload)
            if signal is not None:
                abort_task = asyncio.create_task(_watch_abort())
            result = await asyncio.wait_for(future, timeout=timeout)
            return self._normalize_response(result)
        except asyncio.TimeoutError:
            return UIResponse(cancelled=True)
        except asyncio.CancelledError:
            # 宿主 task 被取消（如 cancelRequest）：撤销前端对话框后再传播
            await self._send_cancel(request_id)
            raise
        finally:
            if abort_task is not None:
                abort_task.cancel()
            self._pending.pop(request_id, None)

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        """发送一个不需要响应的 UI 通知（同 request 的寻址规则）。"""
        frame = {
            "jsonrpc": "2.0",
            "method": "ui/notify",
            "params": {"method": method, **params},
        }
        for conn in self._pick_targets(method):
            conn.send_from_sync(frame)

    # ------------------------------------------------------------------
    # 服务器回调（入站应答 / 连接生命周期）
    # ------------------------------------------------------------------

    def handle_response(
        self, conn: Connection, request_id: Optional[str], result: Any
    ) -> None:
        """处理某连接发来的 ``ui/response``：首响应胜出，败者收撤销帧。"""
        if not isinstance(request_id, str):
            return
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        if not pending.future.done():
            pending.future.set_result(result)
        # 其余已送达连接：弹窗撤下（尽力而为）
        for conn_id in pending.addressed - {conn.id}:
            other = self._find(conn_id)
            if other is not None:
                other.send_from_sync(
                    {
                        "jsonrpc": "2.0",
                        "method": "ui/cancel",
                        "params": {"id": request_id},
                    }
                )

    def connection_closed(self, conn: Connection) -> None:
        """连接关闭：该连接独家持有的 pending 按 cancelled 收尾。

        不做这个，前端断连时在飞的 request 会挂到默认超时（300s）——
        后端协程干等 5 分钟才拿到降级结果。广播件摘掉该地址即可，
        地址集空才收尾。
        """
        for request_id in [
            rid for rid, p in self._pending.items() if conn.id in p.addressed
        ]:
            pending = self._pending[request_id]
            pending.addressed.discard(conn.id)
            if not pending.addressed:
                if not pending.future.done():
                    pending.future.set_result(UIResponse(cancelled=True))
                self._pending.pop(request_id, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find(self, conn_id: int) -> Optional[Connection]:
        for conn in self._connections.all():
            if conn.id == conn_id:
                return conn
        return None

    async def _send_cancel(self, request_id: str) -> None:
        """向全部已送达连接发送 ``ui/cancel`` 撤销帧（尽力而为，失败静默）。"""
        pending = self._pending.get(request_id)
        if pending is None:
            return
        frame = {
            "jsonrpc": "2.0",
            "method": "ui/cancel",
            "params": {"id": request_id},
        }
        for conn_id in pending.addressed:
            conn = self._find(conn_id)
            if conn is not None:
                try:
                    await conn.send(frame)
                except Exception:
                    pass

    def _normalize_response(self, result: Any) -> UIResponse:
        """把前端返回的结果归一化为 UIResponse。"""
        if result is None:
            return UIResponse(cancelled=True)
        if isinstance(result, UIResponse):
            return result
        if isinstance(result, dict):
            if any(k in result for k in ("value", "cancelled", "confirmed")):
                return UIResponse.model_validate(result)
            return UIResponse(value=result)
        return UIResponse(value=result)


__all__ = ["RoutingUIContext"]
