"""ExecutorClient 统一入口（对位 Rust client.rs 的装配层）"""

from __future__ import annotations

from collections.abc import Callable

from .errors import ProtocolError
from .fs import FileSystemManager
from .notifications import NotificationRouter
from .pool import CHANNEL_CONTROL, CHANNEL_DATA, TransportPool
from .process import ProcessManager
from .protocol import (
    ENVIRONMENT_CONFIG_READ,
    ENVIRONMENT_INFO,
    ENVIRONMENT_STATUS,
    INITIALIZE,
    INITIALIZED,
    PROTOCOL_VERSION,
    EnvironmentConfigReadParams,
    EnvironmentConfigReadResponse,
    EnvironmentInfo,
    EnvironmentStatus,
    InitializeParams,
    InitializeResponse,
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
        self.pty = PtyManager(self._pool, self.process)

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
        )

    @property
    def transport(self) -> Transport:
        """控制面主连接（兼容旧代码直接访问 client.transport）；
        重连后自动指向新底层传输实例"""
        return self._control.current_transport

    @property
    def session_id(self) -> str | None:
        """控制面会话 id（connect 握手成功后可用；resume/诊断用）"""
        return self._control.session_id

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

    async def __aenter__(self) -> ExecutorClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
