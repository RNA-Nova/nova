"""进程内 nova-server 客户端。

对位 codex ``app-server-client::InProcessAppServerClient``：在当前进程
装配完整 RPC 栈（``ServerState`` + 全域方法注册表 + ``RpcServer``），
经 ``MemoryTransport`` 对端收发。外部嵌入者与 stdio/websocket 客户端
走**同一协议路径**——事件归约、``event_seq`` 发号、背压与信任来源
分流（MEMORY 属可信来源）全部保真。

harness 层没有 "SDK" 进程内门面（codex 同构：进程内消费走
``RuntimeManager``，协议嵌入走本客户端）。

用法::

    client = await InProcessNovaClient.start()
    result = await client.request("initialize", {})
    async for event in client.events():
        ...
    await client.shutdown()
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from nova_server.client.base import (FrameChannel, MultiplexedClient,
                                     ProtocolError)
from nova_server.connection import ConnectionOrigin
from nova_server.protocol.methods.state import ServerState
from nova_server.rpc.cli import build_rpc_methods
from nova_server.server import RpcServer
from nova_server.transport import MemoryTransport

__all__ = ["InProcessNovaClient"]

# 兼容别名：协议错误类型已上移 base（Remote 客户端抛同一类型）
InProcessServerError = ProtocolError


class _MemoryChannel(FrameChannel):
    """``MemoryTransport`` 对端的帧通道适配。"""

    def __init__(self, transport: MemoryTransport) -> None:
        self._transport = transport

    async def send_frame(self, frame: Dict[str, Any]) -> None:
        await self._transport.write(frame)

    async def recv_frame(self) -> Optional[Dict[str, Any]]:
        return await self._transport.read()

    async def close_channel(self) -> None:
        await self._transport.close()


class InProcessNovaClient(MultiplexedClient):
    """进程内协议客户端：请求/通知/有序事件流三通道。

    生命周期：``start()`` → ``request``/``notify``/``events`` →
    ``shutdown()``。实例不可 fork 复用；多客户端各起一份即可。
    """

    def __init__(
        self,
        channel: _MemoryChannel,
        server: RpcServer,
        state: ServerState,
        server_task: "asyncio.Task[None]",
    ) -> None:
        super().__init__(channel)
        self._server = server
        self._state = state
        self._server_task = server_task

    @classmethod
    async def start(
        cls,
        *,
        state: Optional[ServerState] = None,
        ui: Any = None,
    ) -> "InProcessNovaClient":
        """装配进程内服务端并接入一条 MEMORY 连接。"""
        state = state or ServerState()
        methods = build_rpc_methods(state)
        server = RpcServer(methods, state, ui=ui)

        server_transport = MemoryTransport()
        client_transport = MemoryTransport(peer=server_transport)

        await server.add_connection(server_transport, origin=ConnectionOrigin.MEMORY)
        server_task = asyncio.create_task(server.run())

        client = cls(_MemoryChannel(client_transport), server, state, server_task)
        client._spawn_reader()
        return client

    async def shutdown(self) -> None:
        """关停客户端与服务端（幂等）。

        先关客户端传输（服务端读泵见 EOF 收连接），再请求服务端停止
        并等待主循环退出；最后释放仍挂着的会话运行时。
        """
        if self._closed:
            return
        self._closed = True
        await self._channel.close_channel()
        self._server.shutdown()
        await self._stop_reader()
        self._server_task.cancel()
        try:
            await self._server_task
        except asyncio.CancelledError:
            pass
        # 兜底释放：正常路径 shutdown RPC 已释放；未 createSession 时为空
        await self._state.dispose_runtime()
