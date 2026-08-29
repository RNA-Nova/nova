"""TransportPool 通道路由与 ExecutorClient 多连接装配测试"""

from __future__ import annotations

import pytest
from fake_transport import FakeTransport

from nova_executor_client import (
    ExecutorClient,
    StdioTransport,
    TransportPool,
    WebSocketTransport,
)
from nova_executor_client.pool import CHANNEL_CONTROL, CHANNEL_DATA
from nova_executor_client.transport import Transport


def make_pool() -> tuple[TransportPool, FakeTransport, FakeTransport]:
    control, data = FakeTransport(), FakeTransport()
    pool = TransportPool({CHANNEL_CONTROL: control, CHANNEL_DATA: data})
    return pool, control, data


@pytest.mark.asyncio
async def test_data_plane_methods_route_to_data_channel():
    """数据面方法（read_stream/write_stream 家族）走数据面连接"""
    pool, control, data = make_pool()

    await pool.send_request("fs/readStream", {"handleId": "h", "path": "file:///x"})
    await pool.send_request("fs/writeStream", {"handleId": "h", "path": "file:///x"})
    await pool.send_notification("fs/writeStream/chunk", {"handleId": "h", "seq": 0})
    await pool.send_request("fs/writeStream/done", {"handleId": "h"})

    assert len(data.requests) == 3
    assert len(data.notifications) == 1
    assert control.requests == [] and control.notifications == []


@pytest.mark.asyncio
async def test_control_plane_methods_route_to_control_channel():
    """控制面方法走主连接"""
    pool, control, data = make_pool()

    await pool.send_request("environment/info")
    await pool.send_request("fs/readFile", {"path": "file:///x"})
    await pool.send_request("process/start", {"argv": ["echo"]})
    await pool.send_notification("initialized", {})

    assert len(control.requests) == 3
    assert len(control.notifications) == 1
    assert data.requests == [] and data.notifications == []


@pytest.mark.asyncio
async def test_explicit_channel_overrides_method_route():
    """显式 channel 参数优先于方法名路由表（写流中止的 fs/close 用例）"""
    pool, control, data = make_pool()

    await pool.send_request("fs/close", {"handleId": "w"}, channel=CHANNEL_DATA)
    await pool.send_request("fs/close", {"handleId": "r"})

    # channel 是池级路由参数：落点正确即可，不向下游传输透传
    assert [(m, p) for m, p, _ in data.requests] == [("fs/close", {"handleId": "w"})]
    assert [(m, p) for m, p, _ in control.requests] == [("fs/close", {"handleId": "r"})]


@pytest.mark.asyncio
async def test_single_channel_pool_falls_back_to_default():
    """单连接池：数据面方法回退到默认通道（connections=1 现状行为）"""
    control = FakeTransport()
    pool = TransportPool({CHANNEL_CONTROL: control})

    await pool.send_request("fs/writeStream", {"handleId": "h", "path": "file:///x"})
    await pool.send_notification("fs/writeStream/chunk", {"handleId": "h", "seq": 0})

    assert len(control.requests) == 1
    assert len(control.notifications) == 1


@pytest.mark.asyncio
async def test_notification_fan_in_from_all_connections():
    """通知注册 fan-in 到底层全部连接（流式通知从数据面连接回流）"""
    pool, control, data = make_pool()
    pool.on_notification(lambda msg: None)

    assert len(control.handlers) == 1
    assert len(data.handlers) == 1


@pytest.mark.asyncio
async def test_connect_disconnect_all_channels():
    """连接/断开覆盖全部通道；is_connected 聚合"""
    pool, control, data = make_pool()
    assert not pool.is_connected

    await pool.connect()
    assert pool.is_connected and control.connected and data.connected

    await pool.disconnect()
    assert not pool.is_connected
    assert not control.connected and not data.connected


@pytest.mark.asyncio
async def test_connect_failure_rolls_back_connected_channels():
    """第二条连接失败时回滚第一条，避免半连接状态"""

    class BrokenTransport(FakeTransport):
        async def connect(self) -> None:
            raise RuntimeError("dial failed")

    control = FakeTransport()
    pool = TransportPool({CHANNEL_CONTROL: control, CHANNEL_DATA: BrokenTransport()})

    with pytest.raises(RuntimeError, match="dial failed"):
        await pool.connect()
    assert not control.connected  # 已回滚


@pytest.mark.asyncio
async def test_custom_method_routes():
    """路由表可自定义（不硬编码数据面方法集）"""
    control, bulk = FakeTransport(), FakeTransport()
    pool = TransportPool(
        {CHANNEL_CONTROL: control, "bulk": bulk},
        method_routes={"fs/walk": "bulk", "fs/copy": "bulk"},
    )

    await pool.send_request("fs/walk", {"path": "file:///x"})
    await pool.send_request("fs/readFile", {"path": "file:///x"})

    assert [m for m, _, _ in bulk.requests] == ["fs/walk"]
    assert [m for m, _, _ in control.requests] == ["fs/readFile"]


def test_pool_requires_default_channel():
    with pytest.raises(ValueError, match="default channel"):
        TransportPool({CHANNEL_DATA: FakeTransport()})


# ---------------------------------------------------------------------------
# ExecutorClient 装配层
# ---------------------------------------------------------------------------


def make_fake_factory(sink: list) -> "callable":
    def factory() -> Transport:
        transport = FakeTransport(
            {
                "initialize": {
                    "sessionId": "fake-session",
                    "protocolVersion": "1.0",
                },
                "environment/info": {"shell": {"name": "zsh", "path": "/bin/zsh"}},
            }
        )
        sink.append(transport)
        return transport

    return factory


def test_default_client_is_single_websocket_connection():
    """现状行为不变：url 构造 → 单 WebSocketTransport，管理器经池工作"""
    client = ExecutorClient("ws://localhost:8080", token="t")
    assert isinstance(client.transport, WebSocketTransport)
    assert client.transport.url == "ws://localhost:8080"
    assert client.transport.token == "t"
    assert isinstance(client._pool, TransportPool)
    assert len(client._pool.iter_transports()) == 1


def test_client_requires_some_transport_source():
    with pytest.raises(ValueError, match="至少提供一个"):
        ExecutorClient()


def test_transport_instance_forbids_multi_connection():
    with pytest.raises(ValueError, match="connections 只能为 1"):
        ExecutorClient(transport=FakeTransport(), connections=2)


def test_invalid_connections_value():
    with pytest.raises(ValueError, match="connections"):
        ExecutorClient("ws://localhost:8080", connections=3)


def test_from_stdio_builds_stdio_transports():
    """from_stdio：本地默认命令与 SSH 远程命令同一形态"""
    client = ExecutorClient.from_stdio()
    assert isinstance(client.transport, StdioTransport)
    assert client.transport.program == "nova-executor"
    assert client.transport.args == ["--listen", "stdio"]

    ssh = ExecutorClient.from_stdio(
        program="ssh",
        args=["user@host", "nova-executor", "--listen", "stdio"],
        connections=2,
    )
    transports = ssh._pool.iter_transports()
    assert len(transports) == 2
    assert all(t.program == "ssh" for t in transports)


@pytest.mark.asyncio
async def test_dual_connection_handshakes_each_transport():
    """connections=2：两条连接各自完成 initialize + initialized 握手"""
    sink: list = []
    client = ExecutorClient(transport_factory=make_fake_factory(sink), connections=2)

    await client.connect()
    try:
        assert len(sink) == 2
        for transport in sink:
            methods = [m for m, _, _ in transport.requests]
            notify_methods = [m for m, _, _ in transport.notifications]
            assert methods == ["initialize"]
            assert notify_methods == ["initialized"]
        assert client._pool.is_connected
    finally:
        await client.disconnect()
    assert all(not t.connected for t in sink)


@pytest.mark.asyncio
async def test_dual_connection_fs_streams_use_data_channel():
    """端到端路由：fs.write_stream/read_stream 落数据面，其余落控制面"""
    sink: list = []
    client = ExecutorClient(transport_factory=make_fake_factory(sink), connections=2)
    await client.connect()
    try:
        control, data = sink
        data.responses.update(
            {
                "fs/writeStream": {"handleId": "h"},
                "fs/writeStream/done": {"handleId": "h", "totalBytes": 2},
            }
        )

        total = await client.fs.write_stream("file:///tmp/x", [b"ab"])
        assert total == 2
        await client.environment_info()

        data_methods = [m for m, _, _ in data.requests]
        control_methods = [m for m, _, _ in control.requests]
        assert "fs/writeStream" in data_methods
        assert "fs/writeStream/done" in data_methods
        assert "environment/info" in control_methods
        assert not any(m.startswith("fs/writeStream") for m in control_methods)
    finally:
        await client.disconnect()
