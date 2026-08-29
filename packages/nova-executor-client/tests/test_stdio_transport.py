"""StdioTransport 单元测试（spawn 假 executor 子进程，真管道真协议）"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from nova_executor_client import ConnectionError, ProtocolError, StdioTransport

FAKE_SERVER = str(Path(__file__).parent / "fake_executor_server.py")


def make_transport(
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    stderr_handler=None,
    request_timeout: float = 5.0,
) -> StdioTransport:
    """以当前解释器 spawn 假 executor 服务端"""
    return StdioTransport(
        program=sys.executable,
        args=[FAKE_SERVER, *(args or [])],
        env=env,
        request_timeout=request_timeout,
        stderr_handler=stderr_handler,
    )


@pytest.mark.asyncio
async def test_default_command_is_nova_executor_stdio():
    """默认命令对齐 nova-executor --listen stdio（SSH 只需换 program/args）"""
    transport = StdioTransport()
    assert transport.program == "nova-executor"
    assert transport.args == ["--listen", "stdio"]

    ssh = StdioTransport(
        program="ssh", args=["user@host", "nova-executor", "--listen", "stdio"]
    )
    assert ssh.program == "ssh"
    assert ssh.args == ["user@host", "nova-executor", "--listen", "stdio"]


@pytest.mark.asyncio
async def test_connect_and_request_response():
    """连接、请求/响应、断开"""
    transport = make_transport()
    assert not transport.is_connected

    await transport.connect()
    assert transport.is_connected

    result = await transport.send_request("echo", {"hello": "world"})
    assert result == {"hello": "world"}

    await transport.disconnect()
    assert not transport.is_connected
    # 断开幂等
    await transport.disconnect()


@pytest.mark.asyncio
async def test_error_response_raises_protocol_error():
    """JSON-RPC 错误响应映射为 ProtocolError，且结构化携带 error.code"""
    transport = make_transport()
    await transport.connect()
    try:
        with pytest.raises(ProtocolError, match="boom") as exc_info:
            await transport.send_request("fail")
        assert exc_info.value.code == -32600
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_notification_handler_receives_push():
    """服务端主动推送的通知分发给注册处理器"""
    transport = make_transport()
    received = []

    async def handler(msg: dict) -> None:
        received.append(msg)

    transport.on_notification(handler)

    await transport.connect()
    try:
        await transport.send_request("notify")
        # 等通知异步到达
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.02)
        assert received[0]["method"] == "fake/notice"
        assert received[0]["params"] == {"from": "fake-server"}
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_request_timeout():
    """请求超时映射为 TimeoutError，pending 清理后后续请求不受影响"""
    from nova_executor_client import TimeoutError as ExecutorTimeoutError

    transport = make_transport(request_timeout=0.2)
    await transport.connect()
    try:
        with pytest.raises(ExecutorTimeoutError, match="timed out"):
            await transport.send_request("sleep", {"ms": 800})
        # 假服务端串行处理：等迟到的 sleep 响应按未知 id 被忽略后再发新请求
        await asyncio.sleep(1.0)
        assert transport.is_connected
        result = await transport.send_request("echo", {"after": "timeout"})
        assert result == {"after": "timeout"}
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_process_exit_breaks_connection():
    """进程退出传播为连接断开：pending 请求失败、is_connected 翻转"""
    transport = make_transport()
    await transport.connect()

    with pytest.raises(ConnectionError, match="connection closed"):
        await transport.send_request("exit")

    for _ in range(50):
        if not transport.is_connected:
            break
        await asyncio.sleep(0.02)
    assert not transport.is_connected

    with pytest.raises(ConnectionError):
        await transport.send_request("echo")


@pytest.mark.asyncio
async def test_stderr_consumed_via_handler():
    """stderr 被持续消费并回调（防缓冲填满死锁）"""
    lines: list[str] = []
    transport = make_transport(env={"FAKE_STDERR": "1"}, stderr_handler=lines.append)
    await transport.connect()
    try:
        for _ in range(10):
            await transport.send_request("echo")
        for _ in range(50):
            if len(lines) >= 10:
                break
            await asyncio.sleep(0.02)
        assert len(lines) >= 10
        assert all(line.startswith("fake stderr:") for line in lines)
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_env_overlay_and_cwd():
    """env 叠加在继承环境之上（对齐 Rust envs 语义），cwd 经构造参数生效"""
    import tempfile

    transport = StdioTransport(
        program=sys.executable,
        args=[FAKE_SERVER],
        env={"FAKE_VAR": "fake-value"},
        cwd=tempfile.gettempdir(),
        request_timeout=5.0,
    )
    await transport.connect()
    try:
        info = await transport.send_request("envinfo")
        assert info["fakeVar"] == "fake-value"
        assert info["hasHome"] is True  # 继承环境未被整体替换
        assert Path(info["cwd"]).resolve() == Path(tempfile.gettempdir()).resolve()
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_spawn_failure_raises_connection_error():
    """程序不存在 → ConnectionError（不是裸 OSError）"""
    transport = StdioTransport(program="/nonexistent/nova-executor-xxx")
    with pytest.raises(ConnectionError, match="failed to spawn"):
        await transport.connect()
    assert not transport.is_connected


@pytest.mark.asyncio
async def test_client_over_stdio_full_handshake():
    """ExecutorClient 经 stdio 完成 initialize 握手（含版本检查）"""
    from nova_executor_client import ExecutorClient

    async with ExecutorClient(transport=make_transport()) as client:
        result = await client.transport.send_request("echo", {"ping": 1})
        assert result == {"ping": 1}


@pytest.mark.asyncio
async def test_client_rejects_incompatible_protocol_version():
    """服务端 major 版本不等即拒绝连接"""
    from nova_executor_client import ExecutorClient

    client = ExecutorClient(
        transport=make_transport(env={"FAKE_PROTOCOL_VERSION": "2.0"})
    )
    with pytest.raises(ProtocolError, match="协议版本不兼容"):
        await client.connect()
    assert not client.transport.is_connected
