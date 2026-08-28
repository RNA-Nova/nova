"""ExecutorClient 统一入口"""

from __future__ import annotations

from collections.abc import Callable

from .errors import ProtocolError
from .fs import FileSystemManager
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
from .transport import StdioTransport, Transport, WebSocketTransport


class ExecutorClient:
    """nova-executor 客户端

    传输三选一：

    - `url`（+ `token`）：WebSocket 连接（现状默认）
    - `transport`：调用方自建单连接传输（如 `StdioTransport`）
    - `transport_factory`：传输工场，多连接时逐条新建

    `connections`：连接数——1 = 全部走单连接（现状行为）；
    2 = 控制面/数据面分离（read_stream/write_stream 等大流量方法走独立
    第二条连接，不阻塞 LLM 工具调用）。更复杂的通道布局可直接构造
    `TransportPool` 传给管理器。
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        auto_reconnect: bool = True,
        max_payload: int = 100 * 1024 * 1024,
        request_timeout: float = 30.0,
        *,
        transport: Transport | None = None,
        transport_factory: Callable[[], Transport] | None = None,
        connections: int = 1,
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

        if connections == 1:
            control = transport if transport is not None else transport_factory()
            channels: dict[str, Transport] = {CHANNEL_CONTROL: control}
        else:
            channels = {
                CHANNEL_CONTROL: transport_factory(),
                CHANNEL_DATA: transport_factory(),
            }

        self._pool = TransportPool(channels)
        # 兼容旧代码直接访问 client.transport（控制面主连接）
        self.transport = channels[CHANNEL_CONTROL]
        self.process = ProcessManager(self._pool)
        self.fs = FileSystemManager(self._pool)
        self.pty = PtyManager(self._pool, self.process)
        self.auto_reconnect = auto_reconnect
        # 环境元数据缓存：initialize 捎带即填充；旧服务端未捎带时首个
        # environment_info() 调用回退单次 environment/info 拉取填充
        self._environment_info: EnvironmentInfo | None = None

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
    ) -> ExecutorClient:
        """以 stdio 命令创建客户端（本地 nova-executor 或 SSH 远程同一形态）。

        - 本地：`ExecutorClient.from_stdio()`
        - SSH：`ExecutorClient.from_stdio(program="ssh",
              args=["user@host", "nova-executor", "--listen", "stdio"])`
        - `connections=2`：spawn 两条命令，控制/数据面分离
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

        return cls(transport_factory=transport_factory, connections=connections)

    async def connect(self) -> None:
        """连接并初始化（每条连接各自握手，协议版本 major 不等即拒绝）"""
        await self._pool.connect()
        try:
            for transport in self._pool.iter_transports():
                params = InitializeParams(clientName="nova-executor-py")
                result = await transport.send_request(
                    INITIALIZE, params.model_dump(by_alias=True)
                )
                response = InitializeResponse.model_validate(result)
                self._check_protocol_version(response.protocol_version)
                # initialize 捎带的环境元数据直接填充缓存（多条连接连的是同一
                # 执行端进程，元数据一致，任一连接的捎带都可作为缓存值）
                if response.environment_info is not None:
                    self._environment_info = response.environment_info
                await transport.send_notification(INITIALIZED, {})
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
            result = await self.transport.send_request(ENVIRONMENT_INFO)
            self._environment_info = EnvironmentInfo.model_validate(result)
        return self._environment_info

    async def environment_status(self) -> EnvironmentStatus:
        """获取环境状态"""
        result = await self.transport.send_request(ENVIRONMENT_STATUS)
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
        result = await self.transport.send_request(
            ENVIRONMENT_CONFIG_READ, params.model_dump(by_alias=True)
        )
        return EnvironmentConfigReadResponse.model_validate(result)

    async def __aenter__(self) -> ExecutorClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
