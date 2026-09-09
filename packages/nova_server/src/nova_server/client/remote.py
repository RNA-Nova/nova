"""远程 nova-server 客户端（WebSocket）。

对位 codex ``app-server-client::RemoteAppServerClient``：外部宿主连接
一个**已在运行**的 nova-server（``nova-server --listen ws://...``），
经 WebSocket 帧走同一协议。与进程内客户端共享同一套请求/事件语义
（:class:`MultiplexedClient`），仅帧通道不同。

鉴权与服务器 ``WebSocketAcceptor`` 对齐：``Authorization: Bearer``
头优先，``?token=`` query 兜底；hmac 常时比较在服务器侧。

用法::

    client = await RemoteNovaClient.connect(
        "ws://127.0.0.1:8787", token="..."
    )
    result = await client.request("initialize", {})
    await client.shutdown()
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from nova_server.client.base import FrameChannel, MultiplexedClient
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

__all__ = ["RemoteNovaClient"]


class _WsChannel(FrameChannel):
    """WebSocket 连接的帧通道适配（NDJSON：一帧一条文本消息）。"""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def send_frame(self, frame: Dict[str, Any]) -> None:
        await self._ws.send(json.dumps(frame, ensure_ascii=False))

    async def recv_frame(self) -> Optional[Dict[str, Any]]:
        try:
            message = await self._ws.recv()
        except ConnectionClosed:
            return None
        if not message:
            return None
        return json.loads(message)

    async def close_channel(self) -> None:
        await self._ws.close()


class RemoteNovaClient(MultiplexedClient):
    """远程协议客户端：连接常驻 nova-server 的 ws 监听端。"""

    def __init__(self, channel: _WsChannel, ws: Any) -> None:
        super().__init__(channel)
        self._ws = ws

    @classmethod
    async def connect(
        cls,
        url: str,
        *,
        token: Optional[str] = None,
        connect_timeout: float = 10.0,
        open_timeout: Optional[float] = None,
    ) -> "RemoteNovaClient":
        """连接 ws 监听端并完成握手。

        ``token`` 经 ``Authorization: Bearer`` 头携带（服务器同时接受
        ``?token=`` query——那是给不能自定义头的浏览器 WS 用的）。
        """
        headers: Optional[Dict[str, str]] = None
        if token is not None:
            headers = {"Authorization": f"Bearer {token}"}
        ws = await asyncio.wait_for(
            ws_connect(url, additional_headers=headers),
            open_timeout if open_timeout is not None else connect_timeout,
        )
        client = cls(_WsChannel(ws), ws)
        client._spawn_reader()
        return client

    async def shutdown(self) -> None:
        """关闭协议连接（幂等）。服务器侧不受影响——它服务多客户端。"""
        if self._closed:
            return
        self._closed = True
        await self._channel.close_channel()
        await self._stop_reader()
