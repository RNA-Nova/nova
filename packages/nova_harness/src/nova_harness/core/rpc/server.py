"""Nova JSON-RPC 服务器（连接化形态）。

组合 ``MethodRegistry`` + ``ServerState`` + ``RoutingUIContext``，把
AgentSession 事件广播给全部已 initialize 的连接。

与旧 ``NovaServer`` 的区别（连接化重构）：

- **连接一等公民**：不再是"单 transport 单读循环"，而是连接注册表 +
  每连接读泵/写泵（见 ``connection.py``）；stdio 只是
  ``exit_on_close=True`` 的一条连接；
- **请求键复合化**：在飞请求按连接隔离（``conn.request_tasks``），
  两个客户端用相同 id 互不冲突，cancelRequest 只作用于本连接；
- **事件广播带 initialize 门**：握手完成的连接才收 ``agent/event``；
- **反向原语按连接寻址**：``ui/response`` / ``system/capabilities``
  按连接记账，弹窗路由归 ``RoutingUIContext``。

传输接入面：``add_connection`` 是唯一入口——stdio 直连。
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from nova_harness.core.rpc.connection import (
    Connection,
    ConnectionOrigin,
    ConnectionRegistry,
    _current_connection,
)
from nova_harness.core.rpc.protocol import (
    JSONRPCError,
    JsonRpcMessage,
    MethodRegistry,
    build_error,
    build_notification,
    parse_message,
)
from nova_harness.core.rpc.protocol.serialize import serialize_event, to_json_safe
from nova_harness.core.rpc.transport.base import Transport
from nova_harness.core.rpc.ui_context import RoutingUIContext
from nova_harness.core.types.ui.context import UIContext


class RpcServer:
    """多连接 JSON-RPC 服务器。

    用法（stdio 单客户端形态，TUI 子进程语义）::

        server = RpcServer(methods, state)
        await server.add_connection(StdioTransport(),
                                    origin=ConnectionOrigin.STDIO,
                                    exit_on_close=True)
        await server.run()   # 连接关闭或 shutdown() 时返回
    """

    def __init__(
        self,
        methods: MethodRegistry,
        state: Any,
        ui: Optional[UIContext] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        *,
        lag_interval: float = 1.0,
        lag_threshold_ms: float = 100.0,
    ) -> None:
        self._methods = methods
        self._state = state
        self._on_event = on_event
        self._connections = ConnectionRegistry()
        # UI 上下文单点接线：缺省建连接路由器并写回 state（原 cli 双接线合一）；
        # 测试可注入 fake（须实现 handle_response(conn, id, result) 鸭型口）
        self.ui: UIContext = (
            ui if ui is not None else RoutingUIContext(self._connections)
        )
        state.ui_context = self.ui
        self._shutdown_requested = False
        self._shutdown_event: Optional[asyncio.Event] = None
        self._read_pumps: List[asyncio.Task] = []
        self._unsubscribe_session: Optional[Callable[[], None]] = None
        # 事件循环滞后探针（P3 观测）：周期任务测漂移——单循环的三类卡顿
        # （CPU 占住/同步阻塞/队头阻塞）全部表现为"周期任务不准时"，一个
        # 探针把三类卡顿都变成可观测数据（stderr → rpc-stderr.log）
        self._lag_interval = lag_interval
        self._lag_threshold_ms = lag_threshold_ms
        self._lag_probe_task: Optional[asyncio.Task] = None
        self._setup_state_hook()

    async def _loop_lag_probe(self) -> None:
        """周期测滞后：漂移超阈值打 stderr（cli 已重定向到 rpc-stderr.log）。"""
        expected = time.monotonic() + self._lag_interval
        while True:
            await asyncio.sleep(self._lag_interval)
            now = time.monotonic()
            lag_ms = (now - expected) * 1000.0
            expected = now + self._lag_interval
            if lag_ms > self._lag_threshold_ms:
                print(
                    f"[nova-rpc] event-loop lag {lag_ms:.0f}ms",
                    file=sys.stderr,
                    flush=True,
                )

    # ------------------------------------------------------------------
    # 连接生命周期
    # ------------------------------------------------------------------

    async def add_connection(
        self,
        transport: Transport,
        *,
        origin: ConnectionOrigin,
        queue_size: int = 1024,
        max_inflight: int = 256,
        exit_on_close: bool = False,
    ) -> Connection:
        """打开 transport 并接入为一条连接（读泵/写泵启动）。"""
        await transport.open()
        conn = Connection(
            transport,
            origin,
            queue_size=queue_size,
            max_inflight=max_inflight,
            exit_on_close=exit_on_close,
            on_closed=self._connection_closed,
        )
        self._connections.add(conn)
        conn.start_writer()
        self._read_pumps.append(asyncio.create_task(self._read_pump(conn)))
        return conn

    async def _read_pump(self, conn: Connection) -> None:
        """连接读泵：顺序读帧、并发分派（长命令不阻塞后续 abort/steer）。

        入站背压：在飞 handler 达 ``conn.max_inflight`` 时——请求立即回
        ``-32004 overloaded``（客户端可稍后重试），通知直接丢弃（它们
        本就是 fire-and-forget）；不再无界派生 task。
        """
        try:
            while not self._shutdown:
                try:
                    raw = await conn.transport.read()
                    if raw is None:
                        break
                except Exception:
                    # 传输层错误通常意味着连接断开
                    break
                if len(conn.tasks) >= conn.max_inflight:
                    if raw.get("id") is not None:
                        await conn.send(
                            to_json_safe(
                                build_error(
                                    raw["id"],
                                    JSONRPCError(
                                        JSONRPCError.OVERLOADED,
                                        "Server overloaded; retry later.",
                                    ),
                                ).to_dict()
                            )
                        )
                    # 通知无 id 无从应答——丢弃
                    continue
                task = asyncio.create_task(self._handle(conn, raw))
                # 强引用登记（防 GC）；id → task 的 cancelRequest 索引
                # 由 _handle 内部建立
                conn.tasks.add(task)
                task.add_done_callback(conn.tasks.discard)
        finally:
            await conn.close()

    def _connection_closed(self, conn: Connection) -> None:
        """连接关闭编排：摘表、取消在飞 handler、UI pending 收尾。"""
        self._connections.remove(conn)
        for task in list(conn.tasks):
            task.cancel()
        conn.request_tasks.clear()
        connection_closed = getattr(self.ui, "connection_closed", None)
        if callable(connection_closed):
            connection_closed(conn)
        if conn.exit_on_close:
            self.shutdown()

    def shutdown(self) -> None:
        """请求服务器停止（信号处理器可直接调用——同步方法）。"""
        self._shutdown_requested = True
        if self._shutdown_event is not None:
            self._shutdown_event.set()
        try:
            loop = asyncio.get_running_loop()
            for conn in self._connections.all():
                loop.create_task(conn.close())
        except RuntimeError:
            pass

    @property
    def _shutdown(self) -> bool:
        return self._shutdown_requested

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------

    async def wait(self) -> None:
        """等待 shutdown（stdio exit_on_close 或信号触发）。"""
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
            # shutdown 先于 wait 到达（竞态）：补置位防永远悬挂
            if self._shutdown_requested:
                self._shutdown_event.set()
        await self._shutdown_event.wait()

    async def run(self) -> None:
        """等到关停并完成清理（读泵、连接、会话订阅、滞后探针）。"""
        self._lag_probe_task = asyncio.create_task(self._loop_lag_probe())
        await self.wait()
        await self.stop()

    async def stop(self) -> None:
        """关停全部连接与读泵（幂等）。"""
        if self._lag_probe_task is not None:
            self._lag_probe_task.cancel()
            self._lag_probe_task = None
        for conn in self._connections.all():
            await conn.close()
        for pump in self._read_pumps:
            pump.cancel()
        if self._read_pumps:
            await asyncio.gather(*self._read_pumps, return_exceptions=True)
        self._read_pumps.clear()
        if self._unsubscribe_session is not None:
            self._unsubscribe_session()
            self._unsubscribe_session = None

    # ------------------------------------------------------------------
    # 会话事件桥（广播 + initialize 门）
    # ------------------------------------------------------------------

    def _setup_state_hook(self) -> None:
        """当 JSON-RPC 方法创建 runtime 时，自动订阅其事件。"""
        original = self._state.on_runtime_created

        def hook(runtime: Any) -> None:
            if original is not None:
                original(runtime)
            self._attach_to_runtime(runtime)

        self._state.on_runtime_created = hook

        # 如果 state 里已经有 runtime（例如测试场景），立即订阅。
        if self._state.runtime is not None:
            self._attach_to_runtime(self._state.runtime)

    def _attach_to_runtime(self, runtime: Any) -> None:
        """订阅当前 runtime 的 session 事件，并在 session 替换时重新订阅。"""
        self._subscribe_session(runtime.session)

        async def rebind(session: Any) -> None:
            self._subscribe_session(session)

        runtime.set_rebind_session(rebind)

    def _subscribe_session(self, session: Any) -> None:
        """订阅单个 AgentSession 的事件。"""
        if self._unsubscribe_session is not None:
            self._unsubscribe_session()

        def listener(event: Any, signal: Any = None) -> None:
            payload = serialize_event(event)
            if payload is not None:
                self.broadcast_event(payload)

        self._unsubscribe_session = session.subscribe(listener)

    def broadcast_event(self, payload: Dict[str, Any]) -> None:
        """把一条 ``agent/event`` 广播给全部已 initialize 的连接。

        帧只序列化一次（to_json_safe 兜底）；各连接经自有队列 FIFO
        写出——慢连接不影响其他连接。

        信封锚点（连接化 P2）：``seq``（服务器生命周期单调）+ ``ts``
        + ``sessionId``——syncSession 的高水位与前端"丢弃 ≤ 水位"
        的增量对账都建立在这个序号上。
        """
        self._state.event_seq += 1
        session_id = None
        runtime = getattr(self._state, "runtime", None)
        if runtime is not None:
            session_id = getattr(getattr(runtime, "session", None), "session_id", None)
        stamped = {
            **payload,
            "seq": self._state.event_seq,
            "ts": int(time.time() * 1000),
            "sessionId": session_id,
        }
        frame = to_json_safe(build_notification("agent/event", stamped).to_dict())
        for conn in self._connections.initialized():
            conn.send_from_sync(frame)
        if self._on_event is not None:
            self._on_event(stamped)

    # ------------------------------------------------------------------
    # 请求处理
    # ------------------------------------------------------------------

    async def _handle(self, conn: Connection, raw: Dict[str, Any]) -> None:
        try:
            msg = parse_message(raw)
        except JSONRPCError as exc:
            if raw.get("id") is not None:
                await conn.send(to_json_safe(build_error(raw["id"], exc).to_dict()))
            return

        # UI 反向通道由 UI 上下文/连接记账处理，不进入方法注册表。
        if self._handle_ui_inbound(conn, msg):
            return

        # cancelRequest 寻址基础：登记 id → 当前 task（按连接隔离；
        # 通知无 id 无从取消）
        request_id = msg.id
        if request_id is not None:
            conn.request_tasks[request_id] = asyncio.current_task()
        # 来源连接进上下文：handler 及其派生任务链（工具执行/UI 弹窗）
        # 全程可取——UI 寻址"发起方优先"与 cancelRequest 隔离的基础
        token = _current_connection.set(conn)
        try:
            response = await self._methods.dispatch(msg)
            # initialize 握手成功即上线（事件广播门/UI 寻址以此为准）
            if (
                msg.method == "initialize"
                and response is not None
                and response.error is None
            ):
                conn.initialized = True
            if response is not None:
                await conn.send(to_json_safe(response.to_dict()))
        except asyncio.CancelledError:
            # 被 cancelRequest 取消：必须写回错误应答，否则前端 Promise 永远悬挂。
            # code 对齐 LSP RequestCancelled（-32800）。写回后 re-raise——
            # 吞掉 CancelledError 会让 task 以"正常完成"收场，破坏取消语义。
            if request_id is not None:
                await conn.send(
                    to_json_safe(
                        build_error(
                            request_id,
                            JSONRPCError(
                                JSONRPCError.REQUEST_CANCELLED, "Request cancelled"
                            ),
                        ).to_dict()
                    )
                )
            raise
        finally:
            _current_connection.reset(token)
            if request_id is not None:
                conn.request_tasks.pop(request_id, None)

    def _handle_ui_inbound(self, conn: Connection, msg: JsonRpcMessage) -> bool:
        """处理前端发来的 UI 相关帧（按连接记账）。

        返回 True 表示已处理，不再进入方法注册表。
        仅保留反向原语应答（ui/response）与能力上报（system/capabilities）。
        """
        method = msg.method
        params = msg.params or {}
        if method == "ui/response":
            handle_response = getattr(self.ui, "handle_response", None)
            if callable(handle_response):
                handle_response(conn, params.get("id"), params.get("result"))
            return True
        if method == "system/capabilities":
            # 能力集按连接记账（多客户端能力不同互不覆盖）
            conn.ui_capabilities = set(params.get("capabilities", []))
            return True
        return False


__all__ = ["RpcServer"]
