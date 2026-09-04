"""Nova RPC 通信层。

前后端通信的全部内容，内聚为一个模块，内部分三层：

- ``transport/``：通道层（``Transport`` 抽象 + stdio / memory 实现）——
  dict 消息怎么物理流动。
- ``protocol/``：语义层（JSON-RPC 消息模型、方法路由、线协议 schema、
  事件序列化桥）——流动的 dict 意味着什么。
- ``server.py`` + ``connection.py`` + ``ui_context.py``：组装器
  （``RpcServer`` = 连接注册表 + MethodRegistry + RoutingUIContext，
  含事件广播与并发分派）——多连接一等公民，stdio/memory
  都只是连接来源。

依赖方向单向：transport ← protocol ← server（connection 归 server 层）。
"""

from nova_harness.core.rpc.server import RpcServer

__all__ = ["RpcServer"]
