"""RpcServer 通用服务器测试（连接化形态）。

多连接专项（双客户端隔离/事件门/exit_on_close）见 test_connections.py。
"""

import asyncio

import pytest

from nova_harness.core.types.events.agent import ToolExecutionStartEvent
from nova_harness.core.types.ui.context import UIContext
from nova_harness.core.types.ui.primitives import UIResponse
from nova_harness.server.connection import ConnectionOrigin
from nova_harness.server.protocol import MethodRegistry
from nova_harness.server.protocol.methods.state import ServerState
from nova_harness.server.server import RpcServer
from nova_harness.server.transport import MemoryTransport


class FakeSession:
    """模拟 AgentSession 事件订阅。"""

    def __init__(self):
        self._listeners = []

    def subscribe(self, listener):
        self._listeners.append(listener)

        def unsubscribe():
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def emit(self, event):
        for listener in self._listeners:
            listener(event)


class FakeRuntime:
    """模拟 AgentSessionRuntime。"""

    def __init__(self, session):
        self.session = session
        self._rebind = None
        self.disposed = False

    def set_rebind_session(self, callback):
        self._rebind = callback

    async def rebind(self, session):
        if self._rebind is not None:
            await self._rebind(session)

    def dispose(self):
        self.disposed = True


class FakeUIContext(UIContext):
    """记录反向通道调用的 UIContext（连接化接口：handle_response 带连接）。"""

    def __init__(self):
        self.responses = []
        self.closed_connections = []

    @property
    def capabilities(self):
        return set()

    async def request(self, method, params, signal=None):
        return UIResponse()

    def notify(self, method, params):
        pass

    def handle_response(self, conn, request_id, result):
        self.responses.append((conn.id, request_id, result))

    def connection_closed(self, conn):
        self.closed_connections.append(conn.id)


@pytest.fixture
def transport_pair():
    server = MemoryTransport()
    client = MemoryTransport(server)
    return server, client


@pytest.fixture
def echo_methods():
    registry = MethodRegistry()
    registry.register("echo", lambda params: params)
    # initialize 握手桩：成功应答即上线（连接 initialized 旗标的测试钩子）
    registry.register("initialize", lambda params: {"success": True})
    return registry


async def _start(server, server_transport):
    """接入一条 memory 连接并启动服务器；返回 (conn, run_task)。"""
    conn = await server.add_connection(server_transport, origin=ConnectionOrigin.MEMORY)
    run_task = asyncio.create_task(server.run())
    await asyncio.sleep(0)
    return conn, run_task


async def _stop(server, run_task):
    server.shutdown()
    await asyncio.wait_for(run_task, timeout=1.0)


async def _initialize(client_transport):
    await client_transport.write(
        {"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}}
    )
    resp = await asyncio.wait_for(client_transport.read(), timeout=1.0)
    assert resp["id"] == "init"


@pytest.mark.asyncio
async def test_request_response_roundtrip(transport_pair, echo_methods):
    server_transport, client_transport = transport_pair
    ui_context = FakeUIContext()
    state = ServerState()
    server = RpcServer(echo_methods, state, ui=ui_context)
    _, run_task = await _start(server, server_transport)

    await client_transport.write(
        {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"x": 1}}
    )
    resp = await asyncio.wait_for(client_transport.read(), timeout=1.0)

    await _stop(server, run_task)

    assert resp == {"jsonrpc": "2.0", "id": 1, "result": {"x": 1}}
    # server 把 ui 上下文单点接线回 state（原 cli 双接线合一）
    assert state.ui_context is ui_context


@pytest.mark.asyncio
async def test_invalid_jsonrpc_returns_error(transport_pair, echo_methods):
    server_transport, client_transport = transport_pair
    server = RpcServer(echo_methods, ServerState(), ui=FakeUIContext())
    _, run_task = await _start(server, server_transport)

    await client_transport.write({"method": "echo", "id": 2})
    resp = await asyncio.wait_for(client_transport.read(), timeout=1.0)

    await _stop(server, run_task)

    assert resp["id"] == 2
    assert resp["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_ui_response_routed(transport_pair, echo_methods):
    server_transport, client_transport = transport_pair
    ui_context = FakeUIContext()
    server = RpcServer(echo_methods, ServerState(), ui=ui_context)
    conn, run_task = await _start(server, server_transport)

    await client_transport.write(
        {
            "jsonrpc": "2.0",
            "method": "ui/response",
            "params": {"id": "req-1", "result": {"confirmed": True}},
        }
    )
    await asyncio.sleep(0.01)

    await _stop(server, run_task)

    assert ui_context.responses == [(conn.id, "req-1", {"confirmed": True})]


@pytest.mark.asyncio
async def test_system_capabilities_update(transport_pair, echo_methods):
    """能力上报按连接记账（多客户端能力不同互不覆盖）。"""
    server_transport, client_transport = transport_pair
    server = RpcServer(echo_methods, ServerState(), ui=FakeUIContext())
    conn, run_task = await _start(server, server_transport)

    await client_transport.write(
        {
            "jsonrpc": "2.0",
            "method": "system/capabilities",
            "params": {"capabilities": ["confirm"]},
        }
    )
    await asyncio.sleep(0.01)

    await _stop(server, run_task)

    assert "confirm" in conn.ui_capabilities


@pytest.mark.asyncio
async def test_session_event_forwarded(transport_pair, echo_methods):
    server_transport, client_transport = transport_pair
    state = ServerState()

    session = FakeSession()
    runtime = FakeRuntime(session)
    state.set_runtime(runtime)

    server = RpcServer(echo_methods, state, ui=FakeUIContext())
    _, run_task = await _start(server, server_transport)
    # 事件广播带 initialize 门：握手完成后事件才上线
    await _initialize(client_transport)

    session.emit(
        ToolExecutionStartEvent(
            tool_call_id="tc-1", tool_name="bash", args={"cmd": "ls"}
        )
    )
    resp = await asyncio.wait_for(client_transport.read(), timeout=1.0)

    await _stop(server, run_task)

    assert resp["method"] == "agent/event"
    # 内容事件不再直通——归约层消费后产 item 帧（哑管道直通归域通知）
    assert resp["params"]["type"] == "item_started"
    item = resp["params"]["data"]["item"]
    assert item["type"] == "toolCall"
    assert item["tool"] == "bash"
    assert item["id"] == "tc-1"
    assert item["status"] == "running"


@pytest.mark.asyncio
async def test_rebind_session_resubscribed(transport_pair, echo_methods):
    server_transport, client_transport = transport_pair
    state = ServerState()

    session1 = FakeSession()
    runtime = FakeRuntime(session1)
    state.set_runtime(runtime)

    server = RpcServer(echo_methods, state, ui=FakeUIContext())
    _, run_task = await _start(server, server_transport)
    await _initialize(client_transport)

    session2 = FakeSession()
    await runtime.rebind(session2)

    # 旧 session 的事件不应再被转发
    session1.emit(
        ToolExecutionStartEvent(tool_call_id="old", tool_name="bash", args={})
    )
    await asyncio.sleep(0.01)
    assert client_transport._inbox == []

    # 新 session 的事件应被转发（内容事件经归约以 item 帧上线）
    session2.emit(
        ToolExecutionStartEvent(tool_call_id="new", tool_name="edit", args={})
    )
    resp = await asyncio.wait_for(client_transport.read(), timeout=1.0)

    await _stop(server, run_task)

    assert resp["params"]["type"] == "item_started"
    assert resp["params"]["data"]["item"]["tool"] == "edit"
    assert resp["params"]["data"]["item"]["id"] == "new"


@pytest.mark.asyncio
async def test_shutdown_stops_run_loop(transport_pair, echo_methods):
    server_transport, _ = transport_pair
    server = RpcServer(echo_methods, ServerState(), ui=FakeUIContext())
    _, run_task = await _start(server, server_transport)

    await _stop(server, run_task)

    assert server_transport._closed


@pytest.mark.asyncio
async def test_slow_command_does_not_block_abort(transport_pair):
    """长命令并发分派：turn 期间 abort/steer 类命令随时可达。

    修复前：read 循环顺序 await 每条消息，慢命令阻塞后续所有请求；
    修复后：并发分派，abort 在慢命令执行期间即可被处理。
    """
    server_transport, client_transport = transport_pair

    registry = MethodRegistry()
    slow_started = asyncio.Event()

    async def slow(params):
        slow_started.set()
        await asyncio.sleep(0.3)  # 模拟 turn 中的长任务
        return {"slow": True}

    async def abort(params):
        return {"aborted": True}

    registry.register("slow", slow)
    registry.register("abort", abort)

    server = RpcServer(registry, ServerState(), ui=FakeUIContext())
    _, run_task = await _start(server, server_transport)

    # 先发慢命令（不等响应），确认其已开始执行，再发 abort
    await client_transport.write(
        {"jsonrpc": "2.0", "id": 1, "method": "slow", "params": {}}
    )
    await asyncio.wait_for(slow_started.wait(), timeout=1.0)
    await client_transport.write(
        {"jsonrpc": "2.0", "id": 2, "method": "abort", "params": {}}
    )

    # abort 必须先于 slow 返回（顺序处理时它要等 0.3s 之后）
    resp_abort = await asyncio.wait_for(client_transport.read(), timeout=1.0)
    assert resp_abort == {"jsonrpc": "2.0", "id": 2, "result": {"aborted": True}}

    resp_slow = await asyncio.wait_for(client_transport.read(), timeout=1.0)
    assert resp_slow == {"jsonrpc": "2.0", "id": 1, "result": {"slow": True}}

    await _stop(server, run_task)


@pytest.mark.asyncio
async def test_cancel_request_end_to_end(transport_pair):
    """cancelRequest 端到端：在飞长调用被取消并收到 -32800 应答；
    cancelRequest 自身应答 cancelled:true；已完成调用的取消幂等；
    request_tasks 映射清理干净。"""
    from nova_harness.server.protocol.methods import system as system_methods

    server_transport, client_transport = transport_pair
    state = ServerState()

    registry = MethodRegistry()
    slow_started = asyncio.Event()

    async def slow(params):
        slow_started.set()
        await asyncio.sleep(300)
        return {"slow": True}

    registry.register("slow", slow)
    registry.register("fast", lambda params: {"fast": True})
    system_methods.register(registry, state)  # cancelRequest 真实注册

    server = RpcServer(registry, state, ui=FakeUIContext())
    conn, run_task = await _start(server, server_transport)

    # 1. 在飞长调用被取消
    await client_transport.write(
        {"jsonrpc": "2.0", "id": 1, "method": "slow", "params": {}}
    )
    await asyncio.wait_for(slow_started.wait(), timeout=1.0)
    await client_transport.write(
        {"jsonrpc": "2.0", "id": 2, "method": "cancelRequest", "params": {"id": 1}}
    )

    # 两个应答顺序不保证（cancel 后 task 重调度），按 id 收取
    resps = {}
    for _ in range(2):
        resp = await asyncio.wait_for(client_transport.read(), timeout=1.0)
        resps[resp["id"]] = resp

    assert resps[2]["result"] == {"success": True, "cancelled": True}
    assert resps[1]["error"]["code"] == -32800
    assert conn.request_tasks == {}

    # 2. 已完成调用的取消幂等（cancelled: false，非错误）
    await client_transport.write(
        {"jsonrpc": "2.0", "id": 3, "method": "fast", "params": {}}
    )
    resp_fast = await asyncio.wait_for(client_transport.read(), timeout=1.0)
    assert resp_fast["result"] == {"fast": True}
    assert conn.request_tasks == {}  # 正常完成后映射即清理

    await client_transport.write(
        {"jsonrpc": "2.0", "id": 4, "method": "cancelRequest", "params": {"id": 3}}
    )
    resp_late = await asyncio.wait_for(client_transport.read(), timeout=1.0)
    assert resp_late["result"] == {"success": True, "cancelled": False}

    await _stop(server, run_task)
