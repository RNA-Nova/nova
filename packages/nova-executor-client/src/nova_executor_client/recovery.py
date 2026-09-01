"""断线重连与会话恢复（对位 Rust client_recovery.rs）

- `ReconnectStrategy`：重连调度策略值对象（间隔/退避/上限/最大次数/总时限），
  对位 Rust `ExecServerReconnectStrategy` 的调度侧——Python 侧传输重建由
  调用方注入的 transport_factory 承担（WS 重连 / stdio 重 spawn 同一形态），
  策略只管"何时再试、试到何时"。默认值即 Rust 行为：100ms 固定间隔、
  25s 恢复总时限（服务端 30s 会话保留窗内留余量，对位 SESSION_RECOVERY_TIMEOUT）。
- `ManagedTransport`：包装底层传输的恢复状态机
  （disconnected → connected → recovering → failed），断线后按策略重建
  传输并带 `resumeSessionId` 重握手——服务端进程表/输出缓冲随会话存活，
  恢复成功后调用方无感续用（对位 Rust resume 路径）。

恢复期间的调用语义对位 Rust `RecoveryPolicy::Wait`：send_request 等待恢复
结果而非立即报错；恢复失败（failed）后所有调用以 ConnectionError 收尾
（对位 ExecServerError::Disconnected）。

stdio 语义说明：重 spawn 后服务端是新进程，旧会话必然 unknown session
（-32600），resume 失败转为 failed——该路径保证调用方拿到明确的断线错误
而非干等；WS 长驻服务端才是 resume 的真正受益者。与 Rust 一致：stdio
连接可以不配策略（`strategy=None` 即断线即失败）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from .errors import (
    SESSION_ALREADY_ATTACHED,
    ConnectionError,
    ProtocolError,
    TimeoutError,
)

if TYPE_CHECKING:
    from .protocol import InitializeResponse
    from .transport import Transport

logger = logging.getLogger(__name__)

#: 握手回调：在给定传输上完成 initialize/initialized 握手（含协议版本检查），
#: resume_session_id 非 None 时携带恢复既有会话
HandshakeFn = Callable[["Transport", "str | None"], Awaitable["InitializeResponse"]]
#: 断线回调（同步）：reason 为底层传输给出的断线原因（可为 None）
TransportFactory = Callable[[], "Transport"]


@dataclass(frozen=True)
class ReconnectStrategy:
    """断线重连调度策略（不可变值对象）

    - `interval`：首次重连间隔（秒），默认 0.1——对位 Rust
      SESSION_RECOVERY_RETRY_INTERVAL（100ms）
    - `backoff`：退避倍率，默认 1.0（固定间隔，即 Rust 现状）；>1 时逐次放大
    - `max_interval`：间隔上限（秒）
    - `max_attempts`：最大重连次数；None = 总时限内不限次（Rust 行为）
    - `timeout`：恢复总时限（秒），默认 25——对位 Rust SESSION_RECOVERY_TIMEOUT
      （服务端 30s 会话保留窗内留余量，客户端与服务端的断线计时各自独立起算）
    """

    interval: float = 0.1
    backoff: float = 1.0
    max_interval: float = 5.0
    max_attempts: int | None = None
    timeout: float = 25.0

    def __post_init__(self) -> None:
        if self.interval <= 0:
            raise ValueError(f"interval 须为正数，收到 {self.interval}")
        if self.backoff < 1.0:
            raise ValueError(f"backoff 须 >= 1.0，收到 {self.backoff}")
        if self.max_interval < self.interval:
            raise ValueError(
                f"max_interval（{self.max_interval}）小于 interval（{self.interval}）"
            )
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValueError(f"max_attempts 须 >= 1，收到 {self.max_attempts}")
        if self.timeout <= 0:
            raise ValueError(f"timeout 须为正数，收到 {self.timeout}")

    def delays(self):
        """退避间隔序列：interval 起按 backoff 放大，封顶 max_interval"""
        delay = self.interval
        while True:
            yield delay
            delay = min(delay * self.backoff, self.max_interval)


#: 连接状态机字面量（对位 Rust ConnectionStatus）
State = Literal["disconnected", "connected", "recovering", "failed"]


def _is_retryable_recovery_error(error: Exception) -> bool:
    """恢复错误分类（对位 Rust is_retryable_recovery_error）：

    - 传输/连接/超时类（ConnectionError、SDK/内置 TimeoutError——重连
      wait_for 超时抛的是内置 TimeoutError）：可重试
    - 会话仍附着（-32010，旧连接服务端侧尚未完全关闭）：可重试
    - 其余协议错误（含 -32600 unknown session id——会话不存在或已过
      保留窗）：不可重试，立即转 failed
    """
    if isinstance(error, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return True
    if isinstance(error, ProtocolError):
        return error.code == SESSION_ALREADY_ATTACHED
    return False


class ManagedTransport:
    """带断线恢复的传输包装（实现 Transport 协议，对上层透明）

    - `factory`：传输工场——断线重建经它新造底层传输（WS 重连 / stdio 重
      spawn 同一形态）；None（调用方直传实例）= 无恢复能力，断线即 failed
    - `handshake`：握手回调（initialize + 版本检查 + initialized），首连与
      重连同路径（对位 Rust connect_with_recovery / resume_once 共用
      initialize_rpc）
    - `resume_session_id`：首连显式恢复既有会话（对位 Rust
      ExecServerClientConnectOptions.resume_session_id）；None = 开新会话
    - `on_initialized`：每次握手成功上抛响应（客户端填环境元数据缓存）
    - `on_failed`：恢复失败上抛错误消息（客户端清扫该通道的通知注册表）
    """

    def __init__(
        self,
        factory: TransportFactory | None,
        *,
        handshake: HandshakeFn,
        strategy: ReconnectStrategy | None = None,
        resume_session_id: str | None = None,
        instance: Transport | None = None,
        on_initialized: Callable[[InitializeResponse], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ):
        if factory is None and instance is None:
            raise ValueError("factory / instance 至少提供一个")
        self._factory = factory
        self._handshake = handshake
        self._strategy = strategy
        self._on_initialized = on_initialized
        self._on_failed = on_failed
        #: 首连显式 resume 目标；首连成功后即被服务端下发的 session_id 取代
        self._session_id = resume_session_id
        # 实例直传时直接使用；否则立即经工场造好首条底层传输（连接前可检视
        # 配置——program/url 等，保持旧装配行为）
        self._transport: Transport = instance if instance is not None else factory()
        self._state: State = "disconnected"
        self._failure_message: str | None = None
        self._recover_task: asyncio.Task | None = None
        self._closing = False
        # 状态变化广播：每次变化 set 当前事件并换代（等待者持引用不失效）
        self._state_changed = asyncio.Event()
        self._notification_handlers: list = []
        self._external_disconnect_handlers: list = []
        self._request_handlers: dict = {}
        self._hooks_installed = False

    # ------------------------------------------------------------------
    # 对外属性
    # ------------------------------------------------------------------

    @property
    def current_transport(self) -> Transport:
        """当前底层传输（重连后指向新实例；client.transport 兼容层与诊断用）"""
        return self._transport

    @property
    def session_id(self) -> str | None:
        """当前会话 id（首连握手成功后可用）"""
        return self._session_id

    @property
    def state(self) -> State:
        return self._state

    @property
    def failure_message(self) -> str | None:
        """最近一次连接失败的原因（诊断用；无失败为 None）"""
        return self._failure_message

    @property
    def is_connected(self) -> bool:
        return self._state == "connected" and self._transport.is_connected

    # ------------------------------------------------------------------
    # Transport 协议
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """连接底层传输并完成握手（首连；connected 重复调用幂等）"""
        if self._state == "connected":
            return
        if self._state != "disconnected":
            # recovering/failed 是断线后的内部状态机，不走 connect 重入口
            raise ConnectionError(
                self._failure_message or f"cannot connect while {self._state}"
            )
        self._closing = False
        transport = self._transport
        await transport.connect()
        try:
            response = await self._handshake(transport, self._session_id)
        except Exception:
            # 握手失败即断开底层，避免半连接状态
            await transport.disconnect()
            raise
        self._install(transport, response)
        self._set_state("connected")

    async def disconnect(self) -> None:
        """断开连接（主动关闭——取消进行中的恢复，不触发断线回调）"""
        self._closing = True
        if self._recover_task is not None:
            self._recover_task.cancel()
            try:
                await self._recover_task
            except asyncio.CancelledError:
                pass
            self._recover_task = None
        await self._transport.disconnect()
        self._set_state("disconnected")

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> Any:
        """发送请求（恢复期间等待恢复结果——对位 RecoveryPolicy::Wait）"""
        transport = await self._await_transport()
        return await transport.send_request(method, params)

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> None:
        """发送通知（恢复期间等待恢复结果）"""
        transport = await self._await_transport()
        await transport.send_notification(method, params)

    def on_notification(self, handler) -> None:
        """注册通知处理器（重连后自动重挂到新底层传输）"""
        self._notification_handlers.append(handler)
        if self._hooks_installed:
            self._transport.on_notification(handler)

    def register_request_handler(self, method: str, handler) -> None:
        """注册服务端反向请求处理器（重连后自动重挂到新底层传输）"""
        self._request_handlers[method] = handler
        if self._hooks_installed:
            self._transport.register_request_handler(method, handler)

    def on_disconnect(self, handler) -> None:
        """对 ManagedTransport 自身状态的断线观察口（可选；恢复编排不经这里）"""
        # 底层断线回调由恢复编排消费；对外观察口另维护一份，断线时一并触发
        self._external_disconnect_handlers.append(handler)

    # ------------------------------------------------------------------
    # 状态机与恢复编排
    # ------------------------------------------------------------------

    async def _await_transport(self) -> Transport:
        """取当前可用传输：connected 直取；recovering 等状态变化；failed 报错"""
        while True:
            if self._state == "connected":
                return self._transport
            if self._state == "failed":
                raise ConnectionError(self._failure_message or "connection failed")
            if self._state == "disconnected":
                raise ConnectionError("not connected")
            # recovering：等下一次状态变化（事件换代模式，见 __init__）
            await self._state_changed.wait()

    def _set_state(self, state: State, failure_message: str | None = None) -> None:
        self._state = state
        if failure_message is not None:
            self._failure_message = failure_message
        # 唤醒当前全部等待者，并为下一代等待者换新事件
        self._state_changed.set()
        self._state_changed = asyncio.Event()

    def _install(self, transport: Transport, response: InitializeResponse) -> None:
        """（重）连成功收尾：记录会话 id、挂通知/断线回调、上抛环境元数据"""
        if self._session_id is not None and response.session_id != self._session_id:
            # resume 路径：服务端必须回到同一会话（对位 Rust initialize_rpc 检查）
            raise ProtocolError(
                f"exec-server initialized an unexpected session "
                f"{response.session_id}（期望恢复 {self._session_id}）"
            )
        self._session_id = response.session_id
        self._transport = transport
        self._install_hooks(transport)
        if self._on_initialized is not None:
            self._on_initialized(response)

    def _install_hooks(self, transport: Transport) -> None:
        """把通知处理器、反向请求处理器与断线回调挂到（新）底层传输"""
        for handler in self._notification_handlers:
            transport.on_notification(handler)
        register = getattr(transport, "register_request_handler", None)
        if register is not None:
            for method, handler in self._request_handlers.items():
                register(method, handler)
        on_disconnect = getattr(transport, "on_disconnect", None)
        if on_disconnect is not None:
            on_disconnect(
                lambda reason, t=transport: self._on_transport_disconnect(t, reason)
            )
        self._hooks_installed = True

    def _on_transport_disconnect(
        self, transport: Transport, reason: str | None
    ) -> None:
        """底层意外断线入口（对位 Rust request_recovery 的 ptr_eq 守卫：
        仅当断的是当前传输且仍处于 connected 才发起恢复，避免陈旧回调竞态）"""
        for handler in self._external_disconnect_handlers:
            try:
                handler(reason)
            except Exception:
                logger.warning("disconnect handler raised", exc_info=True)
        if (
            transport is not self._transport
            or self._state != "connected"
            or self._closing
        ):
            return
        message = (
            f"exec-server transport disconnected: {reason}"
            if reason
            else "exec-server transport disconnected"
        )
        self._set_state("recovering")
        self._recover_task = asyncio.create_task(self._recover(message))

    async def _recover(self, disconnect_message: str) -> None:
        """按策略重建传输 + resume 握手（对位 Rust Inner.recover）"""
        strategy = self._strategy
        if strategy is None or self._session_id is None:
            # 无恢复手段（实例直传 / 未配置策略 / 无会话可恢复）——断线即失败
            self._fail(disconnect_message)
            return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + strategy.timeout
        delays = strategy.delays()
        attempts = 0
        last_error = f"recovery timed out after {strategy.timeout}s"

        while True:
            now = loop.time()
            if now >= deadline:
                break
            if strategy.max_attempts is not None and attempts >= strategy.max_attempts:
                last_error = f"reconnect attempts exhausted ({attempts})"
                break
            attempts += 1
            candidate: Transport | None = None
            try:
                candidate = self._factory()
                await asyncio.wait_for(candidate.connect(), deadline - now)
                response = await asyncio.wait_for(
                    self._handshake(candidate, self._session_id), deadline - loop.time()
                )
                self._install(candidate, response)
            except asyncio.CancelledError:
                # 主动 disconnect 取消恢复：半成品候选连接清理后传播取消
                if candidate is not None:
                    try:
                        await candidate.disconnect()
                    except Exception:
                        pass
                raise
            except Exception as e:
                if candidate is not None:
                    try:
                        await candidate.disconnect()
                    except Exception:
                        pass
                if not _is_retryable_recovery_error(e):
                    last_error = str(e)
                    break
                last_error = str(e)
                logger.debug("exec-server resume attempt %d failed: %s", attempts, e)
            else:
                logger.info(
                    "exec-server session %s resumed after %d attempt(s)",
                    self._session_id,
                    attempts,
                )
                self._set_state("connected")
                return

            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(next(delays), remaining))

        self._fail(
            f"{disconnect_message}; failed to resume exec-server session: {last_error}"
        )

    def _fail(self, message: str) -> None:
        """恢复失败终态：在途调用已被旧传输断线收尾，后续调用与等待者统一
        ConnectionError（对位 Rust Inner.fail + fail_all_in_flight_work）"""
        logger.warning("%s", message)
        self._set_state("failed", failure_message=message)
        if self._on_failed is not None:
            try:
                self._on_failed(message)
            except Exception:
                logger.warning("on_failed handler raised", exc_info=True)
