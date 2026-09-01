"""ExecutorClient 统一入口（对位 Rust client.rs 的装配层）"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from .environments import ResolvedEnvironment
from .errors import ProtocolError
from .fs import FileSystemManager
from .notifications import NotificationRouter
from .pool import CHANNEL_CONTROL, CHANNEL_DATA, TransportPool
from .process import ProcessManager
from .protocol import (
    ENVIRONMENT_CONFIG_READ,
    ENVIRONMENT_INFO,
    ENVIRONMENT_STATUS,
    HTTP_REQUEST,
    HTTP_REQUEST_BODY_DELTA,
    INITIALIZE,
    INITIALIZED,
    NETWORK_POLICY_DECISION,
    NETWORK_POLICY_REQUEST,
    PROTOCOL_VERSION,
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
)
from .pty import PtyManager
from .recovery import ManagedTransport, ReconnectStrategy
from .transport import StdioTransport, Transport, WebSocketTransport

#: 默认客户端名（initialize 握手携带）
DEFAULT_CLIENT_NAME = "nova-executor-client"


class ExecutorClient:
    """nova-executor 客户端

    传输三选一：

    - `url`（+ `token`）：WebSocket 连接（现状默认）
    - `transport`：调用方自建单连接传输（如 `StdioTransport`）——无重建
      手段，断线即失败（不恢复）
    - `transport_factory`：传输工场，断线重连经它新造底层传输

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

    `network_policy`：网络沙箱裁决回调（托管网络进程触碰 "ask" 域名时，
    服务端反向请求 `network/policyRequest`，本回调收
    `NetworkPolicyRequestParams`、回 `NetworkPolicyDecision`）。
    None（默认）不注册处理器——服务端收到 METHOD_NOT_FOUND 并按
    fail-closed 拒绝该访问（安全缺省）；回调超时/异常同样 fail-closed。
    注册挂在控制面连接上，断线重连自动重挂。
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        max_payload: int = 100 * 1024 * 1024,
        request_timeout: float = 30.0,
        *,
        transport: Transport | None = None,
        transport_factory: Callable[[], Transport] | None = None,
        connections: int = 1,
        reconnect: ReconnectStrategy | None = ReconnectStrategy(),
        resume_session_id: str | None = None,
        client_name: str = DEFAULT_CLIENT_NAME,
        network_policy: (
            Callable[[NetworkPolicyRequestParams], Awaitable[NetworkPolicyDecision]]
            | None
        ) = None,
    ):
        if transport is None and transport_factory is None:
            if url is None:
                raise ValueError("url / transport / transport_factory 至少提供一个")

            def transport_factory(  # noqa: F811——WebSocket 传输工场
                url: str = url,
                token: str | None = token,
                max_payload: int = max_payload,
                request_timeout: float = request_timeout,
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
                resume_session_id=resume_session_id,
                handshake=self._handshake,
                on_initialized=self._on_initialized,
                on_failed=lambda message, ch=channel: self._router.fail_channel(
                    ch, message
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
        self.process = ProcessManager(self._pool)
        self.fs = FileSystemManager(self._pool, router=self._router)
        self.pty = PtyManager(self.process)

        self._network_policy = network_policy
        if network_policy is not None:
            # 裁决处理器挂控制面（ManagedTransport 重连自动重挂）
            self._control.register_request_handler(
                NETWORK_POLICY_REQUEST, self._answer_network_policy
            )

        #: 环境声明的连接总时限（from_environment 注入；None = 不限制）
        self._connect_timeout: float | None = None

    async def _answer_network_policy(self, params: dict[str, Any]) -> dict[str, Any]:
        """裁决适配：线上载荷解析 → 用户回调 → 结果回线上形态（回调异常由
        传输层转内部错误，服务端 fail-closed 拒决）"""
        assert self._network_policy is not None
        request = NetworkPolicyRequestParams.model_validate(params)
        decision = await self._network_policy(request)
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
        request_timeout: float = 30.0,
        stderr_handler: Callable[[str], None] | None = None,
        reconnect: ReconnectStrategy | None = ReconnectStrategy(),
        resume_session_id: str | None = None,
        network_policy: (
            Callable[[NetworkPolicyRequestParams], Awaitable[NetworkPolicyDecision]]
            | None
        ) = None,
    ) -> ExecutorClient:
        """以 stdio 命令创建客户端（本地 nova-executor 或 SSH 远程同一形态）。

        - 本地：`ExecutorClient.from_stdio()`
        - SSH：`ExecutorClient.from_stdio(program="ssh",
              args=["user@host", "nova-executor", "--listen", "stdio"])`
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
            network_policy=network_policy,
        )

    @classmethod
    def from_environment(
        cls,
        environment: ResolvedEnvironment,
        *,
        connections: int = 1,
        reconnect: ReconnectStrategy | None = ReconnectStrategy(),
        resume_session_id: str | None = None,
        network_policy: (
            Callable[[NetworkPolicyRequestParams], Awaitable[NetworkPolicyDecision]]
            | None
        ) = None,
    ) -> ExecutorClient:
        """按解析后的环境创建客户端（对位 codex EnvironmentToml → 传输参数）。

        `environment.connect_timeout_sec` 接线为 connect 总时限；其余语义与
        构造器/from_stdio 一致（未连接状态返回，`async with`/`connect()` 连）。
        """
        if environment.kind == "ws":
            assert environment.url is not None
            client = cls(
                environment.url,
                connections=connections,
                reconnect=reconnect,
                resume_session_id=resume_session_id,
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
                network_policy=network_policy,
            )
        else:  # local：内建环境 = 本机 stdio 缺省 spawn
            client = cls.from_stdio(
                connections=connections,
                reconnect=reconnect,
                resume_session_id=resume_session_id,
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
                notification = NetworkPolicyDecisionNotification.model_validate(
                    message.get("params") or {}
                )
                if process_id is not None and notification.process_id != process_id:
                    continue
                yield notification
        finally:
            self._router.unregister_method_queue(NETWORK_POLICY_DECISION, queue)

    async def _handshake(
        self, transport: Transport, resume_session_id: str | None
    ) -> InitializeResponse:
        """initialize 握手：版本 major 检查 + initialized 通知（首连与断线
        重连同路径——对位 Rust initialize_rpc 被 connect/resume_once 共用）"""
        params = InitializeParams(
            clientName=self._client_name, resumeSessionId=resume_session_id
        )
        result = await transport.send_request(
            INITIALIZE, params.model_dump(by_alias=True, exclude_none=True)
        )
        response = InitializeResponse.model_validate(result)
        self._check_protocol_version(response.protocol_version)
        await transport.send_notification(INITIALIZED, {})
        return response

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
        """major 不等即拒绝；服务端缺省（旧协议无版本字段）放行并告警。"""
        if server_version is None:
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

    async def environment_info(self) -> EnvironmentInfo:
        """获取环境信息（initialize 捎带/首次拉取后缓存，连接生命周期内不重复请求）"""
        if self._environment_info is None:
            result = await self._pool.send_request(ENVIRONMENT_INFO)
            self._environment_info = EnvironmentInfo.model_validate(result)
        return self._environment_info

    async def environment_status(self) -> EnvironmentStatus:
        """获取环境状态"""
        result = await self._pool.send_request(ENVIRONMENT_STATUS)
        return EnvironmentStatus.model_validate(result)

    async def read_environment_config(
        self, cwd: str, config_paths: list[list[str]]
    ) -> EnvironmentConfigReadResponse:
        """代读执行端本机配置层栈（environmentConfig/read，v1.4 起）

        executor 读自己所在机器的配置层（user 层 ~/.nova/executor/config.toml
        TOML + project 层 <cwd>/.nova/settings.json JSON），按 config_paths
        键路径投影后如实回传——不合并不裁决（层合并与 trust 裁决归客户端）。
        调用前先经 environment_info() 的 capabilities.environment_config_read
        门控（旧服务端无此端点）。
        """
        params = EnvironmentConfigReadParams(cwd=cwd, configPaths=config_paths)
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
    ) -> HttpRequestResponse:
        """经执行器代发 HTTP 请求（http/request）

        - 默认缓冲模式：完整响应体随响应返回
        - stream_response=True：响应头先回、响应体经 `http/request/bodyDelta`
          通知推送——SDK 内部收集增量、done 后把拼装好的完整 body 放进响应
          （需要更早拿到增量可改用 notifications 路由器按 requestId 订阅）
        - headers 元组为字面量头；需执行端环境变量填值（凭据不跨线委派）
          时传 HttpHeader(name=..., value="前缀", valueEnvVar="变量名")——
          执行端敏感变量有保护名单拦截（nova 自家 token/云凭据/供应商 key）
        """
        import uuid as _uuid

        rid = request_id or f"http-{_uuid.uuid4().hex}"
        header_models = [
            h if isinstance(h, HttpHeader) else HttpHeader(name=h[0], value=h[1])
            for h in (headers or [])
        ]
        queue = (
            self._router.register_method(HTTP_REQUEST_BODY_DELTA)
            if stream_response
            else None
        )
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
        result = await self._pool.send_request(
            HTTP_REQUEST, params.model_dump(by_alias=True, exclude_none=True)
        )
        response = HttpRequestResponse.model_validate(result)
        if stream_response:
            # bodyDelta 推送收集：done 或 error 终止，拼装完整响应体
            chunks: list[bytes] = []
            try:
                while True:
                    event = await queue.get()
                    delta = HttpRequestBodyDeltaNotification.model_validate(
                        event["params"]
                    )
                    if delta.error:
                        raise ProtocolError(f"http body stream error: {delta.error}")
                    chunks.append(delta.delta.data)
                    if delta.done:
                        break
            finally:
                self._router.unregister_method_queue(HTTP_REQUEST_BODY_DELTA, queue)
            response.body = ByteChunk(data=b"".join(chunks))
        return response

    async def __aenter__(self) -> ExecutorClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
