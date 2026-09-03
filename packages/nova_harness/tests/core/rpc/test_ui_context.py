"""RoutingUIContext 测试（连接化：寻址 + 首响应胜出 + 能力并集）。

单连接行为（超时/归一化/通知门控）保持与旧 TransportUIContext 逐项一致；
多连接语义（发起方优先、广播首响应、败者撤销）为本文件新增覆盖。
"""

import asyncio

import pytest

from nova_harness.core.rpc.connection import (
    Connection,
    ConnectionOrigin,
    ConnectionRegistry,
    _current_connection,
)
from nova_harness.core.rpc.transport import MemoryTransport
from nova_harness.core.rpc.ui_context import RoutingUIContext


def _make_conn(registry, capabilities=(), *, initialized=True):
    """建一条挂进注册表的测试连接；返回 (conn, client_transport)。"""
    server_t = MemoryTransport()
    client_t = MemoryTransport(server_t)
    conn = Connection(server_t, ConnectionOrigin.MEMORY)
    conn.initialized = initialized
    conn.ui_capabilities = set(capabilities)
    registry.add(conn)
    conn.start_writer()
    return conn, client_t


async def _read_frame(client_t):
    return await asyncio.wait_for(client_t.read(), timeout=1.0)


@pytest.fixture
async def ui_env():
    registry = ConnectionRegistry()
    ui = RoutingUIContext(registry, default_timeout=0.05)
    yield registry, ui
    for conn in registry.all():
        await conn.close()


@pytest.mark.asyncio
async def test_capabilities_union_across_connections(ui_env):
    registry, ui = ui_env
    assert ui.capabilities == set()
    assert not ui.has_capability("confirm")

    conn_a, _ = _make_conn(registry, {"confirm"})
    conn_b, _ = _make_conn(registry, {"input"})
    assert ui.has_capability("confirm")
    assert ui.has_capability("input")

    # 按连接更新：B 改能力不影响 A
    conn_b.ui_capabilities = set()
    assert ui.capabilities == {"confirm"}
    conn_a.ui_capabilities = set()
    assert ui.capabilities == set()


@pytest.mark.asyncio
async def test_request_success(ui_env):
    registry, ui = ui_env
    _, client = _make_conn(registry, {"confirm"})

    task = asyncio.create_task(ui.request("confirm", {"title": "q", "message": "m"}))
    payload = await _read_frame(client)
    assert payload["method"] == "ui/request"
    assert payload["params"]["component"]["componentType"] == "confirm"

    ui.handle_response(registry.all()[0], payload["params"]["id"], {"confirmed": True})
    resp = await task
    assert resp.confirmed is True


@pytest.mark.asyncio
async def test_request_unsupported_returns_cancelled(ui_env):
    registry, ui = ui_env
    _make_conn(registry, {"select"})  # 有能力连接，但不是 confirm
    resp = await ui.request("confirm", {"title": "q", "message": "m"})
    assert resp.cancelled is True


@pytest.mark.asyncio
async def test_request_timeout(ui_env):
    registry, ui = ui_env
    _make_conn(registry, {"confirm"})
    resp = await ui.request("confirm", {"title": "q", "message": "m"})
    assert resp.cancelled is True


@pytest.mark.asyncio
async def test_request_normalize_non_dict_value(ui_env):
    registry, ui = ui_env
    conn, client = _make_conn(registry, {"input"})

    task = asyncio.create_task(ui.request("input", {"title": "x"}))
    payload = await _read_frame(client)
    ui.handle_response(conn, payload["params"]["id"], "hello")
    resp = await task
    assert resp.value == "hello"
    assert resp.cancelled is False


@pytest.mark.asyncio
async def test_notify_forwarded(ui_env):
    registry, ui = ui_env
    _, client = _make_conn(registry, {"notify"})

    ui.notify("notify", {"message": "hello", "type": "info"})
    payload = await _read_frame(client)
    assert payload["method"] == "ui/notify"
    assert payload["params"]["method"] == "notify"


@pytest.mark.asyncio
async def test_notify_skipped_without_capability(ui_env):
    registry, ui = ui_env
    _, client = _make_conn(registry, {"other"})

    ui.notify("notify", {"message": "hello"})
    await asyncio.sleep(0.02)
    assert client._inbox == []


@pytest.mark.asyncio
async def test_resolve_unknown_request_id_is_noop(ui_env):
    registry, ui = ui_env
    conn, _ = _make_conn(registry, {"confirm"})
    # 不应抛出异常
    ui.handle_response(conn, "nonexistent", {"value": 1})


# ---------------------------------------------------------------------------
# 多连接寻址语义
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_origin_connection_preferred(ui_env):
    """发起方优先：来源连接有能力则单发给它，另一连接不收帧。"""
    registry, ui = ui_env
    conn_a, client_a = _make_conn(registry, {"confirm"})
    _, client_b = _make_conn(registry, {"confirm"})

    token = _current_connection.set(conn_a)
    try:
        task = asyncio.create_task(ui.request("confirm", {"title": "q"}))
    finally:
        _current_connection.reset(token)

    payload = await _read_frame(client_a)
    ui.handle_response(conn_a, payload["params"]["id"], {"confirmed": True})
    resp = await task
    assert resp.confirmed is True
    await asyncio.sleep(0.02)
    assert client_b._inbox == []


@pytest.mark.asyncio
async def test_broadcast_first_response_wins_and_cancels_losers(ui_env):
    """无归属广播：全部有能力连接收框，首响应胜出，败者收 ui/cancel。"""
    registry, ui = ui_env
    conn_a, client_a = _make_conn(registry, {"confirm"})
    conn_b, client_b = _make_conn(registry, {"confirm"})

    task = asyncio.create_task(ui.request("confirm", {"title": "q"}))
    frame_a = await _read_frame(client_a)
    frame_b = await _read_frame(client_b)
    assert frame_a["params"]["id"] == frame_b["params"]["id"]

    ui.handle_response(conn_b, frame_b["params"]["id"], {"confirmed": False})
    resp = await task
    assert resp.confirmed is False

    # 败者（A）收到撤销帧
    cancel = await _read_frame(client_a)
    assert cancel["method"] == "ui/cancel"
    assert cancel["params"]["id"] == frame_a["params"]["id"]


@pytest.mark.asyncio
async def test_origin_without_capability_falls_back_to_broadcast(ui_env):
    """发起方无该能力：降级为广播给有能力的连接（弹给有能力的屏）。"""
    registry, ui = ui_env
    conn_a, client_a = _make_conn(registry, {"select"})  # 发起方无 confirm
    _, client_b = _make_conn(registry, {"confirm"})

    token = _current_connection.set(conn_a)
    try:
        task = asyncio.create_task(ui.request("confirm", {"title": "q"}))
    finally:
        _current_connection.reset(token)

    payload = await _read_frame(client_b)
    ui.handle_response(registry.all()[1], payload["params"]["id"], {"confirmed": True})
    assert (await task).confirmed is True
    await asyncio.sleep(0.02)
    assert client_a._inbox == []
