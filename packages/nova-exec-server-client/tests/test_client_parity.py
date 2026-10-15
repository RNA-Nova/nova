"""client.py 行为族对位 Rust（codex 39f4277087）的分歧修复测试：

- A9：常规请求无隐含超时（对位 rpc.rs RpcCallTimeout::None /
  rpc_client_call_has_no_implicit_deadline）；initialize 握手专用
  INITIALIZE_TIMEOUT（10s，对位 INITIALIZE_TIMEOUT）；调用方显式
  request_timeout 保留
- A8：environment/status fail-fast（对位 client.rs:832-840
  rpc_client_without_recovery——不触发/等待恢复）+ 专用
  ENVIRONMENT_STATUS_TIMEOUT（10s）
- A12：protocolVersion 空串放行（对位 client.rs:741
  `if !response.protocol_version.is_empty()`——serde 缺省即空串）
- A14：process/start 在飞门控（对位 client_recovery.rs begin/finish/
  wait_for_process_starts）+ 传输类失败/取消的后台 terminate 清理
  （对位 client.rs cleanup_process_start）
- A15：裁决回调入站校验（processId/host）/ reason 回夹 / 回调超时
  （对位 client_recovery.rs:656-663 / :738-759 / timeout(controller.timeout)）
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fake_transport import FakeTransport

from nova_exec_server_client import (
    ConnectionError,
    ExecutorClient,
    NetworkPolicyDecision,
    ProtocolError,
    ReconnectStrategy,
    TimeoutError,
)
from nova_exec_server_client import client as client_module

FAKE_SERVER = str(Path(__file__).parent / "fake_executor_server.py")

INIT_OK = {"sessionId": "fake-session", "protocolVersion": "1.13"}

FAST = ReconnectStrategy(interval=0.01, timeout=2.0)


# ---------------------------------------------------------------------------
# A9：去 30s 隐含超时 + initialize 专用 10s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_request_timeout_is_none():
    """默认装配不再给底层传输塞 30s：常规请求无隐含超时
    （对位 RpcCallTimeout::None——wait_for(timeout=None) 无限等待）"""
    ws_client = ExecutorClient("ws://localhost:8080")
    assert ws_client.transport.request_timeout is None

    stdio_client = ExecutorClient.from_stdio()
    assert stdio_client.transport.request_timeout is None


@pytest.mark.asyncio
async def test_regular_request_survives_slow_server_by_default():
    """慢应答不被本地隐含判死：默认（无超时）客户端等得到慢响应"""
    client = ExecutorClient.from_stdio(program=sys.executable, args=[FAKE_SERVER])
    await client.connect()
    try:
        result = await client.transport.send_request("sleep", {"ms": 300})
        assert result == {"slept": True}
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_explicit_request_timeout_still_applies():
    """调用方显式 request_timeout 保留：慢请求按显式时限本地判死"""
    client = ExecutorClient.from_stdio(
        program=sys.executable, args=[FAKE_SERVER], request_timeout=0.2
    )
    await client.connect()
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            await client.transport.send_request("sleep", {"ms": 1500})
    finally:
        await client.disconnect()


class HangingInitializeTransport(FakeTransport):
    """initialize 永不应答的假传输（握手专用时限测试用）"""

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False
    ):
        if method == "initialize":
            await asyncio.sleep(60)
        return await super().send_request(method, params, channel=channel)


@pytest.mark.asyncio
async def test_initialize_handshake_has_dedicated_timeout(monkeypatch):
    """initialize 握手受 INITIALIZE_TIMEOUT 约束（对位 Rust
    `timeout(initialize_timeout, ...)`）——卡死传输上的握手不无限干等"""
    monkeypatch.setattr(client_module, "INITIALIZE_TIMEOUT", 0.05)
    client = ExecutorClient(transport=HangingInitializeTransport({}))
    with pytest.raises(TimeoutError, match="initialize handshake timed out"):
        await client.connect()
    await client.disconnect()


@pytest.mark.asyncio
async def test_initialize_timeout_overridable_per_client():
    """构造器 initialize_timeout 覆盖包级默认（环境声明的握手预算注入点，
    environments 侧经 from_environment 传入）"""
    client = ExecutorClient(
        transport=HangingInitializeTransport({}), initialize_timeout=0.05
    )
    with pytest.raises(TimeoutError, match="after 0.05s"):
        await client.connect()
    await client.disconnect()


def test_initialize_timeout_must_be_positive():
    with pytest.raises(ValueError, match="initialize_timeout"):
        ExecutorClient(transport=FakeTransport({}), initialize_timeout=0)


@pytest.mark.asyncio
async def test_from_environment_threads_initialize_timeout():
    """from_environment 透传 initialize_timeout 到构造点（environments
    侧 EnvironmentManager.get_client 的注入路径）"""
    from nova_protocol import ResolvedEnvironment

    environment = ResolvedEnvironment(
        id="ws-1",
        kind="ws",
        url="ws://localhost:8080",
        initialize_timeout_sec=3.5,
    )
    client = ExecutorClient.from_environment(environment)
    assert client._initialize_timeout == 3.5
    # None（环境未声明）→ 包级默认
    client2 = ExecutorClient.from_environment(
        ResolvedEnvironment(id="ws-2", kind="ws", url="ws://localhost:8080")
    )
    assert client2._initialize_timeout == client_module.INITIALIZE_TIMEOUT


# ---------------------------------------------------------------------------
# A12：protocolVersion 空串放行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_protocol_version_allowed():
    """线上显式空串 protocolVersion 放行（serde 缺省即空串——基准语义）"""
    transport = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": ""}}
    )
    client = ExecutorClient(transport=transport)
    await client.connect()
    assert client.session_id == "fake-session"
    await client.disconnect()


@pytest.mark.asyncio
async def test_missing_protocol_version_allowed():
    """旧服务端缺省版本字段（None）放行（既有语义回归钉住）"""
    transport = FakeTransport({"initialize": {"sessionId": "fake-session"}})
    client = ExecutorClient(transport=transport)
    await client.connect()
    await client.disconnect()


@pytest.mark.asyncio
async def test_incompatible_major_version_rejected():
    """major 不等仍拒绝（检查未被空串放行吞掉）"""
    transport = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "2.0"}}
    )
    client = ExecutorClient(transport=transport)
    with pytest.raises(ProtocolError, match="协议版本不兼容"):
        await client.connect()
    await client.disconnect()


# ---------------------------------------------------------------------------
# A8：environment/status fail-fast + 专用 10s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_environment_status_connected_happy_path():
    """已连接：status 走当前连接正常应答"""
    transport = FakeTransport(
        {
            "initialize": INIT_OK,
            "environment/status": {"status": "ready"},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()
    status = await client.environment_status()
    assert status.status == "ready"
    await client.disconnect()


@pytest.mark.asyncio
async def test_environment_status_not_connected_fails_fast():
    """未连接即败（不等待、不触发连接动作）"""
    client = ExecutorClient(transport=FakeTransport({}))
    with pytest.raises(ConnectionError, match="not connected"):
        await client.environment_status()


class HangingConnectTransport(FakeTransport):
    """connect 永不返回的假传输（恢复期间状态保持 recovering 用）"""

    async def connect(self) -> None:
        await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_environment_status_fail_fast_while_recovering():
    """恢复期间即取即败（对位 rpc_client_without_recovery）——不在恢复窗
    里干等（旧行为最坏要等 25s 恢复窗 + 30s 请求超时）"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = (
            FakeTransport({"initialize": INIT_OK})
            if not sink
            else HangingConnectTransport({"initialize": INIT_OK})
        )
        sink.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory, reconnect=FAST)
    await client.connect()
    sink[0].drop("test drop")
    for _ in range(100):
        if client._control.state == "recovering":
            break
        await asyncio.sleep(0.01)
    assert client._control.state == "recovering"

    start = asyncio.get_running_loop().time()
    with pytest.raises(ConnectionError, match="recovering"):
        await client.environment_status()
    assert asyncio.get_running_loop().time() - start < 5  # 即取即败，不等恢复窗
    await client.disconnect()


class HangingStatusTransport(FakeTransport):
    """environment/status 永不应答的假传输（status 专用时限测试用）"""

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False
    ):
        if method == "environment/status":
            await asyncio.sleep(60)
        return await super().send_request(method, params, channel=channel)


@pytest.mark.asyncio
async def test_environment_status_dedicated_timeout(monkeypatch):
    """status 专用 ENVIRONMENT_STATUS_TIMEOUT（对位 Rust 10s 时限）；超时只回
    错误——不退役连接、不触发恢复（连接生死判定归 environment/info 路径）"""
    monkeypatch.setattr(client_module, "ENVIRONMENT_STATUS_TIMEOUT", 0.05)
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = HangingStatusTransport({"initialize": INIT_OK})
        sink.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory, reconnect=FAST)
    await client.connect()
    with pytest.raises(TimeoutError, match="timed out"):
        await client.environment_status()
    await asyncio.sleep(0.05)
    # 超时不触发恢复：仍是首条连接且状态 connected
    assert client._control.state == "connected"
    assert len(sink) == 1
    await client.disconnect()


# ---------------------------------------------------------------------------
# A14：process/start 在飞门控 + 失败清理
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_start_success_leaves_no_cleanup():
    """正常 start：计数归零、无 terminate 清理"""
    transport = FakeTransport(
        {
            "initialize": INIT_OK,
            "process/start": {"processId": "p-1"},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()
    handle = await client.process.start(["true"], "/tmp")
    assert handle.process_id == "p-1"
    assert client._process_start_gate._active == 0
    methods = [m for m, _, _ in transport.requests]
    assert "process/terminate" not in methods
    await client.disconnect()


class DropOnStartTransport(FakeTransport):
    """process/start 模拟断线窗口失败：触发断线回调后以传输错误收尾
    （请求是否落地不可知——孤儿清理测试用）"""

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False
    ):
        if method == "process/start":
            self.requests.append((method, params or {}, channel))
            self.drop("start dropped")
            raise ConnectionError("connection closed")
        return await super().send_request(method, params, channel=channel)


@pytest.mark.asyncio
async def test_process_start_transport_failure_cleaned_up_after_resume():
    """对位 Rust cleanup_process_start：start 传输类失败 → 恢复成功后后台
    terminate 收回可能落地的孤儿进程（恢复走同一会话 resume）"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        responses = {
            "initialize": INIT_OK,
            "process/start": {"processId": "p-unused"},
            "process/terminate": {"running": False},
        }
        transport = (
            DropOnStartTransport(responses) if not sink else FakeTransport(responses)
        )
        sink.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory, reconnect=FAST)
    await client.connect()

    with pytest.raises(ConnectionError):
        await client.process.start(["cat"], "/tmp")
    start_params = [p for m, p, _ in sink[0].requests if m == "process/start"][0]
    orphan_id = start_params["processId"]

    # 恢复成功（resume 同一会话）后，清理 terminate 落到新连接上
    for _ in range(200):
        if len(sink) == 2 and any(
            m == "process/terminate" for m, _, _ in sink[1].requests
        ):
            break
        await asyncio.sleep(0.01)
    terminate_params = [p for m, p, _ in sink[1].requests if m == "process/terminate"]
    assert terminate_params == [{"processId": orphan_id}]
    assert client._process_start_gate._active == 0
    await client.disconnect()


@pytest.mark.asyncio
async def test_process_start_waits_out_recovery_and_lands_on_new_generation():
    """恢复期间的 start 走 Wait 策略等待（对位 start_process 的 loop：
    begin 准入失败即重取对映代连接），恢复成功后落在新连接上——
    绝不落在半死的旧连接上"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport(
            {
                "initialize": INIT_OK,
                "process/start": {"processId": "p-9"},
            }
        )
        sink.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory, reconnect=FAST)
    await client.connect()
    sink[0].drop("test drop")
    for _ in range(100):
        if client._control.state == "recovering":
            break
        await asyncio.sleep(0.01)

    handle = await client.process.start(["true"], "/tmp")
    assert handle.process_id == "p-9"
    # start 只在新对映代连接上发出（旧连接从未见到该请求）
    assert not any(m == "process/start" for m, _, _ in sink[0].requests)
    assert any(m == "process/start" for m, _, _ in sink[1].requests)
    assert client._process_start_gate._active == 0
    await client.disconnect()


class HangableStartTransport(FakeTransport):
    """process/start 挂起直到测试放行（在飞计数/取消清理测试用）"""

    def __init__(self, responses=None):
        super().__init__(responses)
        self.release = asyncio.Event()

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False
    ):
        if method == "process/start":
            self.requests.append((method, params or {}, channel))
            await self.release.wait()
            resp = self.responses.get(method, {})
            if isinstance(resp, Exception):
                raise resp
            return resp
        return await super().send_request(method, params, channel=channel)


@pytest.mark.asyncio
async def test_in_flight_start_blocks_wait_for_process_starts():
    """在飞 start 计数钉住恢复钩子（对位 wait_for_process_starts）：
    start 未完时 _wait_for_process_starts 不返回，归零后放行"""
    transport = HangableStartTransport(
        {
            "initialize": INIT_OK,
            "process/start": {"processId": "p-1"},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    start_task = asyncio.create_task(client.process.start(["sleep", "5"], "/tmp"))
    for _ in range(100):
        if any(m == "process/start" for m, _, _ in transport.requests):
            break
        await asyncio.sleep(0.01)
    assert client._process_start_gate._active == 1

    waiter = asyncio.create_task(client._wait_for_process_starts())
    await asyncio.sleep(0.05)
    assert not waiter.done()  # 在飞未归零——恢复编排须等待

    transport.release.set()
    handle = await start_task
    assert handle.process_id == "p-1"
    await asyncio.wait_for(waiter, 1)
    assert client._process_start_gate._active == 0
    await client.disconnect()


@pytest.mark.asyncio
async def test_cancelled_start_schedules_cleanup():
    """调用方在响应落地前取消 start（对位 Rust 调用方丢 result_rx 分支）：
    取消只是丢回执——线上 RPC 在后台落定后才调度 terminate 清理
    （杜绝 terminate 抢在 start 处理器落地前到达的孤儿竞态）"""
    transport = HangableStartTransport(
        {
            "initialize": INIT_OK,
            "process/start": {"processId": "p-1"},
            "process/terminate": {"running": False},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    start_task = asyncio.create_task(client.process.start(["sleep", "5"], "/tmp"))
    for _ in range(100):
        if any(m == "process/start" for m, _, _ in transport.requests):
            break
        await asyncio.sleep(0.01)
    start_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start_task

    # 取消不打断线上 RPC：在飞计数未归还，terminate 清理尚未调度
    assert client._process_start_gate._active == 1
    assert not any(m == "process/terminate" for m, _, _ in transport.requests)

    # RPC 落定（响应到达但回执已失）后才调度 terminate 清理、归还计数
    transport.release.set()
    start_params = [p for m, p, _ in transport.requests if m == "process/start"][0]
    for _ in range(100):
        terminate_params = [
            p for m, p, _ in transport.requests if m == "process/terminate"
        ]
        if terminate_params:
            break
        await asyncio.sleep(0.01)
    assert terminate_params == [{"processId": start_params["processId"]}]
    assert client._process_start_gate._active == 0
    await client.disconnect()


@pytest.mark.asyncio
async def test_cancelled_start_settles_into_transport_failure_still_cleans_up():
    """取消后 RPC 以传输类失败落定（对位 Rust Err 分支的 cleanup——无论
    调用方回执在与否都调度）：请求是否落地不可知，terminate 清理照发"""
    transport = HangableStartTransport(
        {
            "initialize": INIT_OK,
            "process/start": ConnectionError("connection closed"),
            "process/terminate": {"running": False},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    start_task = asyncio.create_task(client.process.start(["sleep", "5"], "/tmp"))
    for _ in range(100):
        if any(m == "process/start" for m, _, _ in transport.requests):
            break
        await asyncio.sleep(0.01)
    start_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start_task

    transport.release.set()  # 落定失败：传输类错误 → 清理照发
    start_params = [p for m, p, _ in transport.requests if m == "process/start"][0]
    for _ in range(100):
        terminate_params = [
            p for m, p, _ in transport.requests if m == "process/terminate"
        ]
        if terminate_params:
            break
        await asyncio.sleep(0.01)
    assert terminate_params == [{"processId": start_params["processId"]}]
    assert client._process_start_gate._active == 0
    await client.disconnect()


@pytest.mark.asyncio
async def test_start_transport_closed_server_error_cleaned_up():
    """start 以服务端 RPC 层传输关闭（-32000 "JSON-RPC transport closed"）
    落定：同传输类失败调度 terminate 清理（判别同恢复层
    is_transport_class_error——中继预备 + 包内一致性）"""
    transport = FakeTransport(
        {
            "initialize": INIT_OK,
            "process/start": ProtocolError(
                "JSON-RPC error -32000: JSON-RPC transport closed",
                code=-32000,
                server_message="JSON-RPC transport closed",
            ),
            "process/terminate": {"running": False},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    with pytest.raises(ProtocolError, match="transport closed"):
        await client.process.start(["sleep", "5"], "/tmp")

    start_params = [p for m, p, _ in transport.requests if m == "process/start"][0]
    for _ in range(100):
        terminate_params = [
            p for m, p, _ in transport.requests if m == "process/terminate"
        ]
        if terminate_params:
            break
        await asyncio.sleep(0.01)
    assert terminate_params == [{"processId": start_params["processId"]}]
    assert client._process_start_gate._active == 0
    await client.disconnect()


class FlakyTerminateTransport(FakeTransport):
    """process/terminate 首发以服务端 -32000 传输关闭失败（清理重试臂测试用）"""

    def __init__(self, responses=None):
        super().__init__(responses)
        self.terminate_calls = 0

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False
    ):
        if method == "process/terminate":
            self.terminate_calls += 1
            if self.terminate_calls == 1:
                raise ProtocolError(
                    "JSON-RPC error -32000: JSON-RPC transport closed",
                    code=-32000,
                    server_message="JSON-RPC transport closed",
                )
        return await super().send_request(method, params, channel=channel)


@pytest.mark.asyncio
async def test_cleanup_retries_transport_closed_server_error():
    """cleanup 重试臂把 -32000 传输关闭当传输类（对位 Rust
    cleanup_process_start 的 is_transport_closed_error 臂）：连接未到
    终态时重试 terminate——不被一次 -32000 吓退"""
    transport = FlakyTerminateTransport(
        {
            "initialize": INIT_OK,
            "process/start": ConnectionError("connection closed"),
            "process/terminate": {"running": False},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    with pytest.raises(ConnectionError):
        await client.process.start(["sleep", "5"], "/tmp")
    for _ in range(100):
        if transport.terminate_calls >= 2:
            break
        await asyncio.sleep(0.01)
    assert transport.terminate_calls == 2  # -32000 重试后成功收尾
    await client.disconnect()


# ---------------------------------------------------------------------------
# A15：裁决回调入站校验 / reason 回夹 / 回调超时
# ---------------------------------------------------------------------------


def policy_params(process_id: str = "p1", host: str = "api.example.com") -> dict:
    return {
        "processId": process_id,
        "request": {"protocol": "https_connect", "host": host, "port": 443},
    }


def make_policy_client(callback, **kwargs) -> ExecutorClient:
    client = ExecutorClient(
        transport=FakeTransport({"initialize": INIT_OK}),
        network_policy=callback,
        **kwargs,
    )
    # 活进程登记表（对位 Rust Inner.sessions 会话表）：未知 processId 的
    # 裁决请求不调回调直接 deny——默认登记测试常用进程
    client.process.live_processes["p1"] = None
    return client


@pytest.mark.asyncio
async def test_policy_invalid_process_id_denies_without_callback():
    """processId 违规（空串/超 256 字节）不调回调直接 deny not_allowed
    （对位 client_recovery.rs 的 process_id_valid 守卫）"""
    called = False

    async def callback(params):
        nonlocal called
        called = True
        return NetworkPolicyDecision.allow()

    client = make_policy_client(callback)
    for bad_id in ("", "p" * 257):
        reply = await client._answer_network_policy(policy_params(process_id=bad_id))
        assert reply == {"decision": {"type": "deny", "reason": "not_allowed"}}
    assert not called

    # 边界：恰好 256 字节放行到回调（先登记——未知进程不调回调）
    client.process.live_processes["p" * 256] = None
    reply = await client._answer_network_policy(policy_params(process_id="p" * 256))
    assert reply == {"decision": {"type": "allow"}}
    assert called


@pytest.mark.asyncio
async def test_policy_invalid_host_denies_without_callback():
    """host 违规（空串/超 253 字节/含空白/含控制符）不调回调直接 deny
    （对位 client_recovery.rs 的 host_valid 守卫）"""
    called = False

    async def callback(params):
        nonlocal called
        called = True
        return NetworkPolicyDecision.allow()

    client = make_policy_client(callback)
    for bad_host in ("", "h" * 254, "exam ple.com", "evil\x00.com", "a\tb.com"):
        reply = await client._answer_network_policy(policy_params(host=bad_host))
        assert reply == {"decision": {"type": "deny", "reason": "not_allowed"}}
    assert not called

    # 边界：恰好 253 字节放行到回调
    reply = await client._answer_network_policy(policy_params(host="h" * 253))
    assert reply == {"decision": {"type": "allow"}}
    assert called


@pytest.mark.asyncio
async def test_policy_decision_reason_clamped():
    """reason 回夹（对位 decision 收口匹配）：deny/ask 的 reason 超 1024 字节
    或含控制符 → 整体回夹 deny not_allowed；界内原样保留；allow 直通"""
    long_reason = "r" * 1025
    control_reason = "bad\nreason"
    boundary_reason = "r" * 1024

    async def clamped_long(params):
        return NetworkPolicyDecision.deny(long_reason)

    async def clamped_control(params):
        return NetworkPolicyDecision.ask(control_reason)

    async def boundary(params):
        return NetworkPolicyDecision.deny(boundary_reason)

    client = make_policy_client(clamped_long)
    reply = await client._answer_network_policy(policy_params())
    assert reply == {"decision": {"type": "deny", "reason": "not_allowed"}}

    client = make_policy_client(clamped_control)
    reply = await client._answer_network_policy(policy_params())
    assert reply == {"decision": {"type": "deny", "reason": "not_allowed"}}

    client = make_policy_client(boundary)
    reply = await client._answer_network_policy(policy_params())
    assert reply == {"decision": {"type": "deny", "reason": boundary_reason}}


@pytest.mark.asyncio
async def test_policy_callback_timeout_denies():
    """回调超时即 deny not_allowed（对位 timeout(controller.timeout, ...)
    的兜底）——服务端不干等"""

    async def hanging(params):
        await asyncio.sleep(60)
        return NetworkPolicyDecision.allow()

    client = make_policy_client(hanging, network_policy_timeout=0.05)
    reply = await client._answer_network_policy(policy_params())
    assert reply == {"decision": {"type": "deny", "reason": "not_allowed"}}


def test_policy_timeout_must_be_positive():
    """回调时限须为正（对位 Rust 对 0ms 的 Protocol 报错）"""
    with pytest.raises(ValueError, match="network_policy_timeout"):
        make_policy_client(None, network_policy_timeout=0)


@pytest.mark.asyncio
async def test_policy_unknown_process_denies_without_callback():
    """活进程登记表查无此 processId（对位 client_recovery.rs 的
    get_session → None → controller None 分支）：不调回调直接 deny"""
    called = False

    async def callback(params):
        nonlocal called
        called = True
        return NetworkPolicyDecision.allow()

    client = make_policy_client(callback)
    client.process.live_processes.clear()  # 无任何活进程
    reply = await client._answer_network_policy(policy_params())
    assert reply == {"decision": {"type": "deny", "reason": "not_allowed"}}
    assert not called


@pytest.mark.asyncio
async def test_policy_callback_timeout_per_process_from_start_params():
    """回调时限逐进程取登记表里的 policyDecisionTimeoutMs 覆盖（对位 Rust
    controller.timeout 从 start params.network_proxy 取）；登记表 None
    回退全局 network_policy_timeout"""

    async def hanging(params):
        await asyncio.sleep(60)
        return NetworkPolicyDecision.allow()

    client = make_policy_client(hanging, network_policy_timeout=30.0)
    # 逐进程覆盖 50ms：远小于全局 30s——按覆盖值超时拒决
    client.process.live_processes["p1"] = 0.05
    start = asyncio.get_running_loop().time()
    reply = await client._answer_network_policy(policy_params())
    assert reply == {"decision": {"type": "deny", "reason": "not_allowed"}}
    assert asyncio.get_running_loop().time() - start < 5


@pytest.mark.asyncio
async def test_policy_allow_strips_reason():
    """allow 剥离 reason（Rust Allow 变体无 reason 字段）——回调误带
    不回线上"""

    async def allow_with_reason(params):
        return NetworkPolicyDecision(type="allow", reason="should not go online")

    client = make_policy_client(allow_with_reason)
    reply = await client._answer_network_policy(policy_params())
    assert reply == {"decision": {"type": "allow"}}
