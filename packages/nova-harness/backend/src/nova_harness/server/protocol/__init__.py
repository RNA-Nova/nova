"""JSON-RPC 2.0 协议层。

提供消息解析、构造、路由、错误码定义，以及前后端通信的基础设施：

- ``serialize``：运行时事件 → ``{type, data}`` 信封的直通序列化桥
  （哑管道：零呈现加工，类型发现走构建期 schema 导出）；
- 反向原语的连接路由实现 ``RoutingUIContext`` 已升至 ``rpc.ui_context``
  （空实现 ``NoOpUIContext`` 归 ``core.types.ui``）

所有前后端通信都统一使用 JSON-RPC 2.0 over Transport。
"""

from nova_harness.server.protocol.errors import JSONRPCError
from nova_harness.server.protocol.jsonrpc import (
    JsonRpcMessage,
    build_error,
    build_notification,
    build_request,
    build_response,
    parse_message,
)
from nova_harness.server.protocol.router import MethodRegistry

__all__ = [
    "JSONRPCError",
    "JsonRpcMessage",
    "build_error",
    "build_notification",
    "build_request",
    "build_response",
    "parse_message",
    "MethodRegistry",
]
