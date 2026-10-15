"""process 管理器行为测试：

- C6：process/start 参数上线形态（metadata 归因字段，对位 Rust
  `ExecParams.metadata`，v1.7 起 executor 遥测归因用）；
- 传输关闭重试臂（对位 Rust Session::read/write 的
  is_transport_closed_error 臂）：read 带原 afterSeq、write 同一 writeId
  重试，控制面终态（failed/disconnected）不重试；
- 活进程登记表（对位 Rust Inner.sessions 会话表）：start 登记
  policyDecisionTimeoutMs、read 见 exited / terminate 移除；
- ProcessOutput 透传 failure/sandbox_denied。
"""

import pytest
from fake_transport import FakeTransport
from nova_protocol import ExecMetadata

from nova_exec_server_client import ConnectionError, ExecutorClient, ProtocolError
from nova_exec_server_client.process import ProcessManager

INIT_OK = {"sessionId": "fake-session", "protocolVersion": "1.13"}


def make_process() -> tuple[ProcessManager, FakeTransport]:
    transport = FakeTransport({"process/start": {"processId": "p-1"}})
    return ProcessManager(transport), transport


@pytest.mark.asyncio
async def test_start_metadata_goes_online_camel_case():
    """metadata 上线：{"metadata": {"threadId": ..., "toolCallId": ...}}"""
    process, transport = make_process()

    handle = await process.start(
        ["bash", "-c", "true"],
        "/tmp",
        metadata=ExecMetadata(threadId="t-1", toolCallId="call-9"),
    )

    assert handle.process_id == "p-1"
    _, params, _ = transport.requests[0]
    assert params["metadata"] == {"threadId": "t-1", "toolCallId": "call-9"}


@pytest.mark.asyncio
async def test_start_metadata_partial_and_default_omitted():
    """部分字段：None 项不进 metadata 载荷；缺省 None：整个 "metadata" 键省略"""
    process, transport = make_process()
    await process.start(["true"], "/tmp", metadata=ExecMetadata(threadId="t-2"))
    _, params, _ = transport.requests[0]
    assert params["metadata"] == {"threadId": "t-2"}

    process, transport = make_process()
    await process.start(["true"], "/tmp")
    _, params, _ = transport.requests[0]
    assert "metadata" not in params


# ---------------------------------------------------------------------------
# 传输关闭重试臂（对位 Rust Session::read/write 的 is_transport_closed_error 臂）
# ---------------------------------------------------------------------------

READ_OK = {
    "chunks": [{"seq": 1, "stream": "stdout", "chunk": "aGk="}],
    "nextSeq": 2,
    "exited": True,
    "exitCode": 0,
    "closed": True,
}


class FlakyReadTransport(FakeTransport):
    """process/read 首发以传输错误判死（断线瞬间在飞 read 的重试测试用）"""

    def __init__(self, responses=None):
        super().__init__(responses)
        self.read_calls = 0
        #: 含失败尝试的全部 read 参数（FakeTransport.requests 只录成功路径）
        self.read_params: list[dict] = []

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False
    ):
        if method == "process/read":
            self.read_calls += 1
            self.read_params.append(params or {})
            if self.read_calls == 1:
                raise ConnectionError("connection closed")
        return await super().send_request(method, params, channel=channel)


@pytest.mark.asyncio
async def test_read_retries_transport_closed_with_same_after_seq():
    """断线瞬间在飞 read 被判死 → 控制面未终态时带原 afterSeq 重试
    （对位 Rust Session::read 的 continue 臂——恢复成功不丢输出）"""
    transport = FlakyReadTransport({"initialize": INIT_OK, "process/read": READ_OK})
    client = ExecutorClient(transport=transport)
    await client.connect()

    output = await client.process.read("p-1", after_seq=41)
    assert output.exited and output.chunks == [b"hi"]
    assert transport.read_calls == 2
    assert [p["afterSeq"] for p in transport.read_params] == [
        41,
        41,
    ]  # 重试带原 afterSeq
    await client.disconnect()


@pytest.mark.asyncio
async def test_read_retries_transport_closed_server_error():
    """服务端 RPC 层传输关闭（-32000 "JSON-RPC transport closed"）同传输类
    重试（判别同恢复层 is_transport_class_error）"""
    calls = 0

    class TransportClosedOnceTransport(FakeTransport):
        async def send_request(
            self, method, params=None, *, channel=None, for_cleanup=False
        ):
            nonlocal calls
            if method == "process/read":
                calls += 1
                if calls == 1:
                    raise ProtocolError(
                        "JSON-RPC error -32000: JSON-RPC transport closed",
                        code=-32000,
                        server_message="JSON-RPC transport closed",
                    )
            return await super().send_request(method, params, channel=channel)

    transport = TransportClosedOnceTransport(
        {"initialize": INIT_OK, "process/read": READ_OK}
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    output = await client.process.read("p-1")
    assert output.exited
    assert calls == 2
    await client.disconnect()


class DropOnReadTransport(FakeTransport):
    """process/read 模拟断线窗口失败：触发断线回调后以传输错误收尾
    （控制面转 failed 后不重试测试用）"""

    def __init__(self, responses=None):
        super().__init__(responses)
        self.read_calls = 0

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False
    ):
        if method == "process/read":
            self.read_calls += 1
            self.drop("read dropped")
            raise ConnectionError("connection closed")
        return await super().send_request(method, params, channel=channel)


@pytest.mark.asyncio
async def test_read_not_retried_when_control_failed():
    """控制面到 failed 终态：传输错误上抛且不再触碰底层传输（对位 Rust
    is_transport_closed_error && !is_failed() 的否定臂——无恢复手段时
    重试无意义）"""
    transport = DropOnReadTransport({"initialize": INIT_OK, "process/read": READ_OK})
    client = ExecutorClient(transport=transport, reconnect=None)
    await client.connect()

    with pytest.raises(ConnectionError):
        await client.process.read("p-1")
    assert transport.read_calls == 1  # 终态不重试：底层只见一次 read
    assert client._control.state == "failed"
    await client.disconnect()


class FlakyWriteTransport(FakeTransport):
    """process/write 首发以传输错误判死（writeId 固定重试测试用）"""

    def __init__(self, responses=None):
        super().__init__(responses)
        self.write_calls = 0
        #: 含失败尝试的全部 write 参数（FakeTransport.requests 只录成功路径）
        self.write_params: list[dict] = []

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False
    ):
        if method == "process/write":
            self.write_calls += 1
            self.write_params.append(params or {})
            if self.write_calls == 1:
                raise ConnectionError("connection closed")
        return await super().send_request(method, params, channel=channel)


@pytest.mark.asyncio
async def test_write_retries_with_same_write_id():
    """断线瞬间在飞 write 被判死 → 单次逻辑写内固定 writeId 重试（服务端
    writeId 去重保证重试不双写——对位 Rust Session::write 的循环）"""
    transport = FlakyWriteTransport(
        {"initialize": INIT_OK, "process/write": {"status": "accepted"}}
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    await client.process.write("p-1", b"input")
    assert transport.write_calls == 2
    assert transport.write_params[0]["writeId"] == transport.write_params[1]["writeId"]
    await client.disconnect()


@pytest.mark.asyncio
async def test_iter_output_retries_and_keeps_every_chunk():
    """iter_output 共享重试臂：首块拉取被判死重试后不丢输出，exited 收尾"""
    transport = FlakyReadTransport(
        {
            "initialize": INIT_OK,
            "process/read": {
                "chunks": [{"seq": 1, "stream": "stdout", "chunk": "aGk="}],
                "nextSeq": 2,
                "exited": True,
                "exitCode": 0,
                "closed": True,
            },
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    chunks = [c async for c in client.process.iter_output("p-1")]
    assert chunks == [b"hi"]
    assert transport.read_calls == 2
    await client.disconnect()


# ---------------------------------------------------------------------------
# 活进程登记表（对位 Rust Inner.sessions 会话表）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_registry_lifecycle():
    """start 成功登记（含 policyDecisionTimeoutMs 覆盖）；read 见 exited
    或 terminate 后移除"""
    transport = FakeTransport(
        {
            "initialize": INIT_OK,
            "process/start": {"processId": "p-1"},
            "process/read": READ_OK,
            "process/terminate": {"running": False},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    handle = await client.process.start(
        ["cat"],
        "/tmp",
        network_proxy={"proxy": {}, "policyDecisionTimeoutMs": 5000},
    )
    assert client.process.live_processes == {"p-1": 5.0}

    await handle.read()
    assert client.process.live_processes == {}  # exited → 移除

    handle2 = await client.process.start(["cat"], "/tmp")
    assert "p-1" in client.process.live_processes
    assert client.process.live_processes["p-1"] is None  # 未声明 → 回退全局
    await handle2.terminate()
    assert client.process.live_processes == {}  # terminate → 移除
    await client.disconnect()


@pytest.mark.asyncio
async def test_process_output_carries_failure_and_sandbox_denied():
    """ProcessOutput 透传 failure/sandbox_denied（协议模型已有字段）"""
    transport = FakeTransport(
        {
            "initialize": INIT_OK,
            "process/read": {
                "chunks": [],
                "nextSeq": 1,
                "exited": True,
                "exitCode": 1,
                "closed": True,
                "failure": "sandbox denied execution",
                "sandboxDenied": True,
            },
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    output = await client.process.read("p-1")
    assert output.failure == "sandbox denied execution"
    assert output.sandbox_denied is True
    # 缺省回填（旧服务端不带字段）
    transport.responses["process/read"] = READ_OK
    output = await client.process.read("p-1")
    assert output.failure is None and output.sandbox_denied is False
    await client.disconnect()
