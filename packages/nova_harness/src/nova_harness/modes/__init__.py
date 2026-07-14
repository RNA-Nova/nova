"""Nova Harness runtime modes.

本包包含所有运行模式实现：

- ``print``：非交互式命令行运行，使用 ``NoOpUIContext`` 降级。
- ``rpc``：JSON-RPC over stdio，供 TUI/IDE 等本地前端使用。
- ``websocket``：WebSocket 占位，未来供浏览器/远程前端使用。

CLI 入口只做参数解析，具体模式逻辑由本子包各自实现。
"""
