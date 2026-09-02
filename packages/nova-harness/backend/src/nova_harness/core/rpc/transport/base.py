"""Transport 抽象接口。

所有前后端通信通道（stdio、WebSocket、内存）都实现此接口，
使 ``NovaServer`` 和 ``TransportUIContext`` 不依赖具体传输方式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class Transport(ABC):
    """统一传输抽象。

    负责双向收发 JSON 消息。传输层不解析 JSON-RPC 语义，只保证
    每条消息是完整的 ``dict``。
    """

    @property
    @abstractmethod
    def supports_binary(self) -> bool:
        """是否支持原始二进制帧（WebSocket 可以，stdio 不行）。"""

    @abstractmethod
    async def open(self) -> None:
        """打开传输通道。"""

    @abstractmethod
    async def read(self) -> Dict[str, Any] | None:
        """读取下一条消息。

        返回 ``None`` 表示对端已关闭且不会再有消息。
        """

    @abstractmethod
    async def write(self, msg: Dict[str, Any]) -> None:
        """发送一条 JSON 消息。"""

    @abstractmethod
    async def send_binary(
        self, data: bytes, metadata: Dict[str, Any] | None = None
    ) -> None:
        """发送二进制数据（仅在 ``supports_binary`` 为 true 时有效）。"""

    @abstractmethod
    async def receive_binary(self) -> tuple[bytes, Dict[str, Any]] | None:
        """接收二进制数据，返回 ``(data, metadata)``。"""

    @abstractmethod
    async def close(self) -> None:
        """关闭传输通道。"""
