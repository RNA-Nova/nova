"""ScopedUIContext 测试：abort 竞速注入点。

验证：作用域 signal 在每次请求时现取并织入；abort → cancelled + ui/cancel；
显式 signal 覆盖作用域 signal；idle（signal None）不竞速。

base 层为连接化后的 ``RoutingUIContext``（单连接注册表 + 内存传输）。
"""

import asyncio

import pytest
from nova_ai.signal import AbortController
from nova_harness.core.rpc.connection import (
    Connection,
    ConnectionOrigin,
    ConnectionRegistry,
)
from nova_harness.core.rpc.transport import MemoryTransport
from nova_harness.core.rpc.ui_context import RoutingUIContext
from nova_harness.core.types.ui.scoped import ScopedUIContext


def _make_base(capabilities=frozenset({"select"}), timeout=300.0):
    """构造 (RoutingUIContext, conn, client_transport) 测试三元组。"""
    server_t = MemoryTransport()
    client_t = MemoryTransport(server_t)
    registry = ConnectionRegistry()
    conn = Connection(server_t, ConnectionOrigin.MEMORY)
    conn.initialized = True
    conn.ui_capabilities = set(capabilities)
    registry.add(conn)
    conn.start_writer()
    return RoutingUIContext(registry, default_timeout=timeout), conn, client_t


@pytest.mark.asyncio
async def test_scoped_signal_races_abort():
    base, conn, client = _make_base()
    controller = AbortController()
    scoped = ScopedUIContext(base, lambda: controller.signal)

    task = asyncio.create_task(scoped.request("select", {"title": "t"}))
    frame = await asyncio.wait_for(client.read(), timeout=1.0)
    assert frame["method"] == "ui/request"

    controller.abort()
    resp = await asyncio.wait_for(task, timeout=1.0)
    assert resp.cancelled is True

    cancel = await asyncio.wait_for(client.read(), timeout=1.0)
    assert cancel["method"] == "ui/cancel"
    await conn.close()


@pytest.mark.asyncio
async def test_scoped_no_signal_when_idle():
    """signal_getter 返回 None（idle）：不竞速，300s 兜底的短路径验证。"""
    base, conn, _ = _make_base(timeout=0.05)
    scoped = ScopedUIContext(base, lambda: None)

    resp = await scoped.request("select", {"title": "t"})
    assert resp.cancelled is True  # 超时兜底（无人应答）
    await conn.close()


@pytest.mark.asyncio
async def test_explicit_signal_overrides_scope():
    """调用方显式传 signal 时优先于作用域 signal。"""
    base, conn, _ = _make_base()
    scope_controller = AbortController()
    explicit_controller = AbortController()
    scoped = ScopedUIContext(base, lambda: scope_controller.signal)

    task = asyncio.create_task(
        scoped.request("select", {"title": "t"}, explicit_controller.signal)
    )
    await asyncio.sleep(0.01)  # 让 request 发出并挂起

    # 作用域 abort 不应影响显式 signal 的请求
    scope_controller.abort()
    await asyncio.sleep(0.02)
    assert not task.done()

    explicit_controller.abort()
    resp = await asyncio.wait_for(task, timeout=1.0)
    assert resp.cancelled is True
    await conn.close()


@pytest.mark.asyncio
async def test_scoped_lock_serializes_parallel_requests():
    """弹窗串行锁：并行请求经同一把锁排队（并行工具调用的 UI 纪律）。"""
    base, conn, _ = _make_base(timeout=0.05)
    lock = asyncio.Lock()
    scoped = ScopedUIContext(base, lambda: None, lock)

    # 在 base 层记录开闭区间，断言无交叠
    spans: list[tuple[str, str]] = []
    orig_request = base.request

    async def _spy(method, params, signal=None):
        spans.append((params["title"], "open"))
        try:
            return await orig_request(method, params, signal)
        finally:
            spans.append((params["title"], "close"))

    base.request = _spy  # type: ignore[method-assign]

    async def call(tag: str):
        return await scoped.request("select", {"title": tag})

    t1 = asyncio.create_task(call("a"))
    await asyncio.sleep(0)
    t2 = asyncio.create_task(call("b"))
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2.0)

    # 严格串行：a 的完整区间结束后 b 才开始
    assert spans == [("a", "open"), ("a", "close"), ("b", "open"), ("b", "close")]
    await conn.close()


@pytest.mark.asyncio
async def test_scoped_lock_bypassed_when_already_aborted():
    """已 abort 的请求不排队：锁被占时也直放（路由层立即按 cancelled 解决）。"""
    base, conn, _ = _make_base(timeout=5.0)
    lock = asyncio.Lock()
    scoped = ScopedUIContext(base, lambda: None, lock)

    await lock.acquire()  # 模拟另一个工具的对话框正在进行
    controller = AbortController()
    controller.abort()
    try:
        resp = await asyncio.wait_for(
            scoped.request("select", {"title": "t"}, controller.signal), timeout=1.0
        )
        assert resp.cancelled is True
    finally:
        lock.release()
        await conn.close()
