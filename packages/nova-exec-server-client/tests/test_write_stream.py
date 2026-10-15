"""fs.write_stream 单元测试（FakeTransport 录制协议流量，验证请求/通知/结束语义）"""

from __future__ import annotations

import asyncio
import base64

import pytest
from fake_transport import FakeTransport
from nova_protocol import MAX_WRITE_STREAM_CHUNK_BYTES, FileSystemSandboxContext

from nova_exec_server_client import FileSystemError, ProtocolError
from nova_exec_server_client.fs import FileSystemManager


def make_fs(responses: dict | None = None) -> tuple[FileSystemManager, FakeTransport]:
    transport = FakeTransport(responses)
    return FileSystemManager(transport), transport


def ok_responses(total: int) -> dict:
    return {
        "fs/writeStream": {"handleId": "any"},
        "fs/writeStream/done": {"handleId": "any", "totalBytes": total},
        "fs/close": {},
    }


async def wait_close_requests(transport: FakeTransport) -> list:
    """死线轮询即发即忘的 fs/close 中止任务落地（对位 Rust runtime.spawn——
    inline await 会把异常/取消路径挂在半死连接上，故中止不随行内等待）"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    close_requests = [r for r in transport.requests if r[0] == "fs/close"]
    while not close_requests:
        assert loop.time() < deadline, "fs/close 中止任务 5s 内未落地"
        await asyncio.sleep(0.01)
        close_requests = [r for r in transport.requests if r[0] == "fs/close"]
    return close_requests


@pytest.mark.asyncio
async def test_write_stream_happy_path_chunking_and_seq():
    """分片切块、seq 严格序、空块 eof 收尾、done 返回总字节数"""
    fs, transport = make_fs(ok_responses(total=11))

    total = await fs.write_stream(
        "file:///tmp/out.bin", [b"hello ", b"world"], block_size=3
    )

    assert total == 11
    # 开句柄 + done 两个请求
    methods = [m for m, _, _ in transport.requests]
    assert methods == ["fs/writeStream", "fs/writeStream/done"]
    open_params = transport.requests[0][1]
    assert open_params["path"] == "file:///tmp/out.bin"
    assert open_params["handleId"].startswith("w-")
    assert transport.requests[1][1]["handleId"] == open_params["handleId"]

    # chunk 通知：hel/lo_/wor/ld 四块 + 空 eof 块
    chunks = [p for m, p, _ in transport.notifications if m == "fs/writeStream/chunk"]
    assert len(chunks) == 5
    payload = b""
    for i, chunk in enumerate(chunks[:-1]):
        assert chunk["handleId"] == open_params["handleId"]
        assert chunk["seq"] == i
        assert chunk["eof"] is False
        payload += base64.b64decode(chunk["chunk"])
    assert payload == b"hello world"
    assert chunks[-1]["seq"] == 4
    assert chunks[-1]["eof"] is True
    assert base64.b64decode(chunks[-1]["chunk"]) == b""


@pytest.mark.asyncio
async def test_write_stream_async_iterable_source():
    """异步字节源同样可用"""
    fs, transport = make_fs(ok_responses(total=6))

    async def source():
        for piece in (b"foo", b"bar"):
            yield piece

    total = await fs.write_stream("file:///tmp/out.bin", source())
    assert total == 6
    chunks = [p for m, p, _ in transport.notifications if m == "fs/writeStream/chunk"]
    assert [base64.b64decode(c["chunk"]) for c in chunks[:-1]] == [b"foo", b"bar"]


@pytest.mark.asyncio
async def test_write_stream_empty_source_writes_empty_file():
    """空字节源：仅一个 eof 空块，done 返回 0"""
    fs, transport = make_fs(ok_responses(total=0))

    total = await fs.write_stream("file:///tmp/empty.bin", [])
    assert total == 0
    chunks = [p for m, p, _ in transport.notifications if m == "fs/writeStream/chunk"]
    assert len(chunks) == 1
    assert chunks[0]["seq"] == 0 and chunks[0]["eof"] is True


@pytest.mark.asyncio
async def test_write_stream_sandbox_passes_through():
    """sandbox 透传：writeStream 开门参数携带按请求沙箱载荷（v1.10 起服务端
    按请求沙箱开门写）；缺省 None 时与现状一致（"sandbox": None 上线）"""
    sandbox = FileSystemSandboxContext.read_only("/tmp/proj").model_dump(
        by_alias=True, exclude_none=True
    )

    fs, transport = make_fs(ok_responses(total=1))
    await fs.write_stream("file:///tmp/out.bin", [b"x"], sandbox=sandbox)
    assert transport.requests[0][1]["sandbox"] == sandbox

    # 缺省（不下发沙箱）：同现状——模型 dump 恒含 "sandbox": None
    fs, transport = make_fs(ok_responses(total=1))
    await fs.write_stream("file:///tmp/out.bin", [b"x"])
    assert transport.requests[0][1]["sandbox"] is None


@pytest.mark.asyncio
async def test_write_stream_rejects_plain_bytes():
    """整块 bytes 是误用（应走 write_file）"""
    fs, _ = make_fs()
    with pytest.raises(TypeError, match="write_file"):
        await fs.write_stream("file:///tmp/out.bin", b"whole")


@pytest.mark.asyncio
async def test_write_stream_block_size_validation():
    """block_size 上限对齐服务端单块上限"""
    fs, _ = make_fs()
    with pytest.raises(ValueError, match="block_size"):
        await fs.write_stream("file:///tmp/out.bin", [b"x"], block_size=0)
    with pytest.raises(ValueError, match="block_size"):
        await fs.write_stream(
            "file:///tmp/out.bin",
            [b"x"],
            block_size=MAX_WRITE_STREAM_CHUNK_BYTES + 1,
        )


@pytest.mark.asyncio
async def test_write_stream_done_error_becomes_filesystem_error():
    """服务端业务错误在 done 回报（chunk 通知无回执），转为 FileSystemError"""
    responses = ok_responses(total=0)
    responses["fs/writeStream/done"] = ProtocolError(
        "JSON-RPC error -32603: file write stream `x` expected seq 1, got 2"
    )
    fs, _ = make_fs(responses)

    with pytest.raises(FileSystemError, match="expected seq"):
        await fs.write_stream("file:///tmp/out.bin", [b"ab"])


@pytest.mark.asyncio
async def test_write_stream_local_abort_sends_close_on_stream_channel():
    """字节源中途抛错：发 fs/close 中止（落点与开流同一通道解析——裸传输
    无通道概念归 None；连接池自定义路由见读侧对应用例），原异常传播。
    中止为即发即忘形态（与 read_stream 收摊同纪律）"""
    fs, transport = make_fs(ok_responses(total=0))

    async def broken_source():
        yield b"partial"
        raise RuntimeError("source exploded")

    with pytest.raises(RuntimeError, match="source exploded"):
        await fs.write_stream("file:///tmp/out.bin", broken_source())

    close_calls = await wait_close_requests(transport)
    assert len(close_calls) == 1
    assert close_calls[0][2] is None
    assert "fs/writeStream/done" not in [m for m, _, _ in transport.requests]


@pytest.mark.asyncio
async def test_write_stream_cancellation_aborts_handle():
    """上传中途取消（CancelledError 非 Exception 子类——except 形态漏捕会把
    写句柄连同 fd/槽泄漏在服务端干等）：取消照样发 fs/close 中止"""
    fs, transport = make_fs(ok_responses(total=0))
    gate = asyncio.Event()

    async def stalled_source():
        yield b"partial"
        await gate.wait()  # 永不放行——等待取消
        yield b"more"

    task = asyncio.create_task(fs.write_stream("file:///tmp/out.bin", stalled_source()))
    # 等首块 chunk 通知落地（写流确已开句柄并开推）
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while not transport.notifications:
        assert loop.time() < deadline, "首块 chunk 通知 5s 内未落地"
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    close_calls = await wait_close_requests(transport)
    assert len(close_calls) == 1
    assert close_calls[0][1]["handleId"].startswith("w-")
    assert "fs/writeStream/done" not in [m for m, _, _ in transport.requests]


@pytest.mark.asyncio
async def test_write_stream_abort_failure_is_best_effort(caplog):
    """中止时连接已死：close 失败被吞掉（即发即忘任务的异常取回留痕，不打
    "Task exception was never retrieved" 循环告警），原异常照常传播"""
    responses = ok_responses(total=0)
    responses["fs/close"] = ConnectionErrorStub("connection closed")
    fs, transport = make_fs(responses)

    def broken_source():
        yield b"partial"
        raise RuntimeError("boom")

    with caplog.at_level("DEBUG", logger="nova_exec_server_client.fs"):
        with pytest.raises(RuntimeError, match="boom"):
            await fs.write_stream("file:///tmp/out.bin", broken_source())
        close_calls = await wait_close_requests(transport)
        # 等即发即忘任务完成（异常在 done_callback 取回留痕）
        deadline = asyncio.get_running_loop().time() + 5.0
        while fs._pending_closes:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
    assert close_calls
    assert "fs/close 收摊任务失败" in caplog.text


class ConnectionErrorStub(Exception):
    pass
