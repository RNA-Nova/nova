"""JSON-RPC 收发公共机械：`_JsonRpcTransport` 基类与 `Transport` 接口协议

- `Transport`：两种传输与连接池（pool.TransportPool）、恢复包装
  （recovery.ManagedTransport）共同遵守的接口协议，上层只面向它编程
- `_JsonRpcTransport`：pending 请求表、响应/通知分发、服务端反向请求的
  准入与并发裁决、断线回调（`on_disconnect`——恢复层据此发起重连，对位
  Rust 的 RpcClientEvent::Disconnected 驱动 request_recovery）
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Protocol

from nova_protocol import (
    NETWORK_POLICY_REQUEST,
    AbortedError,
    AbortSignal,
    NetworkPolicyDecision,
    NetworkPolicyRequestResponse,
)
from nova_protocol.signal import await_with_signal

from ..errors import (
    JSON_RPC_INTERNAL_ERROR,
    JSON_RPC_METHOD_NOT_FOUND,
    ConnectionError,
    ExecutorError,
    ProtocolError,
    RequestHandlerError,
    TimeoutError,
)
from .gate import NETWORK_POLICY_DENIAL_REASON

logger = logging.getLogger(__name__)

#: 通知处理器 / 断线回调 / stderr 回调的签名
NotificationHandler = Callable[[dict], Awaitable[None]]
DisconnectHandler = Callable[[str | None], None]
#: 服务端反向请求处理器：收 params，回 result（异常由传输层转内部错误回执）
RequestHandler = Callable[[dict], Awaitable[Any]]


def _signal_kwarg(signal: AbortSignal | None) -> dict[str, Any]:
    """signal 仅在非 None 时透传（对位 pool.send_request 的 for_cleanup 纪律：
    声明时透传——未声明该参数的传输实现保持兼容）"""
    return {} if signal is None else {"signal": signal}


class Transport(Protocol):
    """传输层接口协议（结构化类型——实现类无需显式继承）。

    `channel` 参数只在连接池（TransportPool）上有路由意义；单传输实现
    接收但忽略它，保持接口统一。

    `for_cleanup` 标记收摊调用（process/terminate、fs/close 等收摊路径）：
    在飞槽位裁决时先抢共享槽、再抢收摊保留槽（对位 Rust rpc.rs
    call_for_cleanup 的保留槽语义）。

    `signal`：取消词汇（nova_protocol AbortSignal）——响应等待经竞速原语
    `await_with_signal` 包装：signal 触发即取消等待、抛 AbortedError（预
    触发快失败不发请求；竞速完成优先，对位 concurrency.md §六）。

    `on_disconnect` 注册意外断线回调（主动 disconnect 不触发）——恢复层
    （recovery.ManagedTransport）据此发起重连；不参与恢复的调用方可忽略。
    """

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
        for_cleanup: bool = False,
        signal: AbortSignal | None = None,
    ) -> Any: ...

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> None: ...

    def on_notification(self, handler: NotificationHandler) -> None: ...

    def register_request_handler(self, method: str, handler: RequestHandler) -> None:
        """注册服务端反向请求处理器（可选能力；恢复层经 getattr 鸭式探测）"""
        ...

    def on_disconnect(self, handler: DisconnectHandler) -> None: ...

    @property
    def is_connected(self) -> bool: ...


class _JsonRpcTransport:
    """JSON-RPC 收发公共机械：pending 表、消息分发、反向请求准入、断线传播。

    子类实现 `_send_message`（写线）并启动各自的接收循环；接收循环逐条过
    `_gate_inbound_message` 闸门后分发——反向请求走 `_dispatch_server_request`
    （准入 + 并发执行）、通知走 `_run_notification_handlers`、响应走
    `_resolve_response`；意外结束时调 `_mark_disconnected`。
    """

    #: 服务端反向请求的在飞槽位上限（对位 Rust client.rs 的
    #: rpc_inbound_request_slots = Semaphore::new(MAX_IN_FLIGHT_SERVER_CALLS)）：
    #: 满员即时 fail-closed 拒决，裁决排队不得堵死接收循环
    MAX_INBOUND_SERVER_CALLS = 256

    #: 常规 outbound 调用的在飞共享槽上限（对位 Rust rpc.rs
    #: MAX_IN_FLIGHT_REGULAR_CALLS）：满员立即报错，不排队
    MAX_IN_FLIGHT_REGULAR_CALLS = 1024
    #: 收摊调用（for_cleanup）的保留槽数（对位 Rust rpc.rs
    #: RESERVED_CLEANUP_CALLS）：共享槽满员时收摊调用仍有最后一格——
    #: 收摊是连接退役前的善后，不得被常规流量饿死
    RESERVED_CLEANUP_CALLS = 1

    def __init__(self, request_timeout: float):
        self.request_timeout = request_timeout
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        #: 在飞 outbound 调用槽位计数（对位 Rust 双信号量的不可用格数——
        #: py 不需要等待语义，整数计数即 try_acquire；响应落定/取消/异常
        #: 即归还，对位 SemaphorePermit drop）
        self._in_flight_regular = 0
        self._in_flight_cleanup = 0
        self._notification_handlers: list[NotificationHandler] = []
        self._disconnect_handlers: list[DisconnectHandler] = []
        self._request_handlers: dict[str, RequestHandler] = {}
        #: 在飞反向请求的 id 集（准入去重；任务收尾即释放——对位 Rust
        #: RpcInboundRequestGuard 的 drop）
        self._inbound_request_ids: set[int] = set()
        #: 在飞反向请求任务（断线/关闭时统一取消——对位 Rust 的
        #: connection_cancelled drop guard：连接退役后裁决不再回包）。
        #: Rust 的槽位信号量跨重连共享（旧连接的在飞请求与新连接并存收尾）；
        #: py 侧断线即取消在飞裁决，重叠窗口不存在，槽位故随连接实例
        self._inbound_tasks: set[asyncio.Task] = set()
        self._closed = True  # 未连接即关闭态

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
        for_cleanup: bool = False,
        signal: AbortSignal | None = None,
    ) -> Any:
        """发送 JSON-RPC 请求并等待响应（单传输忽略 channel——连接池路由参数）

        在飞槽位裁决（对位 Rust rpc.rs 的双信号量）：常规调用占
        MAX_IN_FLIGHT_REGULAR_CALLS 共享槽，满员立即报错（对位
        PendingRequestLimitExceeded）；收摊调用（for_cleanup=True——
        process/terminate、fs/close 等收摊路径）先抢共享槽、再抢
        RESERVED_CLEANUP_CALLS 保留槽，双无即判连接死亡（对位
        call_for_cleanup 的 close_transport 熔断 + RpcCallError::Closed）。

        `signal`：取消词汇——预触发快失败（举旗点：不占槽、不发请求）；
        响应等待经 `await_with_signal` 竞速，signal 胜出即取消等待抛
        AbortedError（pending 表清理与在飞槽归还由 finally 覆盖）。
        """
        if signal is not None:
            # 举旗点：预触发信号快失败——不发请求（对位 concurrency.md §八
            # abort 语义"pre-aborted 不发请求"）
            signal.throw_if_aborted()
        cleanup_reserved = False
        if self._in_flight_regular < self.MAX_IN_FLIGHT_REGULAR_CALLS:
            self._in_flight_regular += 1
        elif for_cleanup and self._in_flight_cleanup < self.RESERVED_CLEANUP_CALLS:
            self._in_flight_cleanup += 1
            cleanup_reserved = True
        elif for_cleanup:
            # 收摊双无：判连接死亡（对位 Rust close_transport 熔断）——
            # 保留槽也耗尽说明连接已堆积上千在飞调用，收摊都排不进
            self._mark_disconnected(
                "exec-server has reached its limit of pending requests "
                "(including the reserved cleanup slot)"
            )
            raise ConnectionError("connection closed")
        else:
            raise ProtocolError(
                "exec-server has reached its limit of "
                f"{self.MAX_IN_FLIGHT_REGULAR_CALLS} pending requests"
            )

        self._request_id += 1
        request_id = self._request_id

        message = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        try:
            await self._send_message(message)
            return await await_with_signal(
                asyncio.wait_for(future, timeout=self.request_timeout), signal
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"request {method} timed out") from None
        except AbortedError:
            # 取消词汇直通（对位 data-modeling.md §七领域层纪律：except 先直通
            # 取消词汇，再进泛型兜底——包装成 ConnectionError 就是把用户取消
            # 误报成故障）
            raise
        except Exception as e:
            # 业务错误（JSON-RPC error 响应的 ProtocolError、连接已断）原样透传，
            # 只把传输层发送失败包装为 ConnectionError
            if isinstance(e, ExecutorError):
                raise
            raise ConnectionError(f"failed to send request {method}: {e}") from e
        finally:
            # 对位 Rust PendingRequestGuard：完成/失败/被取消（CancelledError
            # 基础设施收尾 / AbortedError 用户取消词汇）一律即从 pending 表
            # 移除——取消残留会等到后续流量或断线才清；
            # 在飞槽位一并归还（对位 SemaphorePermit drop）
            if cleanup_reserved:
                self._in_flight_cleanup -= 1
            else:
                self._in_flight_regular -= 1
            self._pending.pop(request_id, None)

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> None:
        """发送 JSON-RPC 通知（无响应；单传输忽略 channel——连接池路由参数）"""
        await self._send_message(
            {
                "method": method,
                "params": params or {},
            }
        )

    @abstractmethod
    async def _send_message(self, message: dict) -> None:
        """把一条 JSON-RPC 消息写到线上（子类实现各自的线格式）"""

    # ------------------------------------------------------------------
    # 接收分发
    # ------------------------------------------------------------------

    def _resolve_response(self, message: dict) -> None:
        """响应归 pending future：error 响应映射为带结构化 code 的 ProtocolError"""
        request_id = message["id"]
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return
        if "result" in message:
            # result 键优先（对位 serde 反序列化的 contains_key("result") 优先：
            # result+error 并存时 error 键被忽略——与 _is_malformed_message
            # 的归类一致，并存消息按 Response 归）
            future.set_result(message["result"])
        elif "error" in message:
            error = message["error"]
            code = error.get("code")
            future.set_exception(
                ProtocolError(
                    f"JSON-RPC error {code}: {error.get('message')}",
                    code=code,
                    # 线上裸消息原样携带（恢复层全等甄别用——对位 Rust
                    # is_transport_closed_error 比较的是 wire message）
                    server_message=error.get("message"),
                )
            )

    async def _run_notification_handlers(self, message: dict) -> None:
        """通知按序分发给全部处理器（异常逐条 logger.warning 留痕——
        处理器错误不许炸掉接收循环，也不静默吞）"""
        for handler in self._notification_handlers:
            try:
                await handler(message)
            except Exception:
                logger.warning(
                    "notification handler raised on %s",
                    message.get("method"),
                    exc_info=True,
                )

    def on_notification(self, handler: NotificationHandler) -> None:
        """注册通知处理器"""
        self._notification_handlers.append(handler)

    # ------------------------------------------------------------------
    # 服务端反向请求（客户端当服务端：网络沙箱裁决等 method+id 消息）
    # ------------------------------------------------------------------

    def register_request_handler(self, method: str, handler: RequestHandler) -> None:
        """注册服务端发起请求的处理器（按方法名路由；重复注册覆盖旧者）"""
        self._request_handlers[method] = handler

    def unregister_request_handler(self, method: str) -> None:
        """注销服务端请求处理器（幂等）"""
        self._request_handlers.pop(method, None)

    def _dispatch_server_request(self, message: dict) -> None:
        """反向请求的准入与并发分发（对位 Rust client_recovery.rs
        spawn_rpc_reader + rpc.rs admit_inbound_request）。

        接收循环内同步调用、绝不 inline 等用户回调——回调期间后续消息照常
        流动（对位 Rust 的 tokio::spawn 并发执行）。准入纪律：

        - 无处理器：回 METHOD_NOT_FOUND（服务端 fail-closed 拒决），不占槽
          （对位 Rust 分发分支的早退——方法路由先于准入校验）
        - id 须为非负整数（对位 Rust RequestId::Integer >= 0）；字符串/负数/
          布尔 id 即断连+恢复（对位 InvalidRequestId → close_transport +
          request_recovery）
        - id 在飞去重：重复即断连+恢复（对位 DuplicateRequestId）
        - 在飞满槽：即时回 fail-closed 拒决（对位 AtCapacity），不排队
        """
        method = message.get("method") or ""
        message_id = message["id"]
        handler = self._request_handlers.get(method)
        if handler is None:
            self._spawn_inbound_task(
                self._reply_to_server_request(
                    message_id,
                    error={
                        "code": JSON_RPC_METHOD_NOT_FOUND,
                        "message": f"no handler for {method}",
                    },
                )
            )
            return
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id < 0
        ):
            self._mark_disconnected("exec-server sent an invalid request ID")
            return
        if message_id in self._inbound_request_ids:
            self._mark_disconnected("exec-server reused an in-flight request ID")
            return
        if len(self._inbound_request_ids) >= self.MAX_INBOUND_SERVER_CALLS:
            self._spawn_inbound_task(self._reply_at_capacity(message_id, method))
            return
        self._inbound_request_ids.add(message_id)
        self._spawn_inbound_task(self._run_server_request(message, handler))

    def _spawn_inbound_task(self, coro: Coroutine[Any, Any, None]) -> None:
        """反向请求相关协程统一 spawn 并跟踪（断线/关闭时取消）"""
        task = asyncio.create_task(coro)
        self._inbound_tasks.add(task)
        task.add_done_callback(self._inbound_tasks.discard)

    async def _run_server_request(self, message: dict, handler: RequestHandler) -> None:
        """裁决执行任务（对位 Rust tokio::spawn 的决策 future）。

        裁决类请求绝不让服务端干等超时：处理器异常回内部错误（服务端
        fail-closed 拒决）。完成/异常/取消一律释放在飞 id（对位
        RpcInboundRequestGuard drop）；取消（断线收尾）不回包（对位
        connection_cancelled 分支提前返回）。
        """
        message_id = message["id"]
        method = message.get("method") or ""
        try:
            try:
                result = await handler(message.get("params") or {})
            except asyncio.CancelledError:
                raise
            except RequestHandlerError as e:
                # 处理器声明的线上错误（对位 Rust respond_error——如裁决参数
                # 解析失败回 -32602）：按码表原样回包，不兜成内部错误
                await self._reply_to_server_request(
                    message_id, error={"code": e.code, "message": str(e)}
                )
            except Exception as e:
                logger.warning(
                    "server request handler %s failed: %s", method, e, exc_info=True
                )
                await self._reply_to_server_request(
                    message_id,
                    error={
                        "code": JSON_RPC_INTERNAL_ERROR,
                        "message": f"handler failed: {e}",
                    },
                )
            else:
                await self._reply_to_server_request(message_id, result=result)
        finally:
            self._inbound_request_ids.discard(message_id)

    async def _reply_at_capacity(self, message_id: int, method: str) -> None:
        """在飞满槽的即时拒决（对位 Rust AtCapacity 分支）：裁决请求回
        `{deny, not_allowed}`（服务端 fail-closed 拒决）；其他方法回内部
        错误——任何路径都不让服务端干等"""
        if method == NETWORK_POLICY_REQUEST:
            result = NetworkPolicyRequestResponse(
                decision=NetworkPolicyDecision.deny(NETWORK_POLICY_DENIAL_REASON)
            ).model_dump(by_alias=True, exclude_none=True)
            await self._reply_to_server_request(message_id, result=result)
        else:
            await self._reply_to_server_request(
                message_id,
                error={
                    "code": JSON_RPC_INTERNAL_ERROR,
                    "message": (
                        "too many in-flight server requests "
                        f"(limit {self.MAX_INBOUND_SERVER_CALLS})"
                    ),
                },
            )

    async def _reply_to_server_request(
        self,
        message_id: Any,
        *,
        result: Any = None,
        error: dict | None = None,
    ) -> None:
        """回写反向请求响应（写线失败即判连接死亡——对位 Rust respond /
        respond_error 失败后 request_recovery，client_recovery.rs:584-590
        等处：连接已死才让写失败；经断线收尾触发恢复层重建，而非仅留痕
        让服务端干等响应）"""
        reply: dict = {"id": message_id}
        if error is not None:
            reply["error"] = error
        else:
            reply["result"] = result if result is not None else {}
        try:
            await self._send_message(reply)
        except Exception as e:
            self._mark_disconnected(
                f"failed to reply to exec-server request id={message_id}: {e}"
            )

    def _cancel_inbound_tasks(self) -> None:
        """取消在飞反向请求任务（断线/关闭收尾；幂等）"""
        current = asyncio.current_task()
        for task in self._inbound_tasks:
            if task is not current:
                task.cancel()

    async def _drain_inbound_tasks(self) -> None:
        """等已取消的反向请求任务收尾（disconnect 用——事件循环关闭前不留悬挂）"""
        current = asyncio.current_task()
        tasks = [task for task in self._inbound_tasks if task is not current]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # 断线
    # ------------------------------------------------------------------

    def on_disconnect(self, handler: DisconnectHandler) -> None:
        """注册意外断线回调（同步回调；主动 disconnect 不触发）"""
        self._disconnect_handlers.append(handler)

    def _mark_disconnected(self, reason: str | None) -> None:
        """意外断线的统一收尾：pending 以 ConnectionError 收尾、取消在飞反向
        请求、关闭底层承载（`_abort_connection`——对位 Rust 连接退役即
        transport.terminate()）、触发 on_disconnect 回调
        （主动 disconnect 不走这里；多路径并发只生效一次）"""
        if self._closed:
            return
        self._closed = True
        self._fail_pending()
        self._cancel_inbound_tasks()
        self._abort_connection()
        for handler in self._disconnect_handlers:
            try:
                handler(reason)
            except Exception:
                logger.warning("disconnect handler raised", exc_info=True)

    def _abort_connection(self) -> None:
        """意外断线时关闭底层承载（对位 Rust close_transport 的 task abort /
        stdio 的 transport.terminate()）。基类无承载可关；子类覆盖：
        WebSocket 关 socket、stdio 收割进程树——否则恢复路径的旧连接泄漏"""

    def _fail_pending(self) -> None:
        """连接断开收尾：取消所有 pending 请求（幂等）"""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("connection closed"))
        self._pending.clear()
