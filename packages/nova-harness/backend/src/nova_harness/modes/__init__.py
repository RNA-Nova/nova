"""Nova Harness runtime modes.

本包包含所有运行模式实现：

- ``print``：非交互式命令行运行，使用 ``NoOpUIContext`` 降级。
- ``rpc``：JSON-RPC over stdio，供 TUI/IDE 等本地前端使用。

传输接入归 rpc 连接层：当前 stdio，WebSocket 随 P1 落地（连接化已就绪）。

CLI 入口只做参数解析，具体模式逻辑由本子包各自实现。
"""
