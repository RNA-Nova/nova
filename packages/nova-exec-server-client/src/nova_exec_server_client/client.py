"""ExecutorClient 统一入口（对位 Rust client.rs 的装配层）"""

from __future__ import annotations

import asyncio
import logging
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from nova_protocol import (
    ENVIRONMENT_CONFIG_READ,
    ENVIRONMENT_INFO,
    ENVIRONMENT_STATUS,
    HTTP_REQUEST,
    HTTP_REQUEST_BODY_DELTA,
    INITIALIZE,
    INITIALIZED,
    NETWORK_POLICY_DECISION,
    NETWORK_POLICY_REQUEST,
    PROCESS_START,
    PROCESS_TERMINATE,
    PROTOCOL_VERSION,
    AbortedError,
    AbortSignal,
    ByteChunk,
    EnvironmentConfigReadParams,
    EnvironmentConfigReadResponse,
    EnvironmentInfo,
    EnvironmentStatus,
    HttpHeader,
    HttpRedirectPolicy,
    HttpRequestBodyDeltaNotification,
    HttpRequestParams,
    HttpRequestResponse,
    InitializeParams,
    InitializeResponse,
    NetworkPolicyDecision,
    NetworkPolicyDecisionNotification,
    NetworkPolicyRequestParams,
    NetworkPolicyRequestResponse,
    ProcessTerminateParams,
    ResolvedEnvironment,
)
from nova_protocol.signal import await_with_signal
from pydantic import ValidationError

from .errors import (
    JSON_RPC_INVALID_PARAMS,
    ConnectionError,
    ExecutorError,
    ProtocolError,
    RequestHandlerError,
    TimeoutError,
    TransportError,
)
from .fs import FileSystemManager
from .notifications import NotificationRouter
from .pool import CHANNEL_CONTROL, CHANNEL_DATA, TransportPool
from .process import ProcessManager
from .pty import PtyManager
from .recovery import ManagedTransport, ReconnectStrategy, is_transport_class_error
from .transport import StdioTransport, Transport, WebSocketTransport, _signal_kwarg

logger = logging.getLogger(__name__)

#: 默认客户端名（initialize 握手携带）
DEFAULT_CLIENT_NAME = "nova-exec-server-client"

#: initialize 握手时限（秒，对位 Rust INITIALIZE_TIMEOUT /
#: DEFAULT_REMOTE_EXEC_SERVER_INITIALIZE_TIMEOUT）：常规请求无隐含超时
#: （对位 rpc.rs RpcCallTimeout::None），唯握手带专用 deadline——卡死传输
#: 上的首连/重连握手不能无限干等（恢复编排自身的总时限另算）
INITIALIZE_TIMEOUT = 10.0

#: environment/info 探活时限（秒，对位 Rust ENVIRONMENT_INFO_TIMEOUT）：
#: 覆盖发送与等待全程——卡死传输可能把请求堵在出站队列，时限必须覆盖发送
ENVIRONMENT_INFO_TIMEOUT = 30.0

#: environment/status 探活时限（秒，对位 Rust ENVIRONMENT_STATUS_TIMEOUT）：
#: 健康检查 fail-fast——只复用既有连接、绝不等待/触发恢复，配专用短时限
ENVIRONMENT_STATUS_TIMEOUT = 10.0

#: 裁决应答的兜底拒决理由（对位 Rust NETWORK_POLICY_DENIAL_REASON）：
#: 入站请求违规 / 回调超时 / 回调 reason 越限时以此 fail-closed
NETWORK_POLICY_DENIAL_REASON = "not_allowed"
#: 裁决请求 processId 上限（UTF-8 字节，对位 MAX_NETWORK_POLICY_PROCESS_ID_BYTES）
MAX_NETWORK_POLICY_PROCESS_ID_BYTES = 256
#: 裁决请求 host 上限（UTF-8 字节，对位 MAX_NETWORK_POLICY_HOST_BYTES）
MAX_NETWORK_POLICY_HOST_BYTES = 253
#: 裁决 reason 上限（UTF-8 字节，对位 MAX_NETWORK_POLICY_REASON_BYTES）
MAX_NETWORK_POLICY_REASON_BYTES = 1024


def _is_control_char(ch: str) -> bool:
    """Unicode 控制符（Cc 类，对位 Rust char::is_control）"""
    return unicodedata.category(ch) == "Cc"


def _policy_process_id_valid(process_id: str) -> bool:
    """入站裁决请求 processId 校验（对位 client_recovery.rs 的 process_id_valid）：
    非空且 UTF-8 字节长不超上限"""
    return (
        len(process_id) > 0
        and len(process_id.encode("utf-8")) <= MAX_NETWORK_POLICY_PROCESS_ID_BYTES
    )


def _policy_host_valid(host: str) -> bool:
    """入站裁决请求 host 校验（对位 client_recovery.rs 的 host_valid）：
    非空、UTF-8 字节长不超上限、无控制符、无空白。
    （str.isspace 比 Rust char::is_whitespace 多收 U+001C–U+001F，
    但那些本属 Cc 控制符已被上一条件拦截——两实现判定结果一致）"""
    return (
        len(host) > 0
        and len(host.encode("utf-8")) <= MAX_NETWORK_POLICY_HOST_BYTES
        and not any(_is_control_char(ch) or ch.isspace() for ch in host)
    )


def _file_url(path: str) -> str:
    """本地绝对路径 → file:// URL（服务端 PathUri 线形）。

    纪律参照 nova_protocol exec_server_wire._file_url：纯字符串构造，
    仅对 URL path 保留集之外的字符做百分号编码；已是 file:// 前缀的
    原样透传。
    """
    if path.startswith("file://"):
        return path
    if not path.startswith("/"):
        raise ValueError(f"路径须为绝对路径：{path}")
    safe = "/:@-._~!$&'()*+,;="
    encoded = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in safe)) else f"%{b:02X}"
        for ch in path
        for b in ch.encode("utf-8")
    )
    return "file://" + encoded


def _clamp_policy_decision(decision: NetworkPolicyDecision) -> NetworkPolicyDecision:
    """裁决 reason 回夹（对位 client_recovery.rs 的 decision 收口匹配）：

    - allow 剥离 reason（Rust Allow 变体无 reason 字段，wire 不带——
      回调误带不回线上）
    - deny/ask 的 reason 缺省、超长或含控制符 → 整体回夹为
      deny("not_allowed")（fail-closed，不把违规文本送回服务端）
    """
    if decision.type == "allow":
        return NetworkPolicyDecision.allow()
    reason = decision.reason
    if (
        reason is None
        or len(reason.encode("utf-8")) > MAX_NETWORK_POLICY_REASON_BYTES
        or any(_is_control_char(ch) for ch in reason)
    ):
        return NetworkPolicyDecision.deny(NETWORK_POLICY_DENIAL_REASON)
    return decision


class _ProcessStartGate:
    """process/start 在飞准入与跟踪（对位 Rust Inner.begin_process_start /
    finish_process_start / wait_for_process_starts + cleanup_process_start）

    - 准入只在控制面 connected 且钉住当前底层传输时成功（对位 ptr_eq 守卫）
      ——恢复期间准入失败，调用方重取对映代连接，start 永不落在半死连接上
    - 恢复编排在重建传输前应等计数归零（`wait_idle`，接线归 recovery.py
      侧恢复钩子——见 ExecutorClient._wait_for_process_starts 的协调说明）
    - 传输类失败/被取消的 start 调度后台 terminate 清理：请求是否落地不可知，
      进程可能已在服务端运行而客户端拿不到句柄；恢复成功后 terminate 落到
      同一会话把孤儿收回
    """

    def __init__(self, control: ManagedTransport, pool: TransportPool):
        self._control = control
        self._pool = pool
        self._active = 0
        # 归零事件（计数 0 时置位；恢复编排等待它——事件常置不换代）
        self._idle = asyncio.Event()
        self._idle.set()
        # 后台任务跟踪集（terminate 清理 / start 线 RPC——持引用防 GC，
        # 对位 transport/base.py _spawn_inbound_task 的跟踪集模式）
        self._cleanup_tasks: set[asyncio.Task] = set()

    def _track_task(self, task: asyncio.Task) -> None:
        """后台任务入跟踪集持引用（防 GC 提前回收悬挂任务）"""
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    def try_begin(self, transport: Transport) -> bool:
        """原子准入（同步无 await，对位 begin_process_start 的锁内检查+计数）"""
        if (
            self._control.state != "connected"
            or self._control.current_transport is not transport
            # 对位 begin_process_start 的 expected.is_disconnected() 检查：
            # 底层传输已死而断线回调未至的窗口内不予准入
            or not transport.is_connected
        ):
            return False
        self._active += 1
        self._idle.clear()
        return True

    def finish(self) -> None:
        """计数归还（对位 finish_process_start / ActiveProcessStart drop 守卫）"""
        self._active -= 1
        if self._active == 0:
            self._idle.set()

    async def wait_idle(self) -> None:
        """等在飞 start 归零（对位 wait_for_process_starts；总时限归调用方）"""
        await self._idle.wait()

    def schedule_cleanup(self, process_id: str | None) -> None:
        """调度后台 terminate 清理（processId 缺失即请求未成形，无需清理）"""
        if process_id:
            self._track_task(asyncio.create_task(self._cleanup(process_id)))

    async def _cleanup(self, process_id: str) -> None:
        """对位 Rust cleanup_process_start 的 loop：terminate 经连接池走
        Wait 策略（恢复期间挂起，恢复成功后落到同一会话）；传输类失败
        （连接/超时/服务端 -32000 传输关闭——判别同 recovery
        is_transport_class_error）且连接未到终态时重试，业务结果
        （进程不存在/终止异步未生效）收尾"""
        while True:
            try:
                await self._pool.send_request(
                    PROCESS_TERMINATE,
                    ProcessTerminateParams(processId=process_id).model_dump(
                        by_alias=True
                    ),
                    # 孤儿收摊调用——对位 Rust call_for_cleanup：挤占共享槽失败
                    # 时还有收摊保留槽可用（常规调用挤兑不致收不了摊）
                    for_cleanup=True,
                )
                return
            except Exception as e:
                if not is_transport_class_error(e):
                    if not isinstance(e, ExecutorError):
                        logger.exception(
                            "process start cleanup failed for %s", process_id
                        )
                    return
                # failed/disconnected 终态放弃；其余（恢复竞态）重试——
                # 重试的 send_request 在恢复期间挂起，不会忙转
                if self._control.state in ("failed", "disconnected"):
                    return


class _ProcessStartGatedTransport:
    """ProcessManager 专用发送包装：process/start 经在飞门控 + 失败清理
    （对位 Rust start_process 的 begin/finish/cleanup 编排），其余方法原样
    透传连接池。process/start 恒走控制面（池路由缺省），门控钉住控制面
    当前底层传输直接发送——恢复期间不把 start 静默改投到对映代连接上。

    取消词汇：调用方取消语义从裸 CancelledError 扩展到 AbortedError
    （signal 触发经 `await_with_signal` 竞速）——两词汇同语义：signal
    触发只丢回执（AbortedError 上抛调用方），后台线 RPC 不受影响
    （二轮语义：RPC 落定后按取消分支调度 terminate 清理）。
    """

    def __init__(self, pool: TransportPool, gate: _ProcessStartGate):
        self._pool = pool
        self._gate = gate

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
        signal: AbortSignal | None = None,
    ) -> Any:
        if method != PROCESS_START:
            return await self._pool.send_request(
                method, params, channel=channel, signal=signal
            )
        gate = self._gate
        control = gate._control
        while True:
            # 对位 Rust start_process 的 loop：Wait 策略取当前连接（恢复期间
            # 等待），取到后原子准入；准入失败说明连接已换代（或底层已死、
            # 断线回调未至——让出事件循环等恢复推进），重取对映代。
            # 恢复等待同样可被 signal 打断（竞速挂 _await_transport；预触发
            # 快失败——门控不发请求）
            transport = await await_with_signal(control._await_transport(), signal)
            if gate.try_begin(transport):
                break
            await asyncio.sleep(0)
        # 对位 Rust start_process 的 result_tx/result_received_rx 结构：
        # 线 RPC 挪进后台任务，调用方等待回执——取消/超时只是丢回执，不打断
        # 线上 RPC；gate.finish 与 cleanup 调度都推迟到 RPC 落定（响应/传输
        # 关闭）。否则取消即调度 terminate 可能抢在 start 处理器落地前到达
        # （服务端进程表尚无此 id，terminate 返回成功），start 随后落地
        # Running 成孤儿
        settled: asyncio.Future = asyncio.get_running_loop().create_future()
        gate._track_task(
            asyncio.create_task(self._run_start_rpc(transport, method, params, settled))
        )
        try:
            # 回执等待经竞速包装：signal 触发即取消等待（settled 随取消关闭）、
            # 抛 AbortedError；signal 为 None 时直通（与裸 await settled 同构）
            return await await_with_signal(settled, signal)
        except (asyncio.CancelledError, AbortedError):
            # 调用方取消/超时（CancelledError 基础设施收尾）与用户取消
            # （AbortedError——signal 触发的取消词汇）的同语义臂；回执丢失两态
            # （对位 Rust 两处分支）：
            # - RPC 仍在飞：settled 随取消关闭，后台任务落定后自行调度清理
            # - RPC 已成功落定但取消仍被投递（future 已完成、取消标记在途）：
            #   进程已在服务端运行而调用方拿不到句柄——此处补调度清理
            if settled.done() and not settled.cancelled():
                try:
                    settled.result()
                except Exception:
                    pass  # 失败落定：清理已在后台任务的失败臂调度
                else:
                    gate.schedule_cleanup((params or {}).get("processId"))
            raise

    async def _run_start_rpc(
        self,
        transport: Transport,
        method: str,
        params: dict[str, Any] | None,
        settled: asyncio.Future,
    ) -> None:
        """后台执行 start 线 RPC（对位 Rust start_process spawn 的任务体）：
        RPC 落定后归还门控计数并交付结果；回执已失（调用方取消/超时/signal
        触发）的落定成功按取消分支调度 terminate 清理；传输类失败无论回执
        在与否都调度清理（请求是否落地不可知，进程可能已在服务端运行）。

        有意不接调用方 signal：取消只丢回执，线上 RPC 跑到落定（二轮语义）。"""
        gate = self._gate
        try:
            try:
                result = await transport.send_request(method, params)
            except Exception as e:
                # RPC 落定失败：传输类（连接/超时/服务端 -32000 传输关闭，
                # 判别同 recovery is_transport_class_error）→ 清理孤儿；
                # 业务错误（参数拒绝等）原样上抛——对位 Rust Err 分支的
                # is_transport_closed_error 分流
                if is_transport_class_error(e):
                    gate.schedule_cleanup((params or {}).get("processId"))
                if not settled.cancelled():
                    settled.set_exception(e)
                return
            if settled.cancelled():
                # 调用方已丢回执（对位 Rust result_tx.send 失败分支）：
                # 进程已在服务端运行——后台 terminate 清理
                gate.schedule_cleanup((params or {}).get("processId"))
            else:
                settled.set_result(result)
        finally:
            # 计数归还推迟到 RPC 落定（对位 ActiveProcessStart 守卫持到
            # spawn 任务内）——恢复编排在飞等待因此覆盖整个线 RPC
            gate.finish()

    def __getattr__(self, name: str) -> Any:
        # 其余接口（send_notification/on_notification/is_connected 等）透传连接池
        return getattr(self._pool, name)


class ExecutorClient:
    """nova-exec-server 客户端

    传输三选一：

    - `url`（+ `token`）：WebSocket 连接（现状默认）
    - `transport`：调用方自建单连接传输（如 `StdioTransport`）——无重建
      手段，断线即失败（不恢复）
    - `transport_factory`：传输工场，断线重连经它新造底层传输

    `request_timeout`：常规请求的本地时限（秒）。默认 `None` = 无隐含超时
    （对位 Rust rpc.rs `RpcCallTimeout::None`——慢盘大文件读写、长
    timeoutMs 的 http/request、高负载 process/start 等慢调用不会被本地
    提前判死）；显式浮点数 = 每请求本地超时（透传到底层传输）。例外：
    initialize 握手恒带专用 INITIALIZE_TIMEOUT（10s）时限，探活类调用
    （environment/info 30s / environment/status 10s）各自带专用时限。

    `connections`：连接数——1 = 全部走单连接（现状行为）；
    2 = 控制面/数据面分离（read_stream/write_stream 等大流量方法走独立
    第二条连接，不阻塞 LLM 工具调用）。更复杂的通道布局可直接构造
    `TransportPool` 传给管理器。

    `reconnect`：断线重连策略（recovery.ReconnectStrategy；对位 Rust
    ExecServerReconnectStrategy）。默认策略 = 原 `auto_reconnect=True`
    语义（断线按策略重连并带 resumeSessionId 恢复会话——进程表/输出流
    跨重连存活）；`None` 关闭恢复（断线即失败，原 `auto_reconnect=False`
    语义）。每条连接各自恢复各自的会话（多连接时服务端会话随连接）。

    `resume_session_id`：首连显式恢复既有会话（对位 Rust
    ExecServerClientConnectOptions.resume_session_id）；None = 开新会话。
    `connections=2` 时只落控制面首连（服务端拒绝同一会话的重复附着），
    数据面开新会话。

    `initialize_timeout`：initialize 握手时限（秒）。None（默认）用包级
    INITIALIZE_TIMEOUT（10s）；环境声明的握手时限经此注入。

    `network_policy`：网络沙箱裁决回调（托管网络进程触碰 "ask" 域名时，
    服务端反向请求 `network/policyRequest`，本回调收
    `NetworkPolicyRequestParams`、回 `NetworkPolicyDecision`）。
    None（默认）不注册处理器——服务端收到 METHOD_NOT_FOUND 并按
    fail-closed 拒绝该访问（安全缺省）。入站请求先经
    processId/host 校验（对位 Rust client_recovery.rs：非空/限长/无控制符
    空白），违规不调回调直接 deny("not_allowed")；processId 不在本客户端
    活进程登记表（ProcessManager 维护，对位 Rust Inner.sessions 会话表）
    同样不调回调直接 deny。回调耗时逐进程取 start 的
    networkProxy.policyDecisionTimeoutMs（对位 Rust controller.timeout），
    未声明回退 `network_policy_timeout`（默认 30s——超时即
    deny("not_allowed")）；回调返回的 deny/ask reason 超限（1024 字节）
    或含控制符时整体回夹为 deny("not_allowed")；回调异常由传输层转内部
    错误，服务端同样 fail-closed。注册挂在控制面连接上，断线重连自动重挂。
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        max_payload: int = 64 * 1024 * 1024,
        request_timeout: float | None = None,
        *,
        transport: Transport | None = None,
        transport_factory: Callable[[], Transport] | None = None,
        connections: int = 1,
        reconnect: ReconnectStrategy | None = ReconnectStrategy(),
        resume_session_id: str | None = None,
        client_name: str = DEFAULT_CLIENT_NAME,
        initialize_timeout: float | None = None,
        network_policy: (
            Callable[[NetworkPolicyRequestParams], Awaitable[NetworkPolicyDecision]]
            | None
        ) = None,
        network_policy_timeout: float = 30.0,
    ):
        if network_policy_timeout <= 0:
            raise ValueError(
                f"network_policy_timeout 须为正数，收到 {network_policy_timeout}"
            )
        if initialize_timeout is not None and initialize_timeout <= 0:
            raise ValueError(f"initialize_timeout 须为正数，收到 {initialize_timeout}")
        if transport is None and transport_factory is None:
            if url is None:
                raise ValueError("url / transport / transport_factory 至少提供一个")

            def transport_factory(  # noqa: F811——WebSocket 传输工场
                url: str = url,
                token: str | None = token,
                max_payload: int = max_payload,
                request_timeout: float | None = request_timeout,
            ) -> Transport:
                return WebSocketTransport(
                    url=url,
                    token=token,
                    max_payload=max_payload,
                    request_timeout=request_timeout,
                )

        if connections not in (1, 2):
            raise ValueError(
                f"connections 只支持 1（单连接）或 2（控制/数据面分离），收到 {connections}"
            )
        if transport is not None and connections != 1:
            raise ValueError(
                "传入 transport 实例时无法克隆，connections 只能为 1"
                "（多连接请传 transport_factory）"
            )

        self._client_name = client_name
        #: initialize 握手时限（秒；None → 包级 INITIALIZE_TIMEOUT——读取
        #: 构造时刻的模块常量，monkeypatch 调短后装配的客户端随之生效）
        self._initialize_timeout = (
            INITIALIZE_TIMEOUT if initialize_timeout is None else initialize_timeout
        )
        # 环境元数据缓存：initialize 捎带即填充；旧服务端未捎带时首个
        # environment_info() 调用回退单次 environment/info 拉取填充
        self._environment_info: EnvironmentInfo | None = None
        # 统一通知分发（对位 Rust Inner 注册表）：全部连接的通知统一经它
        # 按 handle_id 路由；连接恢复失败时按通道清扫挂起流
        self._router = NotificationRouter()

        def make_managed(
            channel: str,
            factory: Callable[[], Transport] | None,
            instance: Transport | None = None,
        ) -> ManagedTransport:
            return ManagedTransport(
                factory,
                instance=instance,
                # 实例直传无重建手段——断线即失败（对位 Rust 无 strategy 的
                # connect() 路径）
                strategy=None if factory is None else reconnect,
                # 显式 resume 只落控制面：服务端 session_registry 拒绝同一
                # 会话的重复附着（-32010），双通道都带同一 resume id 必败；
                # 数据面首连开新会话（服务端句柄状态随连接，跨通道本不共享）
                resume_session_id=(
                    resume_session_id if channel == CHANNEL_CONTROL else None
                ),
                handshake=self._handshake,
                on_initialized=self._on_initialized,
                on_failed=lambda message, ch=channel: self._router.fail_channel(
                    ch, message
                ),
                # 对位 Rust recover() 起手的 wait_for_process_starts：控制面
                # 恢复前等在飞 start 归零（数据面无 start，不传）
                pre_recovery=(
                    self._wait_for_process_starts
                    if channel == CHANNEL_CONTROL
                    else None
                ),
            )

        if connections == 1:
            # transport 直传与 factory 路径同一装配（前者 factory=None → 无恢复）
            channels: dict[str, Transport] = {
                CHANNEL_CONTROL: make_managed(
                    CHANNEL_CONTROL, transport_factory, transport
                )
            }
        else:
            channels = {
                CHANNEL_CONTROL: make_managed(CHANNEL_CONTROL, transport_factory),
                CHANNEL_DATA: make_managed(CHANNEL_DATA, transport_factory),
            }

        self._pool = TransportPool(channels)
        # 通知统一分发：pool fan-in 到全部通道（重连后 ManagedTransport 自动重挂）
        self._pool.on_notification(self._router.dispatch)
        self._control: ManagedTransport = channels[CHANNEL_CONTROL]  # type: ignore[assignment]
        # process/start 在飞门控（对位 Rust begin/finish_process_start +
        # cleanup_process_start）：准入钉住控制面对映代连接；传输类失败/
        # 被取消的 start 调度后台 terminate 清理；恢复编排重建传输前经
        # _wait_for_process_starts 等在飞归零。control 注入供 read/write
        # 传输关闭重试臂判终态（对位 Rust client.inner.is_failed() 检查）
        self._process_start_gate = _ProcessStartGate(self._control, self._pool)
        self.process = ProcessManager(
            _ProcessStartGatedTransport(self._pool, self._process_start_gate),
            control=self._control,
        )
        # 写流能力门（fileWriteStreaming）与环境元数据缓存同源：initialize 捎带
        # 即填充、旧服务端惰性拉取（对位 codex e7798c9944 的 environment_info()）
        self.fs = FileSystemManager(
            self._pool, router=self._router, environment_info=self.environment_info
        )
        self.pty = PtyManager(self.process)

        self._network_policy = network_policy
        self._network_policy_timeout = network_policy_timeout
        if network_policy is not None:
            # 裁决处理器挂控制面（ManagedTransport 重连自动重挂）
            self._control.register_request_handler(
                NETWORK_POLICY_REQUEST, self._answer_network_policy
            )

        #: 环境声明的连接总时限（from_environment 注入；None = 不限制）
        self._connect_timeout: float | None = None

    async def _answer_network_policy(self, params: dict[str, Any]) -> dict[str, Any]:
        """裁决适配（对位 Rust client_recovery.rs 的 network/policyRequest 应答
        编排）：线上载荷解析 → 入站校验 → 会话表查找 → 用户回调（带逐进程
        时限）→ reason 回夹 → 回线上形态。回调异常由传输层转内部错误
        （服务端 fail-closed 拒决）。"""
        assert self._network_policy is not None
        try:
            request = NetworkPolicyRequestParams.model_validate(params)
        except ValidationError as e:
            # 线上载荷解析失败：回 -32602 不调用户回调（对位 Rust
            # client_recovery.rs 的 invalid_params 分支——"invalid network
            # policy request params"）
            raise RequestHandlerError(
                JSON_RPC_INVALID_PARAMS, "invalid network policy request params"
            ) from e
        if not _policy_process_id_valid(request.process_id) or not _policy_host_valid(
            request.request.host
        ):
            # 入站校验违规：不调用户回调，直接 deny（对位 Rust process_id_valid /
            # host_valid 不满足即跳过 controller 走 fail-closed 分支）
            decision = NetworkPolicyDecision.deny(NETWORK_POLICY_DENIAL_REASON)
        elif request.process_id not in self.process.live_processes:
            # 活进程登记表查无此 processId（对位 Rust get_session → None →
            # controller None 的 fail-closed 分支）：不调用户回调直接 deny
            decision = NetworkPolicyDecision.deny(NETWORK_POLICY_DENIAL_REASON)
        else:
            # 回调时限逐进程取 start 的 networkProxy.policyDecisionTimeoutMs
            # （对位 Rust controller.timeout——start_process 从 params 取
            # policy_decision_timeout_ms）；未声明回退全局时限
            timeout = self.process.live_processes.get(request.process_id)
            try:
                decision = await asyncio.wait_for(
                    self._network_policy(request),
                    timeout if timeout is not None else self._network_policy_timeout,
                )
            except asyncio.TimeoutError:
                # 回调超时即 deny（对位 Rust timeout(controller.timeout, ...)
                # 的 unwrap_or_else 兜底）
                decision = NetworkPolicyDecision.deny(NETWORK_POLICY_DENIAL_REASON)
            decision = _clamp_policy_decision(decision)
        return NetworkPolicyRequestResponse(decision=decision).model_dump(
            by_alias=True, exclude_none=True
        )

    @classmethod
    def from_stdio(
        cls,
        program: str = StdioTransport.DEFAULT_PROGRAM,
        args: tuple[str, ...] | list[str] = StdioTransport.DEFAULT_ARGS,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        connections: int = 1,
        request_timeout: float | None = None,
        stderr_handler: Callable[[str], None] | None = None,
        reconnect: ReconnectStrategy | None = ReconnectStrategy(),
        resume_session_id: str | None = None,
        initialize_timeout: float | None = None,
        network_policy: (
            Callable[[NetworkPolicyRequestParams], Awaitable[NetworkPolicyDecision]]
            | None
        ) = None,
        network_policy_timeout: float = 30.0,
    ) -> ExecutorClient:
        """以 stdio 命令创建客户端（本地 nova-exec-server 或 SSH 远程同一形态）。

        - 本地：`ExecutorClient.from_stdio()`
        - SSH：`ExecutorClient.from_stdio(program="ssh",
              args=["user@host", "nova-exec-server", "--listen", "stdio"])`
        - `connections=2`：spawn 两条命令，控制/数据面分离
        - `reconnect`：断线重 spawn 策略。注意重 spawn 后服务端是新进程，
          旧会话必然 unknown session，resume 一次失败即转 failed——该路径
          保证调用方拿到明确断线错误而非干等（WS 长驻服务端才是 resume
          的真正受益者）；`None` 关闭恢复
        """

        def transport_factory() -> StdioTransport:
            return StdioTransport(
                program=program,
                args=list(args),
                env=env,
                cwd=cwd,
                request_timeout=request_timeout,
                stderr_handler=stderr_handler,
            )

        return cls(
            transport_factory=transport_factory,
            connections=connections,
            reconnect=reconnect,
            resume_session_id=resume_session_id,
            initialize_timeout=initialize_timeout,
            network_policy=network_policy,
            network_policy_timeout=network_policy_timeout,
        )

    @classmethod
    def from_environment(
        cls,
        environment: ResolvedEnvironment,
        *,
        connections: int = 1,
        reconnect: ReconnectStrategy | None = ReconnectStrategy(),
        resume_session_id: str | None = None,
        initialize_timeout: float | None = None,
        network_policy: (
            Callable[[NetworkPolicyRequestParams], Awaitable[NetworkPolicyDecision]]
            | None
        ) = None,
    ) -> ExecutorClient:
        """按解析后的环境创建客户端（对位 codex EnvironmentToml → 传输参数）。

        `environment.connect_timeout_sec` 接线为 connect 总时限；
        `initialize_timeout` 接线 initialize 握手时限（缺省回退环境声明的
        `initialize_timeout_sec`，再缺省用包级默认）；其余语义与构造器/
        from_stdio 一致（未连接状态返回，`async with`/`connect()` 连）。
        """
        if initialize_timeout is None:
            # 环境声明的握手预算（与 connect_timeout 分账）——显式 kwarg 优先
            initialize_timeout = environment.initialize_timeout_sec
        if environment.kind == "ws":
            assert environment.url is not None
            client = cls(
                environment.url,
                token=environment.token,
                connections=connections,
                reconnect=reconnect,
                resume_session_id=resume_session_id,
                initialize_timeout=initialize_timeout,
                network_policy=network_policy,
            )
        elif environment.kind == "stdio":
            client = cls.from_stdio(
                program=environment.program or StdioTransport.DEFAULT_PROGRAM,
                args=environment.args,
                env=environment.env,
                cwd=environment.cwd,
                connections=connections,
                reconnect=reconnect,
                resume_session_id=resume_session_id,
                initialize_timeout=initialize_timeout,
                network_policy=network_policy,
            )
        else:  # local：内建环境 = 本机 stdio 缺省 spawn
            client = cls.from_stdio(
                connections=connections,
                reconnect=reconnect,
                resume_session_id=resume_session_id,
                initialize_timeout=initialize_timeout,
                network_policy=network_policy,
            )
        client._connect_timeout = environment.connect_timeout_sec
        return client

    @property
    def transport(self) -> Transport:
        """控制面主连接（兼容旧代码直接访问 client.transport）；
        重连后自动指向新底层传输实例"""
        return self._control.current_transport

    @property
    def session_id(self) -> str | None:
        """控制面会话 id（connect 握手成功后可用；resume/诊断用）"""
        return self._control.session_id

    @property
    def notifications(self) -> NotificationRouter:
        """统一通知分发器（公共只读入口：http body delta /
        networkPolicyDecision 等按方法订阅；fs read_stream 已由
        FileSystemManager 内部接管，勿重复注册同名流）"""
        return self._router

    async def on_policy_decision(
        self, *, process_id: str | None = None
    ) -> AsyncIterator[NetworkPolicyDecisionNotification]:
        """network/policyDecision 审计通知的类型化订阅（糖 API）。

        executor 每次做出网络裁决都推一条审计通知（丢只影响审计完整性，
        不影响裁决本身）。`process_id` 可选过滤指定进程。用法：

        ```python
        async for event in client.on_policy_decision():
            print(event.host, event.decision, event.reason)
        ```

        取消/退出迭代即自动注销订阅（不再收到后续通知）。
        """
        queue = self._router.register_method(NETWORK_POLICY_DECISION)
        try:
            while True:
                message = await queue.get()
                error = message.get("error")
                if error is not None:
                    # fail_channel 合成的终态事件（连接清扫——挂起消费者响亮
                    # 收尾而非干等下一条）
                    raise TransportError(error)
                try:
                    notification = NetworkPolicyDecisionNotification.model_validate(
                        message.get("params") or {}
                    )
                except ValidationError:
                    # 畸形通知丢弃不杀订阅（对位 Rust client.rs 的
                    # let Ok(params) = ... else debug + return Ok 分支）
                    logger.warning(
                        "ignoring malformed network policy decision notification",
                        exc_info=True,
                    )
                    continue
                if process_id is not None and notification.process_id != process_id:
                    continue
                yield notification
        finally:
            self._router.unregister_method_queue(NETWORK_POLICY_DECISION, queue)

    async def _handshake(
        self, transport: Transport, resume_session_id: str | None
    ) -> InitializeResponse:
        """initialize 握手：版本 major 检查 + resume 会话一致性检查 +
        initialized 通知（首连与断线重连同路径——对位 Rust initialize_rpc
        被 connect/resume_once 共用）。

        整段握手（initialize 请求 → 版本/会话检查 → initialized 通知）受专用
        时限约束（默认 INITIALIZE_TIMEOUT 10s，构造器 initialize_timeout
        可覆盖；对位 Rust `timeout(initialize_timeout, ...)`）——常规请求
        无隐含超时，唯握手带 deadline：卡死传输上的首连/重连不能无限干等。
        """
        params = InitializeParams(
            clientName=self._client_name, resumeSessionId=resume_session_id
        )

        async def do_handshake() -> InitializeResponse:
            result = await transport.send_request(
                INITIALIZE, params.model_dump(by_alias=True, exclude_none=True)
            )
            response = InitializeResponse.model_validate(result)
            self._check_protocol_version(response.protocol_version)
            if (
                resume_session_id is not None
                and response.session_id != resume_session_id
            ):
                # resume 路径：服务端必须回到同一会话——先于 INITIALIZED 判
                # 违约（对位 Rust initialize_rpc：session 校验在 notify
                # 之前）；recovery._install 的同款检查保留兜底
                raise ProtocolError(
                    f"exec-server initialized an unexpected session "
                    f"{response.session_id}（期望恢复 {resume_session_id}）"
                )
            await transport.send_notification(INITIALIZED, {})
            return response

        try:
            return await asyncio.wait_for(do_handshake(), self._initialize_timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"initialize handshake timed out after {self._initialize_timeout}s"
            ) from None

    def _on_initialized(self, response: InitializeResponse) -> None:
        """握手成功上抛：initialize 捎带的环境元数据直接填充缓存（多条连接
        连的是同一执行端进程，元数据一致，任一连接的捎带都可作为缓存值）"""
        if response.environment_info is not None and self._environment_info is None:
            self._environment_info = response.environment_info

    async def connect(self) -> None:
        """连接并初始化（每条连接各自握手，协议版本 major 不等即拒绝）"""
        try:
            if self._connect_timeout is not None:
                # 环境声明的连接总时限（config 的 connect_timeout_sec）
                await asyncio.wait_for(self._pool.connect(), self._connect_timeout)
            else:
                await self._pool.connect()
        except Exception:
            # 任一连接握手失败即整体断开，避免半连接状态
            await self.disconnect()
            raise

    @staticmethod
    def _check_protocol_version(server_version: str | None) -> None:
        """major 不等即拒绝；服务端缺省（None）或空串（旧协议线上省略字段，
        serde 缺省即空串）放行并告警（对位 Rust
        `if !response.protocol_version.is_empty()`——空串跳过检查）。"""
        if not server_version:
            return
        server_major = server_version.split(".", 1)[0]
        client_major = PROTOCOL_VERSION.split(".", 1)[0]
        if server_major != client_major:
            raise ProtocolError(
                f"协议版本不兼容：服务端 {server_version}，客户端 {PROTOCOL_VERSION}（major 不等）"
            )

    async def disconnect(self) -> None:
        """断开连接（全部通道）"""
        await self._pool.disconnect()

    async def _wait_for_process_starts(self) -> None:
        """恢复编排钩子（对位 Rust Inner::wait_for_process_starts）：断线恢复
        重建传输前等在飞 process/start 归零——断线窗口内在飞的 start 以旧
        对映代传输的失败（并调度 terminate 清理）收尾后恢复才继续，进程
        绝不会在 resume 后的会话里运行而客户端不知其存在。

        接线：构造期经 make_managed 的 pre_recovery 注入控制面
        ManagedTransport，由其 _recover 顶部（首次重连尝试前，对位
        client_recovery.rs recover() 里的
        `timeout_at(deadline, self.wait_for_process_starts())`）await 本
        钩子；总时限由恢复编排的策略 timeout 兜底。
        """
        await self._process_start_gate.wait_idle()

    async def refresh_connection(self) -> None:
        """计划内更换后的显式连接刷新（对位 Rust Environment::refresh_connection）。

        取消全部通道的在途恢复 → 退役旧会话 → 全新连接（不 resume）→
        探活。调用场景：executor 已更换（重供给/端点变更），需要全新会话而
        非等待旧会话恢复放弃。刷新失败不恢复旧会话——任一通道失败时把
        已刷新的通道一并退役，不留"控制面新会话 + 数据面旧/断连"的分裂态；
        整体落回 disconnected，后续 connect() 重试全新连接。
        """
        # 元数据缓存属于旧会话：刷新起手即清空——中途失败不留旧会话的
        # 缓存值，下次访问经新会话重新拉取
        self._environment_info = None
        refreshed: list[ManagedTransport] = []
        try:
            for channel in self._pool._channels.values():
                if not isinstance(channel, ManagedTransport):
                    raise ConnectionError(
                        "connection refresh requires factory-backed managed transports"
                    )
                await channel.refresh_connection()
                refreshed.append(channel)
        except Exception:
            # 双通道刷新非原子的收口：已刷新通道一并退役（对位单通道失败
            # 不恢复旧会话的语义扩展到多通道），整体落回 disconnected
            for channel in refreshed:
                try:
                    await channel.disconnect()
                except Exception:
                    pass
            raise
        # 探活（对位 Rust 刷新末尾的 environment_status 实况检查）
        await self.environment_status()

    async def environment_info(
        self, *, signal: AbortSignal | None = None
    ) -> EnvironmentInfo:
        """获取环境信息（initialize 捎带/首次拉取后缓存，连接生命周期内不重复请求）

        `signal`：取消词汇——缓存未命中的拉取可被 signal 打断（透传
        force_environment_info）。
        """
        if self._environment_info is None:
            # 对位 Rust get_or_try_init(force_environment_info)：缓存未命中走
            # 同一探活路径，超时同样触发连接退役与会话恢复
            self._environment_info = await self.force_environment_info(signal=signal)
        return self._environment_info

    async def force_environment_info(
        self, *, signal: AbortSignal | None = None
    ) -> EnvironmentInfo:
        """强制拉取环境信息——不读缓存、不写缓存（对位 rust force_environment_info）

        ENVIRONMENT_INFO_TIMEOUT（30s）覆盖发送与等待全程；超时即关闭所探
        连接（控制面）并触发会话恢复，调用方拿到超时错误——不重试失败请求
        （对位 Rust：close_transport + request_recovery 后返回 TimedOut）。

        `signal`：取消词汇——钉传输的恢复等待与探活在飞两段都可被打断；
        AbortedError 直通（不触发连接退役——取消是用户语义，非连接死因）。
        """
        # 先等恢复落定再钉所探传输（对位 Rust 先 `rpc_client().await` 取到
        # 实际服役的 client 再探）——恢复窗内钉住旧死传输，黑洞新连接会因
        # 身份守卫（ptr_eq）被正确忽略而无人退役；实发直走钉住的传输
        # （对位 Rust timeout(ENVIRONMENT_INFO_TIMEOUT, rpc_client.call(...))
        # ——不经连接池二次解析，恒落控制面对映代连接）
        probe_transport = await await_with_signal(
            self._control._await_transport(), signal
        )
        try:
            result = await asyncio.wait_for(
                probe_transport.send_request(ENVIRONMENT_INFO, **_signal_kwarg(signal)),
                ENVIRONMENT_INFO_TIMEOUT,
            )
        except asyncio.TimeoutError:
            error = TimeoutError(f"request {ENVIRONMENT_INFO} timed out")
            # 只退役所探连接；陈旧探针/已在恢复由 ManagedTransport 守卫忽略
            await self._control.request_recovery(str(error), transport=probe_transport)
            raise error from None
        return EnvironmentInfo.model_validate(result)

    async def environment_status(
        self, *, signal: AbortSignal | None = None
    ) -> EnvironmentStatus:
        """获取环境状态（对位 Rust environment_status：fail-fast 健康检查）

        只复用既有连接、绝不等待/触发恢复（对位
        rpc_client_without_recovery——"Health checks only reuse an existing
        RPC connection and never initiate recovery"）：恢复中/未连接即败；
        专用 ENVIRONMENT_STATUS_TIMEOUT（10s）时限，超时只回超时错误——
        不退役连接（与 Rust 一致：连接生死判定归 environment/info 探活路径）。

        `signal`：取消词汇——探活在飞可被打断，AbortedError 直通。
        """
        control = self._control
        if control.is_connected:
            transport = control.current_transport
        elif control.state == "failed":
            raise ConnectionError(control.failure_message or "connection failed")
        elif control.state == "disconnected":
            raise ConnectionError("not connected")
        else:
            # recovering（或 connected 但底层已断、断线回调未至）
            raise ConnectionError("exec-server environment is recovering")
        try:
            result = await asyncio.wait_for(
                transport.send_request(ENVIRONMENT_STATUS, **_signal_kwarg(signal)),
                ENVIRONMENT_STATUS_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"request {ENVIRONMENT_STATUS} timed out") from None
        return EnvironmentStatus.model_validate(result)

    async def read_environment_config(
        self, cwd: str, config_paths: list[list[str]]
    ) -> EnvironmentConfigReadResponse:
        """代读执行端本机配置层栈（environmentConfig/read，v1.4 起）

        executor 读自己所在机器的配置层（user 层 ~/.nova/exec-server/config.toml
        TOML + project 层 <cwd>/.nova/settings.json JSON），按 config_paths
        键路径投影后如实回传——不合并不裁决（层合并与 trust 裁决归客户端）。
        调用前先经 environment_info() 的 capabilities.environment_config_read
        门控（旧服务端无此端点）。

        `cwd` 上线前归一为 PathUri（file:// scheme——服务端按 PathUri
        反序列化，原生绝对路径会被拒绝；已是 file:// 前缀的原样透传，
        归一纪律同 nova_protocol exec_server_wire._file_url）。
        """
        params = EnvironmentConfigReadParams(
            cwd=_file_url(cwd), configPaths=config_paths
        )
        result = await self._pool.send_request(
            ENVIRONMENT_CONFIG_READ, params.model_dump(by_alias=True)
        )
        return EnvironmentConfigReadResponse.model_validate(result)

    async def http_request(
        self,
        method: str,
        url: str,
        *,
        headers: list[tuple[str, str] | HttpHeader] | None = None,
        body: bytes | None = None,
        timeout_ms: int | None = None,
        redirect_policy: str = "follow",
        stream_response: bool = False,
        request_id: str | None = None,
        signal: AbortSignal | None = None,
    ) -> HttpRequestResponse:
        """经执行器代发 HTTP 请求（http/request）

        - 默认缓冲模式：完整响应体随响应返回
        - stream_response=True：响应头先回、响应体经 `http/request/bodyDelta`
          通知推送——SDK 内部收集增量、done 后把拼装好的完整 body 放进响应
          （需要更早拿到增量可改用 notifications 路由器按 requestId 订阅）
        - headers 元组为字面量头；需执行端环境变量填值（凭据不跨线委派）
          时传 HttpHeader(name=..., value="前缀", valueEnvVar="变量名")——
          执行端敏感变量有保护名单拦截（nova 自家 token/云凭据/供应商 key）
        - `signal`：取消词汇——请求在飞与流式消费（bodyDelta 的 queue 等待）
          两段都可被打断；AbortedError 直通且路由注销（收摊纪律不破）
        """
        import uuid as _uuid

        rid = request_id or f"http-{_uuid.uuid4().hex}"
        header_models = [
            h if isinstance(h, HttpHeader) else HttpHeader(name=h[0], value=h[1])
            for h in (headers or [])
        ]
        queue = self._router.register_http_body_stream(rid) if stream_response else None
        params = HttpRequestParams(
            method=method,
            url=url,
            headers=header_models,
            body=ByteChunk(data=body) if body is not None else None,
            timeoutMs=timeout_ms,
            redirectPolicy=HttpRedirectPolicy(redirect_policy),
            requestId=rid,
            streamResponse=stream_response,
        )
        try:
            result = await self._pool.send_request(
                HTTP_REQUEST,
                params.model_dump(by_alias=True, exclude_none=True),
                signal=signal,
            )
            response = HttpRequestResponse.model_validate(result)
        except BaseException:
            # 请求失败（服务端 error/参数拒绝/断线/取消——AbortedError 取消词汇
            # 同走这里）或响应畸形时摘掉刚注册的流式路由（对位 Rust
            # HttpBodyStreamRegistration::drop 守卫——否则路由残留到下次清扫）
            if stream_response:
                self._router.unregister_http_body_stream(rid)
            raise
        if stream_response:
            # bodyDelta 推送收集：done 或 error 终止，拼装完整响应体；
            # seq 严格序校验（对位 Rust recv()：缺口即协议错误）。
            # queue 等待经竞速包装：signal 触发即抛 AbortedError，finally
            # 注销路由（收摊纪律与断线清扫同）
            chunks: list[bytes] = []
            expected_seq = 1
            try:
                while True:
                    event = await await_with_signal(queue.get(), signal)
                    delta = HttpRequestBodyDeltaNotification.model_validate(
                        event["params"]
                    )
                    if delta.error is not None:
                        raise ProtocolError(f"http body stream error: {delta.error}")
                    if delta.seq != expected_seq:
                        raise ProtocolError(
                            f"http body stream seq gap: expected {expected_seq}, got {delta.seq}"
                        )
                    expected_seq += 1
                    chunks.append(delta.delta.data)
                    if delta.done:
                        break
            finally:
                self._router.unregister_http_body_stream(rid)
            response.body = ByteChunk(data=b"".join(chunks))
        return response

    async def __aenter__(self) -> ExecutorClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
