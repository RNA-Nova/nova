"""RemoteNovaClient 端到端测试：真实 WS 监听 + token 鉴权。

对位 codex Remote 形态的契约验证——外部宿主连接常驻服务器的 ws 端，
走与 stdio/进程内完全相同的协议路径。
"""

from __future__ import annotations

from typing import Any

import pytest
from nova_server.client.remote import RemoteNovaClient
from nova_server.connection import ConnectionOrigin
from nova_server.protocol.methods.state import ServerState
from nova_server.rpc.cli import build_rpc_methods
from nova_server.server import RpcServer
from nova_server.transport.websocket import WebSocketAcceptor

_TOKEN = "test-token-0600"


@pytest.fixture
async def server_url():
    """起一个真实 ws 服务器（loopback 随机端口 + token），返回 (url, shutdown)。"""
    state = ServerState()
    server = RpcServer(build_rpc_methods(state), state)

    async def _on_connection(transport) -> None:
        await server.add_connection(transport, origin=ConnectionOrigin.WEBSOCKET)

    acceptor = WebSocketAcceptor(
        "127.0.0.1",
        0,
        token=_TOKEN,
        on_connection=_on_connection,
    )
    await acceptor.start()
    url = f"ws://127.0.0.1:{acceptor.port}"
    server_task = __import__("asyncio").create_task(server.run())

    async def _shutdown() -> None:
        server.shutdown()
        server_task.cancel()
        try:
            await server_task
        except __import__("asyncio").CancelledError:
            pass
        await acceptor.close()
        await state.dispose_runtime()

    yield url, _shutdown
    await _shutdown()


@pytest.mark.asyncio
async def test_remote_request_initialize(server_url):
    url, shutdown = server_url
    client = await RemoteNovaClient.connect(url, token=_TOKEN)
    try:
        result: Any = await client.request("initialize", {})
        assert result["version"]
        assert "session" in result["capabilities"]["domains"]
    finally:
        await client.shutdown()
    await shutdown()


@pytest.mark.asyncio
async def test_remote_rejects_bad_token(server_url):
    url, _shutdown = server_url
    with pytest.raises(Exception) as exc_info:
        await RemoteNovaClient.connect(url, token="wrong-token")
    # 握手期 401 拒绝（websockets 抛 InvalidStatus）——不是静默降级
    assert "401" in str(exc_info.value) or "denied" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_remote_query_token_fallback(server_url):
    """``?token=`` query 兜底（浏览器 WS 形态——不能自定义头）。"""
    url, shutdown = server_url
    client = await RemoteNovaClient.connect(f"{url}?token={_TOKEN}")
    try:
        result: Any = await client.request("initialize", {})
        assert result["version"]
    finally:
        await client.shutdown()
    await shutdown()


@pytest.mark.asyncio
async def test_remote_two_clients_isolated(server_url):
    """多客户端并存：各自独立连接，互不串线（对齐服务器多客户端语义）。"""
    url, shutdown = server_url
    c1 = await RemoteNovaClient.connect(url, token=_TOKEN)
    c2 = await RemoteNovaClient.connect(url, token=_TOKEN)
    try:
        r1, r2 = await __import__("asyncio").gather(
            c1.request("initialize", {}),
            c2.request("initialize", {}),
        )
        assert r1["version"] and r2["version"]
    finally:
        await c1.shutdown()
        await c2.shutdown()
    await shutdown()
