"""StdioTransport 单元测试（spawn 假 executor 子进程，真管道真协议）"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nova_exec_server_client import (
    NETWORK_POLICY_REQUEST,
    ConnectionError,
    ProtocolError,
    StdioTransport,
)

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
async def test_default_command_is_nova_exec_server_stdio():
    """默认命令对齐 nova-exec-server --listen stdio（SSH 只需换 program/args）"""
    transport = StdioTransport()
    assert transport.program == "nova-exec-server"
    assert transport.args == ["--listen", "stdio"]

    ssh = StdioTransport(
        program="ssh", args=["user@host", "nova-exec-server", "--listen", "stdio"]
    )
    assert ssh.program == "ssh"
    assert ssh.args == ["user@host", "nova-exec-server", "--listen", "stdio"]


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
    from nova_exec_server_client import TimeoutError as ExecutorTimeoutError

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
        # Windows 路径比较要 normcase+realpath：GitHub runner 的 TEMP 可能是
        # 8.3 短路径形式（RUNNER~1），realpath 展开后大小写/形式才一致
        import os

        assert os.path.normcase(os.path.realpath(info["cwd"])) == os.path.normcase(
            os.path.realpath(tempfile.gettempdir())
        )
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_spawn_failure_raises_connection_error():
    """程序不存在 → ConnectionError（不是裸 OSError）"""
    transport = StdioTransport(program="/nonexistent/nova-exec-server-xxx")
    with pytest.raises(ConnectionError, match="failed to spawn"):
        await transport.connect()
    assert not transport.is_connected


@pytest.mark.asyncio
async def test_client_over_stdio_full_handshake():
    """ExecutorClient 经 stdio 完成 initialize 握手（含版本检查）"""
    from nova_exec_server_client import ExecutorClient

    async with ExecutorClient(transport=make_transport()) as client:
        result = await client.transport.send_request("echo", {"ping": 1})
        assert result == {"ping": 1}


@pytest.mark.asyncio
async def test_client_rejects_incompatible_protocol_version():
    """服务端 major 版本不等即拒绝连接"""
    from nova_exec_server_client import ExecutorClient

    client = ExecutorClient(
        transport=make_transport(env={"FAKE_PROTOCOL_VERSION": "2.0"})
    )
    with pytest.raises(ProtocolError, match="协议版本不兼容"):
        await client.connect()
    assert not client.transport.is_connected


@pytest.mark.asyncio
async def test_send_request_before_connect_raises_connection_error():
    """客户端纪律：未 connect 就发请求 → 干净的 ConnectionError

    （对位 codex rejects_pipelined_requests_before_initialized 的客户端侧
    ——初始化握手前不发业务请求是调用方纪律，SDK 以明确错误表达）"""
    transport = StdioTransport(program=sys.executable, args=[FAKE_SERVER])
    with pytest.raises(ConnectionError, match="not connected"):
        await transport.send_request("echo", {"x": 1})


@pytest.mark.asyncio
async def test_malformed_line_breaks_connection():
    """畸形帧零容忍（对位 Rust rpc.rs MalformedMessage → 断开 + 恢复）：
    服务端写出一行非 JSON，客户端断开连接——pending 以 ConnectionError 收尾、
    is_connected 翻转、断线原因带解析失败（不再静默丢帧继续服务）"""
    transport = make_transport()
    reasons: list[str | None] = []
    transport.on_disconnect(reasons.append)
    await transport.connect()
    try:
        with pytest.raises(ConnectionError, match="connection closed"):
            await transport.send_request("garbage", {})
        for _ in range(50):
            if not transport.is_connected:
                break
            await asyncio.sleep(0.02)
        assert not transport.is_connected
        assert reasons and "failed to parse JSON-RPC message" in (reasons[0] or "")
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_cancelled_request_removed_from_pending_without_further_traffic():
    """对位上游 ed9e5a26a8（PendingRequestGuard）回归测试：在飞请求被取消
    即从 pending 表移除——不等响应、后续流量或断线；迟到响应安全丢弃"""
    transport = make_transport(request_timeout=30.0)
    await transport.connect()
    try:
        call = asyncio.create_task(transport.send_request("sleep", {"ms": 500}))
        for _ in range(100):
            if transport._pending:
                break
            await asyncio.sleep(0.01)
        assert len(transport._pending) == 1

        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert len(transport._pending) == 0  # 无后续流量即清零

        # 迟到响应（服务端 500ms 后回包）找不到 pending 条目，安全丢弃；
        # 连接照常服务
        await asyncio.sleep(0.7)
        assert await transport.send_request("echo", {"ok": True}) == {"ok": True}
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_unknown_method_returns_method_not_found():
    """未知方法 → -32601 method_not_found（对位 PROTOCOL.md:46 与
    request_dispatcher.rs——不回空结果冒充成功）"""
    from nova_exec_server_client import JSON_RPC_METHOD_NOT_FOUND

    transport = make_transport()
    await transport.connect()
    try:
        with pytest.raises(ProtocolError, match="does not implement") as exc_info:
            await transport.send_request("bogus/method")
        assert exc_info.value.code == JSON_RPC_METHOD_NOT_FOUND
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_duplicate_initialize_rejected():
    """initialize 每连接仅一次：第二次报 -32600（对位 PROTOCOL.md:45 与
    handler.rs "initialize may only be sent once per connection"）"""
    from nova_exec_server_client import JSON_RPC_INVALID_REQUEST

    transport = make_transport()
    await transport.connect()
    try:
        first = await transport.send_request("initialize", {"clientName": "test"})
        assert first["sessionId"] == "fake-session"
        with pytest.raises(ProtocolError, match="may only be sent once") as exc_info:
            await transport.send_request("initialize", {"clientName": "test"})
        assert exc_info.value.code == JSON_RPC_INVALID_REQUEST
        # 连接未因协议错误关闭，照常服务
        assert await transport.send_request("echo", {"ok": True}) == {"ok": True}
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_unknown_notification_closes_connection():
    """未知通知 → 服务端直接关闭连接（PROTOCOL.md:46 严格姿态；stdio 形态下
    fake 进程退出即关连接）——连接断开传播，后续请求报 ConnectionError"""
    transport = make_transport()
    await transport.connect()
    await transport.send_notification("bogus/notice", {})

    for _ in range(50):
        if not transport.is_connected:
            break
        await asyncio.sleep(0.02)
    assert not transport.is_connected
    with pytest.raises(ConnectionError):
        await transport.send_request("echo")


# ---------------------------------------------------------------------------
# A13 客户端入站限流（PROTOCOL v1.7，对位 Rust client_inbound_request_limit.rs）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_request_over_limit_breaks_connection():
    """反向请求超 8KiB 即断开（对位 Rust
    client_stdio_connection_rejects_overlong_request：超限断开带原因）"""
    transport = make_transport()
    reasons: list[str | None] = []
    transport.on_disconnect(reasons.append)
    await transport.connect()
    try:
        await transport.send_request(
            "pushraw",
            {
                "messages": [
                    {
                        "id": 9,
                        "method": "network/policyRequest",
                        "params": {"padding": "x" * 9000},
                    }
                ]
            },
        )
        for _ in range(100):
            if not transport.is_connected:
                break
            await asyncio.sleep(0.02)
        assert not transport.is_connected
        assert reasons and "exceeds maximum length of 8192 bytes" in (reasons[0] or "")
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_inbound_unknown_notification_over_limit_breaks_connection():
    """未知通知同按 8KiB 档（对位 Rust：非豁免名单的 Notification 归请求档）"""
    transport = make_transport()
    await transport.connect()
    try:
        await transport.send_request(
            "pushraw",
            {
                "messages": [
                    {"method": "unknown/notice", "params": {"padding": "x" * 9000}}
                ]
            },
        )
        for _ in range(100):
            if not transport.is_connected:
                break
            await asyncio.sleep(0.02)
        assert not transport.is_connected
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_data_plane_notification_bypasses_request_limit():
    """数据面通知放宽 2MiB 档：>8KiB 的 process/output 照常送达，连接不断"""
    transport = make_transport()
    received: list[dict] = []

    async def handler(msg: dict) -> None:
        received.append(msg)

    transport.on_notification(handler)
    await transport.connect()
    try:
        padding = "x" * (100 * 1024)
        await transport.send_request(
            "pushraw",
            {
                "messages": [
                    {
                        "method": "process/output",
                        "params": {
                            "processId": "p1",
                            "stream": "stdout",
                            "data": padding,
                        },
                    }
                ]
            },
        )
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.02)
        assert received and received[0]["params"]["data"] == padding
        assert transport.is_connected
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_read_stream_chunk_notification_bypasses_notification_limit():
    """fs/readStream/chunk 独享 8MiB 档：>2MiB 的块通知照常送达（nova 扩展
    豁免点，对位 Rust MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN）"""
    transport = make_transport()
    received: list[dict] = []

    async def handler(msg: dict) -> None:
        received.append(msg)

    transport.on_notification(handler)
    await transport.connect()
    try:
        padding = "x" * (3 * 1024 * 1024)
        await transport.send_request(
            "pushraw",
            {
                "messages": [
                    {
                        "method": "fs/readStream/chunk",
                        "params": {"streamId": "s1", "seq": 1, "data": padding},
                    }
                ]
            },
        )
        for _ in range(200):
            if received:
                break
            await asyncio.sleep(0.02)
        assert received and received[0]["params"]["data"] == padding
        assert transport.is_connected
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_large_response_bypasses_inbound_limits():
    """响应/错误不受类别限流（仍受传输层单消息上限约束）：>8KiB 的大响应
    照常归 pending（对位 Rust：Response/Error 直接 return None）"""
    transport = make_transport()
    await transport.connect()
    try:
        padding = "x" * (256 * 1024)
        result = await transport.send_request("echo", {"data": padding})
        assert result == {"data": padding}
    finally:
        await transport.disconnect()


# ---------------------------------------------------------------------------
# A6 裁决反向请求并发化（对位 Rust client_recovery.rs spawn_rpc_reader）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_requests_adjudicated_concurrently():
    """并发裁决：首个裁决回调未返回时第二个请求照常准入并先行应答——
    接收循环不被用户回调堵死（对位 Rust 的 tokio::spawn 并发执行）"""
    transport = make_transport()
    started: list[str] = []

    async def handler(params: dict) -> dict:
        host = params["request"]["host"]
        started.append(host)
        if host == "h1.example.com":
            # 第一个裁决等待第二个到达——串行分发下这永远等不到（死锁）
            for _ in range(200):
                if "h2.example.com" in started:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("second request never dispatched concurrently")
            return {"decision": {"type": "deny", "reason": "first"}}
        return {"decision": {"type": "allow"}}

    transport.register_request_handler(NETWORK_POLICY_REQUEST, handler)
    await transport.connect()
    try:
        result = await transport.send_request("policy/probe_batch", {"count": 2})
        assert result["saw"]["1"] == {"type": "deny", "reason": "first"}
        assert result["saw"]["2"] == {"type": "allow"}
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_server_request_with_string_id_breaks_connection():
    """准入校验：字符串 id 的反向请求即断连（对位 Rust admit_inbound_request
    InvalidRequestId → close_transport + request_recovery——真实服务端只发
    非负整数 id）"""
    transport = make_transport()
    reasons: list[str | None] = []
    transport.on_disconnect(reasons.append)

    async def handler(params: dict) -> dict:
        return {"decision": {"type": "allow"}}

    transport.register_request_handler(NETWORK_POLICY_REQUEST, handler)
    await transport.connect()
    try:
        with pytest.raises(ConnectionError):
            await transport.send_request("policy/probe_bad_id", {})
        for _ in range(50):
            if not transport.is_connected:
                break
            await asyncio.sleep(0.02)
        assert not transport.is_connected
        assert reasons and reasons[0] == "exec-server sent an invalid request ID"
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_duplicate_in_flight_request_id_breaks_connection():
    """在飞去重：重复 id 即断连（对位 Rust DuplicateRequestId →
    close_transport + request_recovery）"""
    transport = make_transport()
    reasons: list[str | None] = []
    transport.on_disconnect(reasons.append)
    gate = asyncio.Event()

    async def handler(params: dict) -> dict:
        await gate.wait()  # 永不开闸——保证第二个同 id 请求到达时第一个仍在飞
        return {"decision": {"type": "allow"}}

    transport.register_request_handler(NETWORK_POLICY_REQUEST, handler)
    await transport.connect()
    try:
        with pytest.raises(ConnectionError):
            await transport.send_request("policy/probe_dup", {})
        for _ in range(50):
            if not transport.is_connected:
                break
            await asyncio.sleep(0.02)
        assert not transport.is_connected
        assert reasons and reasons[0] == "exec-server reused an in-flight request ID"
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_inbound_request_at_capacity_gets_immediate_deny(monkeypatch):
    """满员即时拒决：在飞槽满时新请求立刻收到 {deny, not_allowed}（对位 Rust
    AtCapacity 分支回 NetworkPolicyRequestResponse deny——不排队、不干等）"""
    from nova_exec_server_client.transport import _JsonRpcTransport

    monkeypatch.setattr(_JsonRpcTransport, "MAX_INBOUND_SERVER_CALLS", 2)
    transport = make_transport()
    gate = asyncio.Event()
    started: list[str] = []

    async def handler(params: dict) -> dict:
        started.append(params["request"]["host"])
        await gate.wait()
        return {"decision": {"type": "allow"}}

    transport.register_request_handler(NETWORK_POLICY_REQUEST, handler)
    await transport.connect()
    try:
        probe = asyncio.create_task(
            transport.send_request("policy/probe_batch", {"count": 3})
        )
        # 等前两个裁决实际开跑（占满 2 槽），再给第三个请求的准入留一拍
        for _ in range(200):
            if len(started) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(started) == 2
        await asyncio.sleep(0.1)
        gate.set()
        result = await asyncio.wait_for(probe, timeout=5)
        assert result["saw"]["1"] == {"type": "allow"}
        assert result["saw"]["2"] == {"type": "allow"}
        assert result["saw"]["3"] == {"type": "deny", "reason": "not_allowed"}
    finally:
        await transport.disconnect()


# ---------------------------------------------------------------------------
# A18 stderr 泵分块读（对位 Rust client_transport.rs 的 take(8KiB) 读取）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stderr_pump_survives_overlong_line():
    """stderr 泵按 8KiB 分块读：超长行分块送达且泵不停（旧 readline 路径
    在 StreamReader 行限处 ValueError → 泵静默退出、服务端 stderr 堵死），
    之后连接照常服务"""
    chunks: list[str] = []
    transport = make_transport(stderr_handler=chunks.append)
    await transport.connect()
    try:
        line_len = 100 * 1024
        await transport.send_request("stderr_spam", {"len": line_len})
        for _ in range(200):
            if sum(len(c) for c in chunks) >= line_len:
                break
            await asyncio.sleep(0.02)
        assert sum(len(c) for c in chunks) == line_len
        assert "".join(chunks) == "s" * line_len
        # 泵还活着：后续 stderr 行照常送达、连接照常服务
        chunks.clear()
        await transport.send_request("stderr_spam", {"len": 10})
        for _ in range(100):
            if chunks:
                break
            await asyncio.sleep(0.02)
        assert chunks == ["s" * 10]
        assert await transport.send_request("echo", {"ok": True}) == {"ok": True}
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_stderr_pump_delivers_partial_line_without_newline():
    """超长行不带换行：旧 readline 路径等不到 \\n/EOF 停摆（必红），新 8KiB
    分块读照常分发已收部分（8KiB 整倍数段即时送达，尾巴等换行/EOF）"""
    chunks: list[str] = []
    transport = make_transport(stderr_handler=chunks.append)
    await transport.connect()
    try:
        line_len = 100 * 1024
        delivered_floor = (line_len // 8192) * 8192
        await transport.send_request("stderr_spam", {"len": line_len, "newline": False})
        for _ in range(200):
            if sum(len(c) for c in chunks) >= delivered_floor:
                break
            await asyncio.sleep(0.02)
        # 分块读把已满 8KiB 段都送到了（旧路径一块都出不来）
        assert sum(len(c) for c in chunks) == delivered_floor
        # 泵还活着：连接照常服务
        assert await transport.send_request("echo", {"ok": True}) == {"ok": True}
    finally:
        await transport.disconnect()


# ---------------------------------------------------------------------------
# A10 stdio 进程组（对位 Rust client_transport.rs process_group(0) +
# connection.rs terminate/kill_process_group）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="进程组语义 POSIX 专有")
async def test_disconnect_kills_whole_process_group():
    """子进程 spawn 时入独立进程组（pgid == 子进程 pid），disconnect 组杀：
    服务端拉起的孙进程（沙箱 helper 同构）一并收割，不漏杀"""
    transport = make_transport()
    await transport.connect()
    try:
        result = await transport.send_request("spawn_grandchild", {})
        grandchild_pid = result["grandchildPid"]
        os.kill(grandchild_pid, 0)  # 活着
        # 独立进程组：pgid == 直接子 pid
        assert os.getpgid(transport._process.pid) == transport._process.pid
    finally:
        await transport.disconnect()
    assert not transport.is_connected
    # disconnect 已组杀：孙进程消亡（僵尸被 init 收割需片刻）
    for _ in range(100):
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("grandchild survived disconnect")


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="进程组语义 POSIX 专有")
async def test_stdout_eof_with_living_process_reaps_process_tree():
    """旧进程收割：stdout EOF 但进程仍存活（假服务端关 stdout 后长眠）时，
    断线收尾即后台收割旧进程树（对位 Rust 连接退役即 transport.terminate()）
    ——恢复层只造新候选，旧进程不得残留"""
    transport = make_transport()
    await transport.connect()
    process = transport._process
    assert process is not None
    try:
        with pytest.raises(ConnectionError):
            await transport.send_request("close_stdout", {})
        assert not transport.is_connected
        # 断线即起后台收割（EOF 宽限 2s → terminate 组 → kill 组）
        for _ in range(200):
            if process.returncode is not None:
                break
            await asyncio.sleep(0.05)
        assert process.returncode is not None
    finally:
        await transport.disconnect()


# ---------------------------------------------------------------------------
# A1 spawn 环境变量清洗（对位 Rust client_transport.rs 的
# scrub_non_inheritable_env_vars）
# ---------------------------------------------------------------------------

#: 回声环境名单的最小 JSON-RPC 服务端：result 回报各变量是否存在于子进程环境
_ENV_PROBE_SERVER = r"""
import json, os, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    names = msg.get("params", {}).get("names", [])
    print(
        json.dumps({"id": msg["id"], "result": {n: n in os.environ for n in names}}),
        flush=True,
    )
"""


@pytest.mark.asyncio
async def test_spawn_env_scrubs_non_inheritable_vars(monkeypatch):
    """合成 env 双侧清洗（对位 Rust scrub_non_inheritable_env_vars）：继承侧与
    显式覆盖侧的非继承变量都不到达子进程（大小写不敏感比较），普通变量照常传递"""
    monkeypatch.setenv("CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN", "inherited-secret")
    monkeypatch.setenv("KEEP_ME", "kept")
    transport = StdioTransport(
        program=sys.executable,
        args=["-c", _ENV_PROBE_SERVER],
        env={
            "NODE_REPL_AUTH_TOKEN": "explicit-secret",
            # 大小写不敏感：小写形态同样剔除（对位 eq_ignore_ascii_case）
            "openai_identity_token_file": "lower-secret",
        },
        request_timeout=5.0,
    )
    await transport.connect()
    try:
        result = await transport.send_request(
            "probe",
            {
                "names": [
                    "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
                    "NODE_REPL_AUTH_TOKEN",
                    "openai_identity_token_file",
                    "KEEP_ME",
                ]
            },
        )
        assert result == {
            "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN": False,
            "NODE_REPL_AUTH_TOKEN": False,
            "openai_identity_token_file": False,
            "KEEP_ME": True,
        }
    finally:
        await transport.disconnect()


# ---------------------------------------------------------------------------
# A2 出站 NaN/Infinity 防线（json allow_nan=False——serde_json 序列化非有限
# 浮点为 null；py 默认印裸 NaN，服务端词法拒收判连接级致命）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_nan_rejected_before_write():
    """NaN/Infinity 不出站：序列化失败本地抛错；线上未污染，连接照常服务"""
    transport = make_transport()
    await transport.connect()
    try:
        with pytest.raises(ConnectionError, match="Out of range float values"):
            await transport.send_request("echo", {"v": float("nan")})
        with pytest.raises(ValueError, match="Out of range float values"):
            await transport.send_notification("initialized", {"v": float("inf")})
        # 防线未影响连接：后续正常流量照常服务
        assert transport.is_connected
        assert await transport.send_request("echo", {"ok": True}) == {"ok": True}
    finally:
        await transport.disconnect()


# ---------------------------------------------------------------------------
# A3 客户端 outbound 在飞槽位（对位 Rust rpc.rs 的 1024 共享槽 + 1 收摊
# 保留槽双信号量）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regular_in_flight_limit_exceeded_raises(monkeypatch):
    """共享槽满员的常规调用立即报错（对位 PendingRequestLimitExceeded），
    不入队不上线；在飞请求落定后槽位归还，后续调用照常"""
    from nova_exec_server_client.transport import _JsonRpcTransport

    monkeypatch.setattr(_JsonRpcTransport, "MAX_IN_FLIGHT_REGULAR_CALLS", 1)
    transport = make_transport()
    await transport.connect()
    try:
        slow = asyncio.create_task(transport.send_request("sleep", {"ms": 400}))
        for _ in range(100):
            if transport._pending:
                break
            await asyncio.sleep(0.01)
        assert len(transport._pending) == 1

        with pytest.raises(ProtocolError, match="limit of 1 pending requests"):
            await transport.send_request("echo")
        # 满员报错不影响连接与在飞请求
        assert transport.is_connected
        assert await slow == {"slept": True}
        # 槽位已归还：后续调用照常
        assert await transport.send_request("echo", {"ok": True}) == {"ok": True}
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_cleanup_call_uses_reserved_slot(monkeypatch):
    """收摊调用（for_cleanup=True）在共享槽满员时占收摊保留槽照常完成
    （对位 Rust call_for_cleanup 先抢共享槽、再抢保留槽）"""
    from nova_exec_server_client.transport import _JsonRpcTransport

    monkeypatch.setattr(_JsonRpcTransport, "MAX_IN_FLIGHT_REGULAR_CALLS", 1)
    transport = make_transport()
    await transport.connect()
    try:
        slow = asyncio.create_task(transport.send_request("sleep", {"ms": 300}))
        for _ in range(100):
            if transport._pending:
                break
            await asyncio.sleep(0.01)
        assert len(transport._pending) == 1

        result = await transport.send_request(
            "echo", {"cleanup": True}, for_cleanup=True
        )
        assert result == {"cleanup": True}
        assert await slow == {"slept": True}
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_cleanup_double_full_marks_connection_dead(monkeypatch):
    """收摊双无（共享槽 + 保留槽均满）即判连接死亡熔断（对位 Rust
    call_for_cleanup 双无 → close_transport + RpcCallError::Closed）：
    调用报 ConnectionError、在飞请求以连接关闭收尾、断线回调触发"""
    from nova_exec_server_client.transport import _JsonRpcTransport

    monkeypatch.setattr(_JsonRpcTransport, "MAX_IN_FLIGHT_REGULAR_CALLS", 1)
    transport = make_transport()
    reasons: list[str | None] = []
    transport.on_disconnect(reasons.append)
    await transport.connect()
    try:
        slow = asyncio.create_task(transport.send_request("sleep", {"ms": 300}))
        cleanup = asyncio.create_task(
            transport.send_request("sleep", {"ms": 300}, for_cleanup=True)
        )
        for _ in range(100):
            if len(transport._pending) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(transport._pending) == 2

        with pytest.raises(ConnectionError, match="connection closed"):
            await transport.send_request("echo", for_cleanup=True)
        assert not transport.is_connected
        assert reasons and "reserved cleanup slot" in (reasons[0] or "")
        # 连接死亡传播：在飞请求以 ConnectionError 收尾
        with pytest.raises(ConnectionError):
            await slow
        with pytest.raises(ConnectionError):
            await cleanup
    finally:
        await transport.disconnect()


# ---------------------------------------------------------------------------
# A7 result+error 并存时 result 优先（对位 serde contains_key("result")）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_result_wins_when_error_also_present():
    """并存消息按 Response 归（与 _is_malformed_message 的归类一致）：
    result 落定、error 键被忽略；闸门同样放行"""
    from nova_exec_server_client.transport import _gate_inbound_message

    wire = {
        "id": 7,
        "result": {"winner": "result"},
        "error": {"code": -32603, "message": "ignored"},
    }
    message, fatal = _gate_inbound_message(json.dumps(wire).encode())
    assert fatal is None and message == wire

    transport = make_transport()
    future = asyncio.get_running_loop().create_future()
    transport._pending[7] = future
    transport._resolve_response(message)
    assert future.result() == {"winner": "result"}


# ---------------------------------------------------------------------------
# A8 error.code i64 范围（对位 serde i64 derive 溢出即畸形）
# ---------------------------------------------------------------------------


def test_error_code_outside_i64_is_malformed():
    """error.code 超 i64 即畸形拒收（与 _is_valid_request_id 同纪律）；
    边界值 ±(2^63-1)/-2^63 照常放行"""
    from nova_exec_server_client.transport import _gate_inbound_message

    def gate(code):
        return _gate_inbound_message(
            json.dumps({"id": 1, "error": {"code": code, "message": "x"}}).encode()
        )

    message, fatal = gate(2**63 - 1)
    assert fatal is None and message["error"]["code"] == 2**63 - 1
    message, fatal = gate(-(2**63))
    assert fatal is None and message["error"]["code"] == -(2**63)
    _, fatal = gate(2**63)
    assert fatal == "malformed JSON-RPC message from exec-server"
    _, fatal = gate(-(2**63) - 1)
    assert fatal == "malformed JSON-RPC message from exec-server"


# ---------------------------------------------------------------------------
# B1 Windows 进程树收割（对位 Rust connection.rs 的 kill_windows_process_tree）
# ---------------------------------------------------------------------------


def test_windows_taskkill_command_shape(monkeypatch):
    """taskkill 命令形态对位 Rust：/PID <pid> /T /F，stdio 全置 null；
    退出码成功返回 True，失败/OSError 返回 False（调用方落直接子兜底）"""
    import nova_exec_server_client.transport as transport_module

    recorded = {}

    def fake_run(cmd, **kwargs):
        recorded["cmd"] = cmd
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert transport_module._kill_windows_process_tree(4242) is True
    assert recorded["cmd"] == ["taskkill", "/PID", "4242", "/T", "/F"]
    assert recorded["kwargs"]["stdin"] is subprocess.DEVNULL
    assert recorded["kwargs"]["stdout"] is subprocess.DEVNULL
    assert recorded["kwargs"]["stderr"] is subprocess.DEVNULL

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1),
    )
    assert transport_module._kill_windows_process_tree(4242) is False

    def raising_run(cmd, **kwargs):
        raise OSError("taskkill missing")

    monkeypatch.setattr(subprocess, "run", raising_run)
    assert transport_module._kill_windows_process_tree(4242) is False


def test_process_tree_reap_prefers_taskkill_on_windows(monkeypatch):
    """Windows 分支：taskkill 成功即整树收割、不落直接子；失败才落直接子
    （terminate/kill 两方法同路径——对位 Rust 两个 cfg(windows) 分支）"""
    import nova_exec_server_client.transport as transport_module

    class _Proc:
        pid = 4242
        terminated = False
        killed = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(
        transport_module, "_kill_windows_process_tree", lambda pid: True
    )
    proc = _Proc()
    StdioTransport._terminate_process_tree(proc)
    assert not proc.terminated  # taskkill 已收整树，不补直接子
    StdioTransport._kill_process_tree(proc)
    assert not proc.killed

    monkeypatch.setattr(
        transport_module, "_kill_windows_process_tree", lambda pid: False
    )
    proc = _Proc()
    StdioTransport._terminate_process_tree(proc)
    assert proc.terminated  # taskkill 失败落直接子兜底
    proc = _Proc()
    StdioTransport._kill_process_tree(proc)
    assert proc.killed


# ---------------------------------------------------------------------------
# B2 stdio 64MiB + CRLF 边界（StreamReader 行限 max+2，对位 Rust
# connection.rs 的 max+1+CRLF 前瞻）
# ---------------------------------------------------------------------------

#: 定长响应服务端：回一行恰好 params.size 字节的 JSON 响应 + CRLF 终止
_PAD_RESPONSE_SERVER = r"""
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    size = int(msg.get("params", {}).get("size", 0))
    prefix = json.dumps({"id": msg["id"], "result": {"pad": ""}}).encode()[:-3]
    sys.stdout.buffer.write(
        prefix + b"x" * (size - len(prefix) - 3) + b'"}}' + b"\r\n"
    )
    sys.stdout.buffer.flush()
"""


def _expected_pad_len(size: int) -> int:
    """与服务端同式：size 字节行内填充长度（钉死凑长算术）"""
    prefix = json.dumps({"id": 1, "result": {"pad": ""}}).encode()[:-3]
    return size - len(prefix) - 3


@pytest.mark.asyncio
async def test_max_size_message_with_crlf_accepted():
    """恰好 64MiB 的消息带 CRLF 终止照常收下（行限 +2 的 CRLF 前瞻余量；
    旧 max+0 行限会在分隔符前 LimitOverrun 误杀边界消息）"""
    transport = StdioTransport(
        program=sys.executable,
        args=["-c", _PAD_RESPONSE_SERVER],
        request_timeout=15.0,
    )
    await transport.connect()
    try:
        result = await transport.send_request(
            "pad", {"size": transport.max_message_size}
        )
        assert result["pad"] == "x" * _expected_pad_len(transport.max_message_size)
        assert transport.is_connected
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_over_max_size_message_rejected():
    """上限 +1 字节即拒（读回后的 len 检查不变）——断开带长度原因"""
    transport = StdioTransport(
        program=sys.executable,
        args=["-c", _PAD_RESPONSE_SERVER],
        request_timeout=5.0,
        max_message_size=16 * 1024,
    )
    reasons: list[str | None] = []
    transport.on_disconnect(reasons.append)
    await transport.connect()
    try:
        with pytest.raises(ConnectionError, match="connection closed"):
            await transport.send_request("pad", {"size": 16 * 1024 + 1})
        for _ in range(50):
            if not transport.is_connected:
                break
            await asyncio.sleep(0.02)
        assert not transport.is_connected
        assert reasons and "exceeds maximum length of 16384 bytes" in (reasons[0] or "")
    finally:
        await transport.disconnect()
