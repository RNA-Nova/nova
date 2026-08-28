"""跨连接反向原语路由（连接化形态 + 作用域仲裁）。

寻址语义（连接化）：

- **发起方优先**：当前请求上下文（``current_connection``）指向的连接
  宣告了该能力，则只发给它——TUI 发起的命令，确认框弹给 TUI；
- **无归属/发起方无能力**：广播给全部"已 initialize 且宣告该能力"的
  连接，**首响应胜出**，其余地址收到 ``ui/cancel`` 撤框；
- **无有能力连接**：按 cancelled 解决（基线降级语义不变）。

生命周期语义（作用域仲裁，对齐 codex ``cancel_requests_for_thread``）：

- 每条在飞请求记**作用域归属**（``run:<id>`` / ``session:<id>`` /
  ``global``——缺省 global）进台账；
- **仲裁清扫**：``cancel_scope(scope)`` 按归属批量终结——协程按
  cancelled 解决 + 按台账 addressed 集发 ``ui/cancel`` 撤框。挂接点：
  服务器对 ``agent_end``（run 死）/``session_replaced``（会话死）调用；
- **超时**：无全局默认（永等——pi/codex 对齐）；``timeout_ms``
  per-request 保留为业务语义（OAuth 授权链接会过期），**到点必须配
  撤框**（cancelled + ``ui/cancel``）；
- **断连**：连接关闭时其独家持有的 pending 按 cancelled 收尾；
- **取消**：宿主 task 被 cancelRequest 取消 → CancelledError 路径发
  ``ui/cancel`` 后 re-raise（不吞取消语义）——与仲裁不重叠：一个管
  "客户端主动取消单个调用"，一个管"宿主死亡"。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional, Set

from nova_harness.core.types.ui.context import UIContext
from nova_harness.core.types.ui.primitives import UIResponse
from nova_harness.server.connection import (
    Connection,
    ConnectionRegistry,
    current_connection,
)


class _Pending:
    """一条在飞 UI 请求：应答 future + 已送达连接 id 集 + 作用域归属。"""

    __slots__ = ("future", "addressed", "scope")

    def __init__(self, future: asyncio.Future, addressed: Set[int], scope: str) -> None:
        self.future = future
        self.addressed = addressed
        self.scope = scope


class RoutingUIContext(UIContext):
    """按连接路由 + 作用域仲裁的 UIContext（session 经 ServerState.ui_context 持有）。"""

    def __init__(
        self,
        connections: ConnectionRegistry,
        default_timeout: Optional[float] = None,
    ) -> None:
        self._connections = connections
        # 无全局默认超时（None = 永等——挂起请求的终结归作用域仲裁/断连/
        # cancelRequest，不再由人造时限兜底）
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
        self, method: str, params: Dict[str, Any], *, scope: Optional[str] = None
    ) -> UIResponse:
        """发送一个需要响应的 UI request（寻址见模块 docstring）。

        ``scope`` 为作用域归属（缺省 ``global``）——run/session 死亡时
        仲裁按归属批量终结在飞请求。
        ``params.timeout_ms``（可选）为 per-request 业务超时（pi 对话框
        timeout 语义对位）：到点按 cancelled 解决并向全部已送达连接发
        ``ui/cancel`` 撤框。
        """
        targets = self._pick_targets(method)
        if not targets:
            return UIResponse(cancelled=True)

        # per-request 业务超时：唯一合法的超时形态（无全局默认）
        timeout_ms = params.get("timeout_ms")
        timeout: Optional[float] = (
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
        self._pending[request_id] = _Pending(future, addressed, scope or "global")

        try:
            for conn in targets:
                await conn.send(payload)
            if timeout is not None:
                result = await asyncio.wait_for(future, timeout=timeout)
            else:
                result = await future
            return self._normalize_response(result)
        except asyncio.TimeoutError:
            # 业务超时到期：协程按 cancelled 解决 + 撤框（框不超时自撤）
            await self._send_cancel(request_id)
            return UIResponse(cancelled=True)
        except asyncio.CancelledError:
            # 宿主 task 被取消（如 cancelRequest）：撤销前端对话框后再传播
            await self._send_cancel(request_id)
            raise
        finally:
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
    # 仲裁与生命周期
    # ------------------------------------------------------------------

    def cancel_scope(self, scope: str) -> None:
        """按作用域批量终结在飞请求（仲裁清扫——codex 直译）。

        挂接点：``agent_end``（``run:<id>``）/``session_replaced``
        （``session:<id>`` + 全部 run 级）。每条 pending：协程按
        cancelled 解决 + 按台账 addressed 集发 ``ui/cancel`` 撤框。
        """
        for request_id in [rid for rid, p in self._pending.items() if p.scope == scope]:
            pending = self._pending.pop(request_id, None)
            if pending is None:
                continue
            if not pending.future.done():
                pending.future.set_result(UIResponse(cancelled=True))
            frame = {
                "jsonrpc": "2.0",
                "method": "ui/cancel",
                "params": {"id": request_id},
            }
            for conn_id in pending.addressed:
                conn = self._find(conn_id)
                if conn is not None:
                    conn.send_from_sync(frame)

    def cancel_session_scopes(self) -> None:
        """会话死亡的仲裁：run/session 两级全清（global 不动——OAuth 这类
        自治流程不随会话死）。"""
        for scope in [p.scope for p in self._pending.values() if p.scope != "global"]:
            self.cancel_scope(scope)

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

        广播件摘掉该地址即可，地址集空才收尾。
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
