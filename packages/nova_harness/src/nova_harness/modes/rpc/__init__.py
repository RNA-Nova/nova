"""Nova Harness JSON-RPC over stdio server.

在 Nova Harness 中，外部前端（TUI/聊天/IDE）只能通过 JSON-RPC over stdio
与本进程交互，因此 UI 原语在这里是一等公民：
- ``NovaRpcServer``：主服务器与消息分发
- ``UIRouter``：所有 ``extension/ui/*`` 相关消息路由
- ``RpcUIContext``：UI 原语到 JSON-RPC 的桥接实现
- ``primitives``：UI 原语的 params/response schema 与标准方法集合
- ``types``：RPC 模式下会用到的 UI 原语类型 re-export
- ``JsonRpcProtocol``：JSON-RPC 2.0 消息构造
- ``StdioTransport`` / ``OutputGuard``：stdio 传输与输出保护
- ``RpcMethods``：AgentSession 能力对应的 JSON-RPC 方法
"""

from nova_harness.modes.rpc.errors import JSONRPCError
from nova_harness.modes.rpc.server import NovaRpcServer
from nova_harness.modes.rpc.ui import UIRouter
from nova_harness.modes.rpc.ui_context import RpcUIContext

__all__ = ["JSONRPCError", "NovaRpcServer", "RpcUIContext", "UIRouter"]
