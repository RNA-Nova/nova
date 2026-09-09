"""nova-server 客户端家族（对位 codex ``app-server-client`` 双形态）。

- ``in_process``：进程内客户端——装配完整 RPC 栈（对位
  ``InProcessAppServerClient``）。
- ``remote``：远程客户端——连接常驻 ``nova-server --listen ws://...``
  （对位 ``RemoteAppServerClient``；codex 另支持 UDS 承载，nova 暂无
  该消费者，未建）。

两者共享同一多路复用语义（``base.MultiplexedClient``）：请求按 id
路由响应、通知帧入有序事件队列。
"""
