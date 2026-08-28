"""ScopedUIContext 测试：作用域归属织入注入点。

验证：作用域在每次请求时现取并织入（pending 台账携带归属）；显式 scope
覆盖作用域 getter；idle（getter 返回 None）落路由层默认 global；并行
请求经串行锁排队（弹窗纪律）。

base 层为连接化后的 ``RoutingUIContext``（单连接注册表 + 内存传输）。
"""

import asyncio

import pytest

from nova_harness.core.types.ui.scoped import ScopedUIContext
from nova_harness.server.connection import (
    Connection,
    ConnectionOrigin,
    ConnectionRegistry,
)
from nova_harness.server.transport import MemoryTransport
from nova_harness.server.ui_context import RoutingUIContext


def _make_base(capabilities=frozenset({"select"})):
    """构造 (RoutingUIContext, conn, client_transport) 测试三元组。"""
    server_t = MemoryTransport()
    client_t = MemoryTransport(server_t)
    registry = ConnectionRegistry()
    conn = Connection(server_t, ConnectionOrigin.MEMORY)
    conn.initialized = True
    conn.ui_capabilities = set(capabilities)
    registry.add(conn)
    conn.start_writer()
    return RoutingUIContext(registry), conn, client_t


@pytest.mark.asyncio
async def test_scoped_injects_current_scope():
    """getter 的当前值织入请求：pending 台账携带该归属（仲裁的清扫键）。"""
    base, conn, _ = _make_base()
    scoped = ScopedUIContext(base, lambda: "run:abc123")

    task = asyncio.create_task(scoped.request("select", {"title": "t"}))
    await asyncio.sleep(0.01)  # 让 request 发出并挂起

    pending = list(base._pending.values())
    assert len(pending) == 1
    assert pending[0].scope == "run:abc123"

    base.cancel_scope("run:abc123")  # 仲裁清扫
    resp = await asyncio.wait_for(task, timeout=1.0)
    assert resp.cancelled is True
    await conn.close()


@pytest.mark.asyncio
async def test_explicit_scope_overrides_getter():
    """调用方显式传 scope 时优先于作用域 getter。"""
    base, conn, _ = _make_base()
    scoped = ScopedUIContext(base, lambda: "run:from-getter")

    task = asyncio.create_task(
        scoped.request("select", {"title": "t"}, scope="session:s1")
    )
    await asyncio.sleep(0.01)

    pending = list(base._pending.values())
    assert pending[0].scope == "session:s1"

    base.cancel_scope("session:s1")
    resp = await asyncio.wait_for(task, timeout=1.0)
    assert resp.cancelled is True
    await conn.close()


@pytest.mark.asyncio
async def test_idle_scope_falls_back_to_global():
    """getter 返回 None（idle/无 run）：路由层按 global 归属——不随 run
    终结被误清扫。"""
    base, conn, _ = _make_base()
    scoped = ScopedUIContext(base, lambda: None)

    task = asyncio.create_task(scoped.request("select", {"title": "t"}))
    await asyncio.sleep(0.01)

    pending = list(base._pending.values())
    assert pending[0].scope == "global"

    # 仲裁清扫某个 run：global 归属的请求不受影响
    base.cancel_scope("run:whatever")
    assert not task.done()

    base.cancel_scope("global")
    resp = await asyncio.wait_for(task, timeout=1.0)
    assert resp.cancelled is True
    await conn.close()


@pytest.mark.asyncio
async def test_scoped_lock_serializes_parallel_requests():
    """弹窗串行锁：并行请求经同一把锁排队（并行工具调用的 UI 纪律）。"""
    base, conn, _ = _make_base()
    lock = asyncio.Lock()
    scoped = ScopedUIContext(base, lambda: "run:r1", lock)

    # 在 base 层记录开闭区间，断言无交叠
    spans: list[tuple[str, str]] = []
    orig_request = base.request

    async def _spy(method, params, scope=None):
        spans.append((params["title"], "open"))
        try:
            return await orig_request(method, params, scope=scope)
        finally:
            spans.append((params["title"], "close"))

    base.request = _spy  # type: ignore[method-assign]

    async def call(tag: str):
        return await scoped.request("select", {"title": tag})

    t1 = asyncio.create_task(call("a"))
    await asyncio.sleep(0)
    t2 = asyncio.create_task(call("b"))
    # 两个请求都挂起（无人应答）——仲裁清扫让它们收尾；t2 排在锁后，
    # 锁释放后才进路由层台账（此刻才被仲裁看见），故清扫两次
    await asyncio.sleep(0.05)
    base.cancel_scope("run:r1")
    await asyncio.sleep(0.05)
    base.cancel_scope("run:r1")
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2.0)

    # 严格串行：a 的完整区间结束后 b 才开始
    assert spans == [("a", "open"), ("a", "close"), ("b", "open"), ("b", "close")]
    await conn.close()
