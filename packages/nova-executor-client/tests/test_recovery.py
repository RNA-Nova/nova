"""断线重连与会话恢复测试（ReconnectStrategy + ManagedTransport，对位 Rust client_recovery）"""

from __future__ import annotations

import asyncio

import pytest
from fake_transport import FakeTransport

from nova_executor_client import (
    ConnectionError,
    ExecutorClient,
    ProtocolError,
    ReconnectStrategy,
)
from nova_executor_client.errors import SESSION_ALREADY_ATTACHED
from nova_executor_client.protocol import InitializeResponse
from nova_executor_client.recovery import ManagedTransport

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
