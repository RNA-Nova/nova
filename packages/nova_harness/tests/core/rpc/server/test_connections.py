"""多连接专项测试（连接化重构的核心验收）。

覆盖：双客户端 id 命名空间隔离、事件 initialize 门、cancelRequest
按连接隔离、exit_on_close 关停语义、事件广播扇出。
"""

import asyncio

import pytest

from nova_harness.core.rpc.connection import ConnectionOrigin
from nova_harness.core.rpc.protocol import MethodRegistry
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.server import RpcServer
from nova_harness.core.rpc.transport import MemoryTransport
from nova_harness.core.types.events.agent import ToolExecutionStartEvent


class FakeSession:
    def __init__(self):
        self._listeners = []

    def subscribe(self, listener):
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def emit(self, event):
        for listener in list(self._listeners):
            listener(event)


class FakeRuntime:
    def __init__(self, session):
        self.session = session

    def set_rebind_session(self, callback):
        pass


@pytest.fixture
def methods():
    registry = MethodRegistry()
    registry.register("echo", lambda params: params)
    registry.register("initialize", lambda params: {"ok": True})
    return registry


async def _connect(server, *, exit_on_close=False):
    """接入一条 memory 连接；返回 (conn, client_transport)。"""
    server_t = MemoryTransport()
    client_t = MemoryTransport(server_t)
    conn = await server.add_connection(
        server_t, origin=ConnectionOrigin.MEMORY, exit_on_close=exit_on_close
    )
    return conn, client_t


async def _initialize(client_t, req_id="init"):
    await client_t.write(
        {"jsonrpc": "2.0", "id": req_id, "method": "initialize", "params": {}}
    )
    resp = await asyncio.wait_for(client_t.read(), timeout=1.0)
    assert resp["id"] == req_id


@pytest.fixture
async def running_server(methods):
    server = RpcServer(methods, ServerState())
    run_task = asyncio.create_task(server.run())
    yield server
    server.shutdown()
    await asyncio.wait_for(run_task, timeout=1.0)


@pytest.mark.asyncio
async def test_two_clients_same_request_id_isolated(running_server):
    """两个客户端用相同 id：复合键 (connId, reqId) 隔离，各收各的应答。"""
    _, client_a = await _connect(running_server)
    _, client_b = await _connect(running_server)

    await client_a.write(
        {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"who": "a"}}
    )
    await client_b.write(
        {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"who": "b"}}
    )

    resp_a = await asyncio.wait_for(client_a.read(), timeout=1.0)
    resp_b = await asyncio.wait_for(client_b.read(), timeout=1.0)
    assert resp_a["result"] == {"who": "a"}
    assert resp_b["result"] == {"who": "b"}


@pytest.mark.asyncio
async def test_events_gated_by_initialize(running_server, methods):
    """initialize 门：握手前的连接不收事件；握手后即入扇出集。"""
    state = running_server._state
    session = FakeSession()
    state.set_runtime(FakeRuntime(session))

    _, client_a = await _connect(running_server)
    _, client_b = await _connect(running_server)
    await _initialize(client_a)  # 只有 A 握手

    event = ToolExecutionStartEvent(tool_call_id="t1", tool_name="bash", args={})
    session.emit(event)
    resp_a = await asyncio.wait_for(client_a.read(), timeout=1.0)
    assert resp_a["method"] == "agent/event"
    await asyncio.sleep(0.01)
    assert client_b._inbox == []  # B 未握手，门外

    await _initialize(client_b)  # B 握手后进扇出集
    session.emit(event)
    resp_b = await asyncio.wait_for(client_b.read(), timeout=1.0)
    assert resp_b["method"] == "agent/event"
    resp_a2 = await asyncio.wait_for(client_a.read(), timeout=1.0)
    assert resp_a2["method"] == "agent/event"


@pytest.mark.asyncio
async def test_cancel_request_scoped_to_own_connection(running_server, methods):
    """cancelRequest 只作用本连接：B 取消 A 的在飞调用是幂等空操作。"""
    from nova_harness.core.rpc.protocol.methods import system as system_methods

    system_methods.register(methods, running_server._state)

    slow_started = asyncio.Event()

    async def slow(params):
        slow_started.set()
        await asyncio.sleep(300)
        return {"slow": True}

    methods.register("slow", slow)

    _, client_a = await _connect(running_server)
    _, client_b = await _connect(running_server)

    await client_a.write({"jsonrpc": "2.0", "id": 1, "method": "slow", "params": {}})
    await asyncio.wait_for(slow_started.wait(), timeout=1.0)

    # B 试图取消 A 的调用（同 id）——查不到本连接的该 id，幂等 false
    await client_b.write(
        {"jsonrpc": "2.0", "id": 9, "method": "cancelRequest", "params": {"id": 1}}
    )
    resp_b = await asyncio.wait_for(client_b.read(), timeout=1.0)
    assert resp_b["result"] == {"ok": True, "cancelled": False}

    # A 取消自己的调用——生效
    await client_a.write(
        {"jsonrpc": "2.0", "id": 2, "method": "cancelRequest", "params": {"id": 1}}
    )
    resps = {}
    for _ in range(2):
        resp = await asyncio.wait_for(client_a.read(), timeout=1.0)
        resps[resp["id"]] = resp
    assert resps[2]["result"] == {"ok": True, "cancelled": True}
    assert resps[1]["error"]["code"] == -32800


@pytest.mark.asyncio
async def test_events_carry_seq_anchor(running_server, methods):
    """信封锚点：seq 单调递增 + ts + sessionId（syncSession 对账的依据）。"""
    state = running_server._state
    session = FakeSession()
    state.set_runtime(FakeRuntime(session))

    _, client_a = await _connect(running_server)
    await _initialize(client_a)

    for i in range(2):
        session.emit(
            ToolExecutionStartEvent(tool_call_id=f"t{i}", tool_name="bash", args={})
        )
    first = await asyncio.wait_for(client_a.read(), timeout=1.0)
    second = await asyncio.wait_for(client_a.read(), timeout=1.0)

    p1, p2 = first["params"], second["params"]
    assert p1["seq"] >= 1 and p2["seq"] == p1["seq"] + 1  # 单调递增
    assert isinstance(p1["ts"], int) and p1["ts"] > 0
    assert "sessionId" in p1  # FakeRuntime 无 session_id → None 也在键集里
    assert p1["sessionId"] is None


@pytest.mark.asyncio
async def test_exit_on_close_shuts_down_server(methods):
    """stdio 单客户端语义：连接关闭（exit_on_close）→ 服务器关停。"""
    server = RpcServer(methods, ServerState())
    run_task = asyncio.create_task(server.run())
    await asyncio.sleep(0)

    conn, _ = await _connect(server, exit_on_close=True)
    await conn.transport.close()  # 模拟 stdio EOF/父进程退出

    await asyncio.wait_for(run_task, timeout=1.0)


@pytest.mark.asyncio
async def test_close_cancels_inflight_handlers(running_server):
    """连接关闭即取消本连接在飞 handler（长任务随连接终止）。"""
    from nova_harness.core.rpc.protocol.methods import system as system_methods

    methods_reg = running_server._methods
    cancelled = asyncio.Event()

    async def slow(params):
        try:
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    methods_reg.register("slow", slow)
    system_methods.register(methods_reg, running_server._state)

    conn, client_a = await _connect(running_server)
    await client_a.write({"jsonrpc": "2.0", "id": 1, "method": "slow", "params": {}})
    await asyncio.sleep(0.05)  # 让 slow 起飞

    await conn.close()  # 连接死亡
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    assert conn.request_tasks == {}


@pytest.mark.asyncio
async def test_inflight_overload_returns_32004(methods):
    """入站背压：在飞 handler 达上限——请求回 -32004，通知静默丢弃。"""
    import time as _time

    blocker = asyncio.Event()

    async def slow(params):
        await blocker.wait()
        return {"slow": True}

    methods.register("slow", slow)
    server = RpcServer(methods, ServerState())
    run_task = asyncio.create_task(server.run())
    await asyncio.sleep(0)

    server_t = MemoryTransport()
    client_t = MemoryTransport(server_t)
    await server.add_connection(
        server_t, origin=ConnectionOrigin.MEMORY, max_inflight=2
    )

    # 占满在飞名额（2 个 slow）
    await client_t.write({"jsonrpc": "2.0", "id": 1, "method": "slow", "params": {}})
    await client_t.write({"jsonrpc": "2.0", "id": 2, "method": "slow", "params": {}})
    await asyncio.sleep(0.05)

    # 超限请求：立即 -32004
    await client_t.write({"jsonrpc": "2.0", "id": 3, "method": "echo", "params": {}})
    resp = await asyncio.wait_for(client_t.read(), timeout=1.0)
    assert resp["id"] == 3 and resp["error"]["code"] == -32004

    # 超限通知：静默丢弃（无帧、无异常）
    await client_t.write({"jsonrpc": "2.0", "method": "echo", "params": {}})
    await asyncio.sleep(0.05)
    assert client_t._inbox == []

    # 放行在飞任务：一切恢复正常
    blocker.set()
    resps = {}
    for _ in range(2):
        r = await asyncio.wait_for(client_t.read(), timeout=1.0)
        resps[r["id"]] = r
    assert resps[1]["result"] == {"slow": True}
    assert resps[2]["result"] == {"slow": True}

    server.shutdown()
    await asyncio.wait_for(run_task, timeout=1.0)


@pytest.mark.asyncio
async def test_loop_lag_probe_fires_on_blocked_loop(methods, capfd):
    """滞后探针：同步阻塞循环（time.sleep）超阈值即落 stderr 日志。"""
    import time as _time

    async def blocking(params):
        _time.sleep(0.12)  # 故意在循环线程上同步阻塞（卡顿发生器）
        return {"ok": True}

    methods.register("blocking", blocking)
    server = RpcServer(methods, ServerState(), lag_interval=0.02, lag_threshold_ms=20)
    run_task = asyncio.create_task(server.run())
    await asyncio.sleep(0)

    _, client_t = await _connect(server)
    await client_t.write(
        {"jsonrpc": "2.0", "id": 1, "method": "blocking", "params": {}}
    )
    resp = await asyncio.wait_for(client_t.read(), timeout=1.0)
    assert resp["result"] == {"ok": True}

    await asyncio.sleep(0.08)  # 探针再醒一拍（覆盖阻塞后的漂移观测）
    server.shutdown()
    await asyncio.wait_for(run_task, timeout=1.0)

    err = capfd.readouterr().err
    assert "event-loop lag" in err
