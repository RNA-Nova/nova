"""断线重连与会话恢复测试（ReconnectStrategy + ManagedTransport，对位 Rust client_recovery）"""

from __future__ import annotations

import asyncio

import pytest
from fake_transport import FakeTransport
from nova_protocol import InitializeResponse

from nova_exec_server_client import (
    ConnectionError,
    ExecutorClient,
    ProtocolError,
    ReconnectStrategy,
)
from nova_exec_server_client.errors import SESSION_ALREADY_ATTACHED, AuthError
from nova_exec_server_client.notifications import NotificationRouter
from nova_exec_server_client.recovery import ManagedTransport

FAST = ReconnectStrategy(interval=0.01, timeout=2.0)


def make_handshake_log(sink: list):
    """记录每次握手携带的 resumeSessionId 的握手函数（FakeTransport 版）"""

    async def handshake(transport: FakeTransport, resume_session_id: str | None):
        result = await transport.send_request(
            "initialize",
            {"clientName": "test", "resumeSessionId": resume_session_id},
        )
        response = InitializeResponse.model_validate(result)
        await transport.send_notification("initialized", {})
        return response

    return handshake


class FlakyConnectTransport(FakeTransport):
    """connect 即失败的假传输（重连尝试耗尽测试用）"""

    async def connect(self) -> None:
        raise ConnectionError("dial failed")


def test_strategy_defaults_align_with_rust():
    """默认策略 = Rust 行为：100ms 固定间隔、25s 总时限、时限内不限次"""
    strategy = ReconnectStrategy()
    assert strategy.interval == 0.1
    assert strategy.backoff == 1.0
    assert strategy.max_interval == 5.0
    assert strategy.max_attempts is None
    assert strategy.timeout == 25.0
    # 冻结值对象
    with pytest.raises(Exception):
        strategy.interval = 1.0  # type: ignore[misc]


def test_strategy_validation():
    with pytest.raises(ValueError, match="interval"):
        ReconnectStrategy(interval=0)
    with pytest.raises(ValueError, match="backoff"):
        ReconnectStrategy(backoff=0.5)
    with pytest.raises(ValueError, match="max_interval"):
        ReconnectStrategy(interval=1.0, max_interval=0.5)
    with pytest.raises(ValueError, match="max_attempts"):
        ReconnectStrategy(max_attempts=0)
    with pytest.raises(ValueError, match="timeout"):
        ReconnectStrategy(timeout=0)


def test_strategy_backoff_delays():
    """退避序列：interval 起按 backoff 放大，封顶 max_interval"""
    delays = ReconnectStrategy(interval=0.1, backoff=2.0, max_interval=0.25)
    it = delays.delays()
    assert [next(it) for _ in range(4)] == [0.1, 0.2, 0.25, 0.25]
    fixed = ReconnectStrategy(interval=0.1)
    it = fixed.delays()
    assert [next(it) for _ in range(3)] == [0.1, 0.1, 0.1]


def make_managed(sink: list, strategy: ReconnectStrategy | None = FAST, **kwargs):
    """factory 逐次出件的 ManagedTransport（首个 fake 立即可用）"""

    def factory() -> FakeTransport:
        transport = FakeTransport(
            {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
        )
        sink.append(transport)
        return transport

    return ManagedTransport(
        factory, handshake=make_handshake_log(sink), strategy=strategy, **kwargs
    )


@pytest.mark.asyncio
async def test_reconnect_with_resume_session_id():
    """断线自动重连：initialize 带 resumeSessionId，恢复后调用照常"""
    sink: list[FakeTransport] = []
    managed = make_managed(sink)
    await managed.connect()
    assert managed.state == "connected"
    assert managed.session_id == "fake-session"

    sink[0].drop("test drop")
    for _ in range(100):
        if managed.state == "connected" and len(sink) == 2:
            break
        await asyncio.sleep(0.01)

    assert managed.state == "connected"
    resumed = sink[1]
    # 重连握手携带原会话 id（进程表/输出流跨重连存活的服务端语义）
    assert resumed.requests[0] == (
        "initialize",
        {"clientName": "test", "resumeSessionId": "fake-session"},
        None,
    )
    assert [m for m, _, _ in resumed.notifications] == ["initialized"]
    # 恢复后调用走新传输
    resumed.responses["echo"] = {"ok": True}
    assert await managed.send_request("echo") == {"ok": True}
    await managed.disconnect()


@pytest.mark.asyncio
async def test_caller_waits_during_recovery():
    """恢复期间的调用等待恢复结果（对位 RecoveryPolicy::Wait），不立即报错"""
    sink: list[FakeTransport] = []
    first = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
    )
    sink.append(first)
    calls = 0

    def factory() -> FakeTransport:
        # instance 首连不占计数；恢复首次尝试失败、第二次成功——恢复窗口可观测
        nonlocal calls
        calls += 1
        if calls == 1:
            return FlakyConnectTransport()
        transport = FakeTransport(
            {
                "initialize": {
                    "sessionId": "fake-session",
                    "protocolVersion": "1.4",
                },
                "echo": {"ok": True},
            }
        )
        sink.append(transport)
        return transport

    managed = ManagedTransport(
        factory,
        instance=first,
        handshake=make_handshake_log(sink),
        strategy=ReconnectStrategy(interval=0.2, timeout=5.0),
    )
    await managed.connect()

    first.drop("test drop")
    assert managed.state == "recovering"
    pending = asyncio.create_task(managed.send_request("echo"))
    await asyncio.sleep(
        0.05
    )  # 恢复尚未完成（首次尝试失败 + 0.2s 退避）：调用挂起而非报错
    assert not pending.done()
    for _ in range(100):
        if pending.done():
            break
        await asyncio.sleep(0.01)
    assert pending.done()
    assert pending.result() == {"ok": True}  # 恢复成功后经新传输完成
    await managed.disconnect()


@pytest.mark.asyncio
async def test_unknown_session_fails_fast_without_retry():
    """resume 命中 unknown session（-32600）→ 不可重试，一次尝试即 failed"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport(
            {
                "initialize": ProtocolError(
                    "JSON-RPC error -32600: unknown session id fake-session",
                    code=-32600,
                )
            }
        )
        sink.append(transport)
        return transport

    attempts = 0

    async def handshake(transport, resume_session_id):
        nonlocal attempts
        attempts += 1
        result = await transport.send_request("initialize", {})
        return InitializeResponse.model_validate(result)

    first = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
    )
    # 首连用正常实例（无恢复语义），重连经 factory 产出必失败件
    managed = ManagedTransport(
        factory, instance=first, handshake=handshake, strategy=FAST
    )
    await managed.connect()
    attempts = 0  # 只统计重连握手

    first.drop("test drop")
    for _ in range(100):
        if managed.state == "failed":
            break
        await asyncio.sleep(0.01)

    assert managed.state == "failed"
    assert attempts == 1  # 不可重试错误：无二次尝试
    assert "unknown session id" in managed._failure_message
    with pytest.raises(ConnectionError, match="unknown session id"):
        await managed.send_request("echo")
    await managed.disconnect()


@pytest.mark.asyncio
async def test_session_mismatch_fails():
    """resume 返回的 sessionId 与既有一致性校验（对位 Rust initialize_rpc 检查）"""
    sink: list[FakeTransport] = []
    first = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
    )
    sink.append(first)

    def bad_factory() -> FakeTransport:
        # 重连 fake 返回不同 sessionId → 协议违约
        transport = FakeTransport(
            {"initialize": {"sessionId": "other-session", "protocolVersion": "1.4"}}
        )
        sink.append(transport)
        return transport

    managed = ManagedTransport(
        bad_factory, instance=first, handshake=make_handshake_log(sink), strategy=FAST
    )
    await managed.connect()

    first.drop("test drop")
    for _ in range(100):
        if managed.state == "failed":
            break
        await asyncio.sleep(0.01)
    assert managed.state == "failed"
    assert "unexpected session" in managed._failure_message
    await managed.disconnect()


@pytest.mark.asyncio
async def test_already_attached_is_retried():
    """会话仍附着（-32010）属可重试错误（旧连接服务端侧尚未完全关闭）"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        # 第一次重连尝试报 -32010，第二次成功
        if len(sink) == 1:
            response = ProtocolError(
                "JSON-RPC error -32010: already attached",
                code=SESSION_ALREADY_ATTACHED,
            )
        else:
            response = {"sessionId": "fake-session", "protocolVersion": "1.4"}
        transport = FakeTransport({"initialize": response})
        sink.append(transport)
        return transport

    managed = ManagedTransport(
        factory, handshake=make_handshake_log(sink), strategy=FAST
    )
    await managed.connect()
    sink[0].drop("test drop")
    for _ in range(100):
        if managed.state == "connected" and len(sink) == 3:
            break
        await asyncio.sleep(0.01)
    assert managed.state == "connected"
    assert len(sink) == 3  # 首连 + 两次重连尝试
    await managed.disconnect()


@pytest.mark.asyncio
async def test_attempts_exhausted_fails():
    """重连尝试耗尽 → failed，后续调用 ConnectionError"""
    sink: list[FakeTransport] = []
    first = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
    )
    sink.append(first)
    factory_calls = 0

    def factory() -> FakeTransport:
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            return first
        return FlakyConnectTransport()

    managed = ManagedTransport(
        factory,
        handshake=make_handshake_log(sink),
        strategy=ReconnectStrategy(interval=0.01, max_attempts=2, timeout=5.0),
    )
    await managed.connect()
    first.drop("test drop")
    for _ in range(100):
        if managed.state == "failed":
            break
        await asyncio.sleep(0.01)
    assert managed.state == "failed"
    assert factory_calls == 3  # 首连 1 次 + 重连 2 次
    with pytest.raises(ConnectionError):
        await managed.send_request("echo")
    await managed.disconnect()


@pytest.mark.asyncio
async def test_no_strategy_disconnect_is_terminal():
    """strategy=None：断线即失败，不重连（原 auto_reconnect=False 语义）"""
    sink: list[FakeTransport] = []
    managed = make_managed(sink, strategy=None)
    await managed.connect()
    sink[0].drop("test drop")
    await asyncio.sleep(0.05)
    assert managed.state == "failed"
    assert len(sink) == 1  # 无重连尝试
    with pytest.raises(ConnectionError, match="disconnected"):
        await managed.send_request("echo")
    await managed.disconnect()


@pytest.mark.asyncio
async def test_disconnect_cancels_recovery():
    """恢复进行中主动 disconnect：恢复任务取消，状态归 disconnected"""
    sink: list[FakeTransport] = []
    first = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
    )
    sink.append(first)
    calls = 0

    def factory() -> FakeTransport:
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        return FlakyConnectTransport()

    managed = ManagedTransport(
        factory,
        handshake=make_handshake_log(sink),
        strategy=ReconnectStrategy(interval=0.5, max_attempts=100, timeout=30.0),
    )
    await managed.connect()
    first.drop("test drop")
    await asyncio.sleep(0.05)
    assert managed.state == "recovering"
    await managed.disconnect()
    assert managed.state == "disconnected"
    # disconnect 后恢复不再生效（sleep 间隔中被取消）
    await asyncio.sleep(0.6)
    assert managed.state == "disconnected"


@pytest.mark.asyncio
async def test_client_explicit_resume_session_id_first_connect():
    """ExecutorClient 首连显式 resume：initialize 携带指定 resumeSessionId，
    服务端回到同一会话即建立（对位 Rust ConnectOptions.resume_session_id）"""
    transport = FakeTransport(
        {"initialize": {"sessionId": "s-9", "protocolVersion": "1.4"}}
    )
    client = ExecutorClient(transport=transport, resume_session_id="s-9")
    await client.connect()
    assert transport.requests[0][0] == "initialize"
    assert transport.requests[0][1]["resumeSessionId"] == "s-9"
    assert client.session_id == "s-9"
    await client.disconnect()


@pytest.mark.asyncio
async def test_client_reconnect_none_disables_recovery():
    """reconnect=None：断线即失败（原 auto_reconnect=False 语义迁移）"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport(
            {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
        )
        sink.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory, reconnect=None)
    await client.connect()
    sink[0].drop("test drop")
    await asyncio.sleep(0.05)
    assert len(sink) == 1
    with pytest.raises(ConnectionError):
        await client.environment_status()
    await client.disconnect()


@pytest.mark.asyncio
async def test_request_recovery_retires_transport_and_recovers():
    """request_recovery（对位 Rust close_transport + request_recovery 组合）：
    connected 下退役当前传输（后台关闭，不等关闭握手）并按策略 resume 同一会话"""
    sink: list[FakeTransport] = []
    managed = make_managed(sink)
    await managed.connect()
    first = sink[0]

    await managed.request_recovery("request environment/info timed out")
    # 同步进入恢复状态机（不等旧传输关闭收尾——卡死传输的关闭可能耗时数秒，
    # 服务端会话保留窗有限）
    assert managed.state == "recovering"

    for _ in range(100):
        if managed.state == "connected" and len(sink) == 2 and not first.connected:
            break
        await asyncio.sleep(0.01)
    assert managed.state == "connected"
    assert not first.connected  # 旧传输后台关闭收尾
    # 重连握手携带原会话 id
    assert sink[1].requests[0][1]["resumeSessionId"] == "fake-session"
    sink[1].responses["echo"] = {"ok": True}
    assert await managed.send_request("echo") == {"ok": True}
    await managed.disconnect()


@pytest.mark.asyncio
async def test_request_recovery_stale_probe_ignored():
    """陈旧探针守卫（对位 Rust ptr_eq 检查）：failed / disconnected / 主动关闭
    期间到达的 request_recovery 一律忽略"""
    sink: list[FakeTransport] = []
    managed = make_managed(sink, strategy=None)
    await managed.connect()
    sink[0].drop("test drop")  # 无策略 → failed
    await asyncio.sleep(0.05)
    assert managed.state == "failed"
    await managed.request_recovery("stale probe")
    assert managed.state == "failed"  # 忽略：不再起恢复
    assert len(sink) == 1
    await managed.disconnect()

    sink2: list[FakeTransport] = []
    managed2 = make_managed(sink2)
    await managed2.connect()
    await managed2.disconnect()
    await managed2.request_recovery("stale probe")
    assert managed2.state == "disconnected"  # 忽略
    assert len(sink2) == 1


@pytest.mark.asyncio
async def test_request_recovery_without_strategy_fails():
    """无恢复手段（strategy=None）时 request_recovery 转 failed——调用方拿到
    明确断线错误而非干等（对位 Rust 无 reconnect strategy 的 recover 路径）"""
    sink: list[FakeTransport] = []
    managed = make_managed(sink, strategy=None)
    await managed.connect()

    await managed.request_recovery("probe timed out")
    for _ in range(100):
        if managed.state == "failed" and not sink[0].connected:
            break
        await asyncio.sleep(0.01)
    assert managed.state == "failed"
    assert not sink[0].connected
    with pytest.raises(ConnectionError, match="probe timed out"):
        await managed.send_request("echo")
    await managed.disconnect()


# =============================================================================
# 恢复/清扫族语义（对位 Rust client_recovery.rs / client_refresh.rs /
# client.rs finish_fs_read_stream）：恢复开始·失败·刷新退役三态清扫在途
# 推送工作、探针身份守卫、disconnect 会话终结、恢复错误分类
# =============================================================================


@pytest.mark.asyncio
async def test_recovery_start_fails_in_flight_streams():
    """恢复开始即清扫该通道挂起推送工作（对位 Rust recover 起手
    fail_all_http_body_streams）：fs 读流消费者立即收终态失败而非干等——
    恢复成功后旧流也不会续（服务端断连即 close_all fs 句柄表，resume 只对
    process 重挂通知通道）"""
    sink: list[FakeTransport] = []
    router = NotificationRouter()
    managed = make_managed(
        sink, on_failed=lambda msg: router.fail_channel("control", msg)
    )
    await managed.connect()
    queue = router.register_stream("h-1", channel="control")

    sink[0].drop("test drop")
    # 恢复开始即收终态失败（不等恢复结果）
    event = await asyncio.wait_for(queue.get(), 1)
    assert event.kind == "failed" and "disconnected" in event.error
    assert "h-1" not in router._streams

    # 恢复本身照常完成（resume 成功也不续流）
    for _ in range(100):
        if managed.state == "connected" and len(sink) == 2:
            break
        await asyncio.sleep(0.01)
    assert managed.state == "connected"
    assert sink[1].requests[0][1]["resumeSessionId"] == "fake-session"
    await managed.disconnect()


@pytest.mark.asyncio
async def test_recovery_failure_fails_in_flight_streams():
    """恢复失败（failed 终态）同样清扫挂起消费者（对位 Rust fail() →
    fail_all_in_flight_work）——与恢复开始的清扫同路径、幂等"""
    sink: list[FakeTransport] = []
    router = NotificationRouter()
    managed = make_managed(
        sink,
        strategy=None,
        on_failed=lambda msg: router.fail_channel("control", msg),
    )
    await managed.connect()
    queue = router.register_stream("h-1", channel="control")

    sink[0].drop("test drop")
    event = await asyncio.wait_for(queue.get(), 1)
    assert event.kind == "failed" and "disconnected" in event.error
    for _ in range(100):
        if managed.state == "failed":
            break
        await asyncio.sleep(0.01)
    assert managed.state == "failed"
    await managed.disconnect()


@pytest.mark.asyncio
async def test_fail_channel_cleans_http_streams_and_method_queues():
    """fail_channel 清扫扩展（对位 Rust fail_all_in_flight_work）：除 fs 读流外，
    http 流式响应体收 done+error 合成终态、方法订阅队列收 error 终态事件——
    挂起消费者全部唤醒，不干等"""
    router = NotificationRouter()
    fs_q = router.register_stream("h-1", channel="control")
    http_q = router.register_http_body_stream("r-1")
    method_q = router.register_method("network/policyDecision")

    router.fail_channel("control", "exec-server transport disconnected")

    event = await asyncio.wait_for(fs_q.get(), 1)
    assert event.kind == "failed"
    http_event = await asyncio.wait_for(http_q.get(), 1)
    assert http_event["method"] == "http/request/bodyDelta"
    assert http_event["params"]["done"] is True
    assert "disconnected" in http_event["params"]["error"]
    method_event = await asyncio.wait_for(method_q.get(), 1)
    assert "disconnected" in method_event["error"]
    # 注册表清空；再次清扫幂等无残留
    assert router._http_streams == {} and router._method_queues == {}
    router.fail_channel("control", "again")


@pytest.mark.asyncio
async def test_refresh_connection_fails_in_flight_work():
    """刷新退役旧会话即清扫在途推送工作（对位 Rust retire() →
    fail_all_in_flight_work）：挂起消费者收终态失败，新会话全新（不 resume）"""
    sink: list[FakeTransport] = []
    router = NotificationRouter()
    managed = make_managed(
        sink, on_failed=lambda msg: router.fail_channel("control", msg)
    )
    await managed.connect()
    queue = router.register_stream("h-1", channel="control")

    await managed.refresh_connection()

    event = await asyncio.wait_for(queue.get(), 1)
    assert event.kind == "failed" and "replaced" in event.error
    assert managed.state == "connected"
    # 全新连接：不携带 resumeSessionId
    assert sink[1].requests[0][1].get("resumeSessionId") is None
    await managed.disconnect()


@pytest.mark.asyncio
async def test_disconnect_clears_session_id_for_fresh_reconnect():
    """disconnect 后会话终结：再次 connect 开全新会话，不把旧 session id 当
    resumeSessionId 下发（对位 Rust 每次 connect 全新会话——stdio 旧进程已死
    resume 必收 -32600 / WS 静默续旧会话的两种歧义都被根除）"""
    sink: list[FakeTransport] = []
    managed = make_managed(sink)
    await managed.connect()
    assert managed.session_id == "fake-session"
    await managed.disconnect()
    assert managed.session_id is None

    await managed.connect()
    inits = [p for m, p, _ in sink[0].requests if m == "initialize"]
    assert len(inits) == 2
    assert inits[0].get("resumeSessionId") is None  # 首连（无显式 resume）
    assert inits[1].get("resumeSessionId") is None  # 重连全新会话
    assert managed.state == "connected"
    await managed.disconnect()


@pytest.mark.asyncio
async def test_explicit_resume_session_id_survives_pre_connect_disconnect():
    """显式 resume_session_id 是首连目标：从未连接时的 disconnect（幂等空操作）
    不吞掉它；首连建立会话后 disconnect 再 connect 则开全新会话"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        # 假服务端如实回到被 resume 的会话（对位服务端 resume 语义）
        transport = FakeTransport(
            {"initialize": {"sessionId": "s-9", "protocolVersion": "1.4"}}
        )
        sink.append(transport)
        return transport

    managed = ManagedTransport(
        factory,
        handshake=make_handshake_log(sink),
        strategy=FAST,
        resume_session_id="s-9",
    )
    await managed.disconnect()  # 状态本就 disconnected——保留首连 resume 目标
    assert managed.session_id == "s-9"

    await managed.connect()
    assert sink[0].requests[0][1]["resumeSessionId"] == "s-9"
    assert managed.session_id == "s-9"

    await managed.disconnect()
    assert managed.session_id is None  # 已建立会话随连接终结


@pytest.mark.asyncio
async def test_request_recovery_stale_transport_probe_ignored():
    """传输身份守卫（对位 Rust request_recovery 的 Arc::ptr_eq 检查）：探针
    超时往返期间断线回调已完成恢复时，迟到的陈旧探针忽略——不得误杀恢复
    出的健康新连接；带当前传输身份的探针照常受理"""
    sink: list[FakeTransport] = []
    managed = make_managed(sink)
    await managed.connect()
    probed = managed.current_transport

    # 探针超时往返期间：断线回调路径已完成恢复（换新传输）
    sink[0].drop("test drop")
    for _ in range(100):
        if managed.state == "connected" and len(sink) == 2:
            break
        await asyncio.sleep(0.01)
    assert managed.state == "connected"

    # 陈旧探针迟到：带所探旧传输身份 → 忽略（不退役健康新连接）
    await managed.request_recovery("stale probe", transport=probed)
    assert managed.state == "connected"
    assert len(sink) == 2  # 没有再起恢复
    assert managed.current_transport is sink[1]
    assert sink[1].connected

    # 对照：带当前传输身份的探针受理
    await managed.request_recovery("live probe", transport=managed.current_transport)
    assert managed.state == "recovering"
    for _ in range(100):
        if managed.state == "connected" and len(sink) == 3:
            break
        await asyncio.sleep(0.01)
    assert managed.state == "connected"
    assert len(sink) == 3
    await managed.disconnect()


@pytest.mark.asyncio
async def test_ws_auth_error_retried_until_recovery_timeout():
    """WS 握手 401（AuthError）属可重试恢复错误（对位 Rust
    WebSocketConnect{..} 全量可重试）：重试到恢复总时限再 failed，不一次判死"""
    sink: list[FakeTransport] = []
    first = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
    )
    sink.append(first)
    attempts = 0

    class AuthRejectTransport(FakeTransport):
        async def connect(self) -> None:
            nonlocal attempts
            attempts += 1
            raise AuthError("authentication failed: invalid or missing token")

    def factory() -> FakeTransport:
        return AuthRejectTransport()

    managed = ManagedTransport(
        factory,
        instance=first,
        handshake=make_handshake_log(sink),
        strategy=ReconnectStrategy(interval=0.01, timeout=0.15),
    )
    await managed.connect()
    first.drop("test drop")
    # 等恢复撞总时限转 failed：死线轮询而非固定次数（Windows 的 asyncio
    # 定时器粒度粗，固定 200×10ms 在慢调度下可能不够覆盖恢复窗口）
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while managed.state != "failed":
        assert loop.time() < deadline, "recovery did not fail within 5s"
        await asyncio.sleep(0.01)
    assert attempts > 1  # 可重试：多次尝试直到恢复总时限
    assert "invalid or missing token" in managed._failure_message
    await managed.disconnect()


@pytest.mark.asyncio
async def test_transport_closed_server_error_is_retryable():
    """服务端 RPC 层传输关闭（-32000 "JSON-RPC transport closed"）属可重试
    恢复错误（对位 Rust is_transport_closed_error）——重试后恢复成功"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        # 第一次重连尝试报传输关闭，第二次成功
        if len(sink) == 1:
            response: dict | ProtocolError = ProtocolError(
                "JSON-RPC error -32000: JSON-RPC transport closed",
                code=-32000,
                # 对位真实构造（传输层错误响应落地处）——线上裸消息原样
                # 携带，恢复甄别按它全等比较
                server_message="JSON-RPC transport closed",
            )
        else:
            response = {"sessionId": "fake-session", "protocolVersion": "1.4"}
        transport = FakeTransport({"initialize": response})
        sink.append(transport)
        return transport

    managed = ManagedTransport(
        factory, handshake=make_handshake_log(sink), strategy=FAST
    )
    await managed.connect()
    sink[0].drop("test drop")
    for _ in range(100):
        if managed.state == "connected" and len(sink) == 3:
            break
        await asyncio.sleep(0.01)
    assert managed.state == "connected"
    assert len(sink) == 3  # 首连 + 两次重连尝试（传输关闭错误被重试）
    await managed.disconnect()


@pytest.mark.asyncio
async def test_same_code_other_message_not_retryable():
    """同为 -32000 码但非传输关闭消息：不属 is_transport_closed_error——
    不可重试，一次尝试即 failed（消息甄别防止同码误重试）"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport(
            {
                "initialize": ProtocolError(
                    "JSON-RPC error -32000: server exploded",
                    code=-32000,
                    server_message="server exploded",
                )
            }
        )
        sink.append(transport)
        return transport

    attempts = 0

    async def handshake(transport, resume_session_id):
        nonlocal attempts
        attempts += 1
        result = await transport.send_request("initialize", {})
        return InitializeResponse.model_validate(result)

    first = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
    )
    managed = ManagedTransport(
        factory, instance=first, handshake=handshake, strategy=FAST
    )
    await managed.connect()
    attempts = 0  # 只统计重连握手

    first.drop("test drop")
    for _ in range(100):
        if managed.state == "failed":
            break
        await asyncio.sleep(0.01)
    assert managed.state == "failed"
    assert attempts == 1  # 不可重试错误：无二次尝试
    await managed.disconnect()


@pytest.mark.asyncio
async def test_recovery_waits_for_pre_recovery_hook_before_reconnect():
    """pre_recovery 钩子接线（对位 Rust recover 起手的 timeout_at(deadline,
    wait_for_process_starts())——恢复清扫在途推送后、重建传输前等在飞
    process/start 归零）：钩子未了结绝不发起重连"""
    sink: list[FakeTransport] = []
    hook_started = asyncio.Event()
    hook_release = asyncio.Event()

    async def pre_recovery() -> None:
        hook_started.set()
        await hook_release.wait()

    managed = make_managed(sink, pre_recovery=pre_recovery)
    await managed.connect()
    sink[0].drop("test drop")
    # 恢复开始并进入钩子等待
    await asyncio.wait_for(hook_started.wait(), timeout=2.0)
    await asyncio.sleep(0.1)  # 给重连循环留窗——它不该动
    assert managed.state == "recovering"
    assert len(sink) == 1  # factory 未被调第二次：重连未发起
    hook_release.set()
    # 钩子了结后重连推进至恢复成功
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while managed.state != "connected":
        assert loop.time() < deadline, "recovery did not complete within 5s"
        await asyncio.sleep(0.01)
    assert len(sink) == 2  # 首连 + 一次重连
    await managed.disconnect()


@pytest.mark.asyncio
async def test_pre_recovery_hook_timeout_fails_recovery():
    """pre_recovery 钩子在恢复总时限内未了结（在飞 start 悬置不归零）——
    整个恢复转 failed（对位 Rust timeout_at(deadline, ...) 超时分支）"""
    sink: list[FakeTransport] = []
    never = asyncio.Event()  # 永不置位

    async def pre_recovery() -> None:
        await never.wait()

    managed = make_managed(
        sink,
        strategy=ReconnectStrategy(interval=0.01, timeout=0.15),
        pre_recovery=pre_recovery,
    )
    await managed.connect()
    sink[0].drop("test drop")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while managed.state != "failed":
        assert loop.time() < deadline, "recovery did not fail within 5s"
        await asyncio.sleep(0.01)
    assert "process starts" in (managed.failure_message or "")
    assert len(sink) == 1  # 从未发起重连
    await managed.disconnect()


@pytest.mark.asyncio
async def test_pre_recovery_hook_exception_fails_recovery():
    """pre_recovery 钩子自身异常：恢复转 failed（不卡死 recovering——
    与钩子超时同归宿）"""
    sink: list[FakeTransport] = []

    async def broken_hook() -> None:
        raise RuntimeError("hook boom")

    managed = make_managed(sink, pre_recovery=broken_hook)
    await managed.connect()
    sink[0].drop("test drop")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while managed.state != "failed":
        assert loop.time() < deadline, "recovery did not fail within 5s"
        await asyncio.sleep(0.01)
    assert "hook boom" in (managed.failure_message or "")
    assert len(sink) == 1  # 从未发起重连
    await managed.disconnect()


# =============================================================================
# request_recovery 快速判死 / 退役任务跟踪 / 连接安装兜底 / 恢复超时消息 /
# 外部断线观察口身份守卫
# =============================================================================


class PendingAwareTransport(FakeTransport):
    """带 _fail_pending/_abort_connection 桩的假传输（快速判死测试用——
    对位真实传输 _JsonRpcTransport._mark_disconnected 的后半段）"""

    def __init__(self, responses=None):
        super().__init__(responses)
        self.pending_failed = 0
        self.aborted = 0

    def _fail_pending(self) -> None:
        self.pending_failed += 1

    def _abort_connection(self) -> None:
        self.aborted += 1


@pytest.mark.asyncio
async def test_request_recovery_fails_pending_synchronously():
    """request_recovery 退役传输的在途请求同步判死 + 中止底层承载（对位
    Rust close_transport 的即时 drain_pending）——不等后台优雅关闭走完"""
    sink: list[PendingAwareTransport] = []

    def factory() -> PendingAwareTransport:
        transport = PendingAwareTransport(
            {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
        )
        sink.append(transport)
        return transport

    managed = ManagedTransport(
        factory, handshake=make_handshake_log(sink), strategy=FAST
    )
    await managed.connect()
    first = sink[0]

    await managed.request_recovery("probe timed out")
    # 同步快速判死（request_recovery 返回即已生效，不经事件循环等待）
    assert first.pending_failed == 1
    assert first.aborted == 1
    assert managed.state == "recovering"

    for _ in range(100):
        if managed.state == "connected" and len(sink) == 2:
            break
        await asyncio.sleep(0.01)
    assert managed.state == "connected"
    await managed.disconnect()


class SlowCloseTransport(FakeTransport):
    """disconnect 挂起直到测试放行的假传输（退役关闭任务跟踪测试用）"""

    def __init__(self, responses=None):
        super().__init__(responses)
        self.release_close = asyncio.Event()
        self.close_done = False

    async def disconnect(self) -> None:
        await self.release_close.wait()
        self.close_done = True
        await super().disconnect()


@pytest.mark.asyncio
async def test_retired_close_tasks_tracked_and_gathered_on_disconnect():
    """退役传输的后台关闭任务跟踪集（对齐 _cleanup_tasks 模式）：连续多次
    恢复各自跟踪不被覆盖；disconnect 时 gather 全量收尾"""
    sink: list[FakeTransport] = []
    made = 0

    def factory() -> FakeTransport:
        nonlocal made
        responses = {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}
        }
        # 前两条（将被退役）慢关；后续正常
        transport: FakeTransport = (
            SlowCloseTransport(responses) if made < 2 else FakeTransport(responses)
        )
        made += 1
        sink.append(transport)
        return transport

    managed = ManagedTransport(
        factory, handshake=make_handshake_log(sink), strategy=FAST
    )
    await managed.connect()

    await managed.request_recovery("probe 1")
    for _ in range(100):
        if managed.state == "connected" and len(sink) == 2:
            break
        await asyncio.sleep(0.01)
    await managed.request_recovery("probe 2")
    for _ in range(100):
        if managed.state == "connected" and len(sink) == 3:
            break
        await asyncio.sleep(0.01)

    # 两条退役传输的关闭都未完成——任务全量跟踪，不被单槽覆盖
    assert len(managed._retired_close_tasks) == 2
    first, second = sink[0], sink[1]
    assert isinstance(first, SlowCloseTransport) and isinstance(
        second, SlowCloseTransport
    )

    first.release_close.set()
    second.release_close.set()
    await managed.disconnect()
    assert first.close_done and second.close_done  # gather 全量收尾
    assert managed._retired_close_tasks == set()


class HangingConnectTransport(FakeTransport):
    """connect 永不返回的假传输（恢复裸超时消息测试用）"""

    async def connect(self) -> None:
        await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_recovery_timeout_failure_message_backfilled():
    """wait_for 超时抛的裸 asyncio.TimeoutError str 为空——失败消息回填
    "recovery timed out after {timeout}s"（对位 Rust Err(_) 臂），
    冒号后不留空原因"""
    sink: list[FakeTransport] = []
    first = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
    )
    sink.append(first)

    def factory() -> FakeTransport:
        return HangingConnectTransport(
            {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
        )

    managed = ManagedTransport(
        factory,
        instance=first,
        handshake=make_handshake_log(sink),
        strategy=ReconnectStrategy(interval=0.01, timeout=0.15),
    )
    await managed.connect()
    first.drop("test drop")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while managed.state != "failed":
        assert loop.time() < deadline, "recovery did not fail within 5s"
        await asyncio.sleep(0.01)
    message = managed.failure_message or ""
    assert "recovery timed out after 0.15s" in message
    assert not message.rstrip().endswith(":")
    await managed.disconnect()


@pytest.mark.asyncio
async def test_connect_install_failure_disconnects_transport():
    """connect() 的 _install 校验失败（resume 会话一致性违约）同样断开
    底层传输——与握手失败同路径收尾，不留半连接泄漏"""
    transport = FakeTransport(
        {"initialize": {"sessionId": "other-session", "protocolVersion": "1.4"}}
    )
    sink: list[FakeTransport] = []
    managed = ManagedTransport(
        None,
        instance=transport,
        handshake=make_handshake_log(sink),
        strategy=FAST,
        resume_session_id="s-1",
    )
    with pytest.raises(ProtocolError, match="unexpected session"):
        await managed.connect()
    assert not transport.connected  # _install 失败即断开，不泄漏
    assert managed.state == "disconnected"


@pytest.mark.asyncio
async def test_external_disconnect_handler_fires_only_after_identity_guard():
    """外部断线观察口在身份守卫之后扇出：陈旧回调（非当前传输）不惊动
    订阅者；当前传输断线照常扇出并发起恢复"""
    sink: list[FakeTransport] = []
    managed = make_managed(sink)
    await managed.connect()
    seen: list[str | None] = []
    managed.on_disconnect(lambda reason: seen.append(reason))

    # 陈旧回调：断的不是当前传输——不扇出、不起恢复
    stale = FakeTransport({})
    managed._on_transport_disconnect(stale, "stale drop")
    assert seen == []
    assert managed.state == "connected"

    # 当前传输断线：扇出 + 恢复照常
    sink[0].drop("real drop")
    assert seen == ["real drop"]
    for _ in range(100):
        if managed.state == "connected" and len(sink) == 2:
            break
        await asyncio.sleep(0.01)
    assert managed.state == "connected"
    await managed.disconnect()
