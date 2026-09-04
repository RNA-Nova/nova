"""传输层抽象。

为 Nova 前后端通信提供统一的 Transport 接口：

- ``StdioTransport``：JSON-RPC over stdin/stdout，供 TUI/CLI 嵌入使用。
- ``MemoryTransport``：内存传输，主要用于测试。
"""

from nova_harness.core.rpc.transport.base import Transport
from nova_harness.core.rpc.transport.memory import MemoryTransport
from nova_harness.core.rpc.transport.stdio import StdioTransport

__all__ = [
    "Transport",
    "StdioTransport",
    "MemoryTransport",
]
