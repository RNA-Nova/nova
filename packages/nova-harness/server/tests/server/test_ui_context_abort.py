"""RoutingUIContext 断线 pending 收尾测试。

连接关闭（``connection_closed``）时，该连接独家持有的在飞 ui/request
立即按 cancelled 解决，不再挂到默认超时；广播件摘除该地址，地址集空
才收尾。超时/abort 撤销/任务取消语义与旧 TransportUIContext 逐项一致。
"""

import asyncio
import time

import pytest
from nova_ai.signal import AbortController

from nova_harness.server.connection import (
    Connection,
    ConnectionOrigin,
    ConnectionRegistry,
)
from nova_harness.server.transport import MemoryTransport
from nova_harness.server.ui_context import RoutingUIContext


def _make_conn(registry, capabilities=()):
    server_t = MemoryTransport()
    client_t = MemoryTransport(server_t)
    conn = Connection(server_t, ConnectionOrigin.MEMORY)
    conn.initialized = True
    conn.ui_capabilities = set(capabilities)
    registry.add(conn)
    conn.start_writer()
    return conn, client_t


@pytest.fixture
async def ui_env():
    registry = ConnectionRegistry()
    ui = RoutingUIContext(registry, default_timeout=0.05)
    yield registry, ui
    for conn in registry.all():
        await conn.close()


@pytest.mark.asyncio
async def test_connection_close_resolves_inflight_as_cancelled(ui_env):
    registry, ui = ui_env
    conn, _ = _make_conn(registry, {"select"})

    task = asyncio.create_task(ui.request("select", {"title": "t", "options": ["a"]}))
    await asyncio.sleep(0.01)  # 让 request 发出并挂起

    ui.connection_closed(conn)
    resp = await asyncio.wait_for(task, timeout=1.0)
    assert resp.cancelled is True
    assert ui._pending == {}


@pytest.mark.asyncio
async def test_connection_closed_is_safe_when_empty(ui_env):
    registry, ui = ui_env
    conn, _ = _make_conn(registry)
    ui.connection_closed(conn)  # 空 pending 不报错
    ui.connection_closed(conn)  # 重复调用不报错


@pytest.mark.asyncio
async def test_broadcast_survives_single_connection_loss(ui_env):
    """广播件：一个连接死亡只摘地址，另一连接应答仍正常完成。"""
    registry, ui = ui_env
    conn_a, _ = _make_conn(registry, {"select"})
    conn_b, client_b = _make_conn(registry, {"select"})

    task = asyncio.create_task(ui.request("select", {"title": "t", "options": ["a"]}))
    frame_b = await asyncio.wait_for(client_b.read(), timeout=1.0)

    ui.connection_closed(conn_a)  # A 死了，B 还在
    assert not task.done()

    ui.handle_response(conn_b, frame_b["params"]["id"], {"value": "a"})
    resp = await asyncio.wait_for(task, timeout=1.0)
    assert resp.value == "a"


@pytest.mark.asyncio
async def test_timeout_ms_overrides_default_timeout(ui_env):
    """params.timeout_ms → 按毫秒级超时收尾（pi 对话框 timeout 语义）。"""
    registry, ui = ui_env
    _make_conn(registry, {"select"})

    start = time.monotonic()
    resp = await ui.request("select", {"title": "t", "timeout_ms": 50})
    elapsed = time.monotonic() - start
    assert resp.cancelled is True
    assert elapsed < 1.0  # 50ms 级超时（无全局默认——只 per-request 业务语义）


@pytest.mark.asyncio
async def test_scope_arbitration_sends_ui_cancel_and_resolves(ui_env):
    """仲裁清扫（agent_end 挂接点的本体）：按作用域批量终结——协程按
    cancelled 解决 + 按台账 addressed 集发 ui/cancel 撤框。"""
    registry, ui = ui_env
    _, client = _make_conn(registry, {"select"})

    task = asyncio.create_task(ui.request("select", {"title": "t"}, scope="run:r1"))
    frame = await asyncio.wait_for(client.read(), timeout=1.0)
    assert frame["method"] == "ui/request"

    ui.cancel_scope("run:r1")  # run 终结 → 仲裁
    resp = await asyncio.wait_for(task, timeout=1.0)
    assert resp.cancelled is True

    # 帧序列：ui/request 之后应有 ui/cancel
    cancel = await asyncio.wait_for(client.read(), timeout=1.0)
    assert cancel["method"] == "ui/cancel"
    assert cancel["params"]["id"] == frame["params"]["id"]


@pytest.mark.asyncio
async def test_scope_arbitration_spares_other_scopes(ui_env):
    """仲裁只按精确归属清——别的 run/global 的请求不受影响。"""
    registry, ui = ui_env
    _, client = _make_conn(registry, {"select"})

    task = asyncio.create_task(ui.request("select", {"title": "t"}, scope="run:other"))
    await asyncio.wait_for(client.read(), timeout=1.0)

    ui.cancel_scope("run:r1")  # 清别的 run
    await asyncio.sleep(0.02)
    assert not task.done()  # 不受影响

    ui.cancel_scope("run:other")
    resp = await asyncio.wait_for(task, timeout=1.0)
    assert resp.cancelled is True


@pytest.mark.asyncio
async def test_task_cancellation_sends_ui_cancel_and_propagates(ui_env):
    """宿主 task 被 cancel（cancelRequest 路径）：发 ui/cancel 撤销帧 +
    CancelledError 继续传播（不吞取消语义，前端不留僵尸框）。"""
    registry, ui = ui_env
    _, client = _make_conn(registry, {"select"})

    task = asyncio.create_task(ui.request("select", {"title": "t"}))
    frame = await asyncio.wait_for(client.read(), timeout=1.0)
    assert frame["method"] == "ui/request"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)

    cancel = await asyncio.wait_for(client.read(), timeout=1.0)
    assert cancel["method"] == "ui/cancel"
    assert ui._pending == {}
