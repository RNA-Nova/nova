"""WebSocket 模式占位包。

WebSocket 模式计划通过 WebSocket 与浏览器/IDE 等前端通信。
当前尚未实现，保留此包作为扩展点：

- ``WebSocketUIContext``：未来实现 ``core.types.ui.UIContext``，通过 WebSocket 发送
  request/notify 消息。
- ``WebSocketServer``：WebSocket 服务器，负责与前端建立长连接并转发 Agent 事件。
- ``primitives.py``：WebSocket 模式下前后端交互的 schema；可与 ``modes/rpc`` 共享
  部分字段，也可能根据 Web 场景扩展（如富媒体、DOM 操作等）。

RPC 模式当前通过 ``modes/rpc``（JSON-RPC over stdio）实现。
"""

# Placeholder: WebSocket-based UI mode is not implemented yet.
