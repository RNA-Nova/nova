"""ExecutorClient 统一入口"""

from __future__ import annotations

from .errors import ProtocolError
from .fs import FileSystemManager
from .process import ProcessManager
from .protocol import (
    ENVIRONMENT_INFO,
    ENVIRONMENT_STATUS,
    INITIALIZE,
    INITIALIZED,
    PROTOCOL_VERSION,
    EnvironmentInfo,
    EnvironmentStatus,
    InitializeParams,
    InitializeResponse,
)
from .pty import PtyManager
from .transport import WebSocketTransport


class ExecutorClient:
    """nova-executor 客户端"""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        auto_reconnect: bool = True,
        max_payload: int = 100 * 1024 * 1024,
        request_timeout: float = 30.0,
    ):
        self.transport = WebSocketTransport(
            url=url,
            token=token,
            max_payload=max_payload,
            request_timeout=request_timeout,
        )
        self.process = ProcessManager(self.transport)
        self.fs = FileSystemManager(self.transport)
        self.pty = PtyManager(self.transport, self.process)
        self.auto_reconnect = auto_reconnect

    async def connect(self) -> None:
        """连接并初始化（含协议版本 major 匹配——不等即拒绝）"""
        await self.transport.connect()
        params = InitializeParams(clientName="nova-executor-py")
        result = await self.transport.send_request(
            INITIALIZE, params.model_dump(by_alias=True)
        )
        response = InitializeResponse.model_validate(result)
        self._check_protocol_version(response.protocol_version)
        await self.transport.send_notification(INITIALIZED, {})

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
        """断开连接"""
        await self.transport.disconnect()

    async def environment_info(self) -> EnvironmentInfo:
        """获取环境信息"""
        result = await self.transport.send_request(ENVIRONMENT_INFO)
        return EnvironmentInfo.model_validate(result)

    async def environment_status(self) -> EnvironmentStatus:
        """获取环境状态"""
        result = await self.transport.send_request(ENVIRONMENT_STATUS)
        return EnvironmentStatus.model_validate(result)

    async def __aenter__(self) -> ExecutorClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
