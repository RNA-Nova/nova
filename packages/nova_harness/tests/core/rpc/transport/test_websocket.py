"""WebSocket 传输与 acceptor 鉴权测试（连接化 P1）。

覆盖：鉴权矩阵（token 头/query、错 token、Origin 白名单、非 loopback
拒启）、经 RpcServer 的 WS roundtrip（真实多连接）、慢消费者断连。
"""

import asyncio
import json
import sys

import pytest
import websockets

from nova_harness.core.rpc.connection import ConnectionOrigin
from nova_harness.core.rpc.protocol import MethodRegistry
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.server import RpcServer
from nova_harness.core.rpc.transport.websocket import (
    WebSocketAcceptor,
    _is_loopback,
    provision_token,
)

TOKEN = "test-token-0123456789"


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _start_ws_server(extra=None):
    """起真实 RpcServer + WS acceptor；返回 (server, acceptor, port)。"""

    registry = MethodRegistry()
    registry.register("echo", lambda params: params)
    registry.register("initialize", lambda params: {"ok": True})
    if extra:
        for name, handler in extra.items():
            registry.register(name, handler)
    server = RpcServer(registry, ServerState())
    run_task = asyncio.create_task(server.run())

    async def on_connection(transport):
        await server.add_connection(transport, origin=ConnectionOrigin.WEBSOCKET)

    port = _free_port()
    acceptor = WebSocketAcceptor(
        "127.0.0.1", port, token=TOKEN, on_connection=on_connection
    )
    await acceptor.start()
    return server, acceptor, run_task, acceptor.port


async def _stop(server, acceptor, run_task):
    server.shutdown()
    await asyncio.wait_for(run_task, timeout=2.0)
    await acceptor.close()


async def _ws_roundtrip(port, **kwargs):
    """带正确 token 建连，initialize + echo 往返。"""
    uri = f"ws://127.0.0.1:{port}"
    async with websockets.connect(uri, **kwargs) as ws:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
        resp = json.loads(await ws.recv())
        assert resp["id"] == 1 and "result" in resp
        await ws.send(
            json.dumps(
                {"jsonrpc": "2.0", "id": 2, "method": "echo", "params": {"x": 7}}
            )
        )
        resp = json.loads(await ws.recv())
        assert resp == {"jsonrpc": "2.0", "id": 2, "result": {"x": 7}}


# ---------------------------------------------------------------------------
# 鉴权矩阵
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_roundtrip_with_bearer_header():
    server, acceptor, run_task, port = await _start_ws_server()
    try:
        await _ws_roundtrip(
            port, additional_headers={"Authorization": f"Bearer {TOKEN}"}
        )
    finally:
        await _stop(server, acceptor, run_task)


@pytest.mark.asyncio
async def test_ws_roundtrip_with_query_token():
    """?token= query 兜底（浏览器 WebSocket API 不能自定义头）。"""
    server, acceptor, run_task, port = await _start_ws_server()
    try:
        uri = f"ws://127.0.0.1:{port}/?token={TOKEN}"
        async with websockets.connect(uri) as ws:
            await ws.send(
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            )
            resp = json.loads(await ws.recv())
            assert resp["id"] == 1 and "result" in resp
    finally:
        await _stop(server, acceptor, run_task)


@pytest.mark.asyncio
async def test_ws_wrong_token_rejected():
    server, acceptor, run_task, port = await _start_ws_server()
    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(
                f"ws://127.0.0.1:{port}",
                additional_headers={"Authorization": "Bearer wrong"},
            ):
                pass
        assert exc_info.value.response.status_code == 401
    finally:
        await _stop(server, acceptor, run_task)


@pytest.mark.asyncio
async def test_ws_origin_not_in_allowlist_rejected():
    server, acceptor, run_task, port = await _start_ws_server()
    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(
                f"ws://127.0.0.1:{port}",
                additional_headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "Origin": "https://evil.example.com",
                },
            ):
                pass
        assert exc_info.value.response.status_code == 403
    finally:
        await _stop(server, acceptor, run_task)


@pytest.mark.asyncio
async def test_ws_origin_allowlisted_accepted():
    """Origin 白名单命中即放行（本地 web/桌面端落地通道）。"""
    registry = MethodRegistry()
    registry.register("initialize", lambda params: {"ok": True})
    server = RpcServer(registry, ServerState())
    run_task = asyncio.create_task(server.run())

    async def on_connection(transport):
        await server.add_connection(transport, origin=ConnectionOrigin.WEBSOCKET)

    acceptor = WebSocketAcceptor(
        "127.0.0.1",
        _free_port(),
        token=TOKEN,
        allow_origins={"tauri://localhost"},
        on_connection=on_connection,
    )
    await acceptor.start()
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{acceptor.port}",
            additional_headers={
                "Authorization": f"Bearer {TOKEN}",
                "Origin": "tauri://localhost",
            },
        ) as ws:
            await ws.send(
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            )
            resp = json.loads(await ws.recv())
            assert resp["id"] == 1
    finally:
        await _stop(server, acceptor, run_task)


@pytest.mark.asyncio
async def test_non_loopback_without_token_refused():
    """非 loopback 监听且无 token：构造期拒启。"""

    async def noop(transport):
        pass

    with pytest.raises(ValueError):
        WebSocketAcceptor("0.0.0.0", 9000, token="", on_connection=noop)
    assert _is_loopback("127.0.0.1")
    assert _is_loopback("localhost")
    assert _is_loopback("::1")
    assert not _is_loopback("0.0.0.0")


# ---------------------------------------------------------------------------
# 背压分流（Connection 级：write 挂起的传输把队列灌满后的行为）
# ---------------------------------------------------------------------------


class _StallTransport:
    """write 永远挂起的传输（慢消费者模拟器：队列只进不出）。"""

    supports_binary = False

    def __init__(self):
        self.closed = False

    async def open(self):
        pass

    async def read(self):
        await asyncio.sleep(3600)

    async def write(self, msg):
        await asyncio.Event().wait()  # 永不返回

    async def send_binary(self, data, metadata=None):
        raise NotImplementedError

    async def receive_binary(self):
        raise NotImplementedError

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_slow_consumer_disconnect_policy_by_origin():
    """队列满分流：网络来源（WEBSOCKET）主动断连；可信来源（STDIO）等位不断。"""
    from nova_harness.core.rpc.connection import Connection

    # 网络来源：满则断连
    ws_conn = Connection(_StallTransport(), ConnectionOrigin.WEBSOCKET, queue_size=2)
    ws_conn.start_writer()
    for i in range(5):  # 泵取走 1 堵在 write + 队列 2 + 溢出
        ws_conn.send_from_sync({"n": i})
    await asyncio.sleep(0.05)
    assert ws_conn.closed is True

    # 可信来源：满则等位（转 task 阻塞），不断连
    stdio_conn = Connection(_StallTransport(), ConnectionOrigin.STDIO, queue_size=2)
    stdio_conn.start_writer()
    for i in range(5):
        stdio_conn.send_from_sync({"n": i})
    await asyncio.sleep(0.05)
    assert stdio_conn.closed is False
    await stdio_conn.close()
    assert stdio_conn.closed is True


# ---------------------------------------------------------------------------
# token 供给链
# ---------------------------------------------------------------------------


def test_provision_token_explicit_not_persisted(tmp_path):
    token, path = provision_token("explicit-tok", None, tmp_path / "rpc.json")
    assert token == "explicit-tok"
    assert path is None
    assert not (tmp_path / "rpc.json").exists()


def test_provision_token_generates_and_reloads(tmp_path):
    target = tmp_path / "rpc-server.json"
    token1, path1 = provision_token(None, None, target)
    assert path1 == target
    assert target.exists()
    # Windows 无 POSIX 权限位语义（chmod 仅 read-only 标志，stat 回读恒 0o666）
    expected_mode = "0o666" if sys.platform == "win32" else "0o600"
    assert oct(target.stat().st_mode & 0o777) == expected_mode
    # 二次调用读回同一 token（幂等）
    token2, _ = provision_token(None, None, target)
    assert token1 == token2
