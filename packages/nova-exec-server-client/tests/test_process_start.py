"""process/start 参数上线形态测试（C6——metadata 归因字段，对位 Rust
`ExecParams.metadata`，v1.7 起 executor 遥测归因用）：

- `metadata` kwarg 上线键名 "metadata"，内部 camelCase（threadId/toolCallId）；
- 缺省 None 时经 exclude_none 省略该键（旧客户端省略、旧 executor 忽略）。
"""

import pytest
from fake_transport import FakeTransport
from nova_protocol import ExecMetadata

from nova_exec_server_client.process import ProcessManager


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
