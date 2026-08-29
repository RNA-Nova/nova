"""fs.write_stream 单元测试（FakeTransport 录制协议流量，验证请求/通知/结束语义）"""

from __future__ import annotations

import base64

import pytest
from fake_transport import FakeTransport

from nova_executor_client import FileSystemError, ProtocolError
from nova_executor_client.fs import FileSystemManager
from nova_executor_client.pool import CHANNEL_DATA
from nova_executor_client.protocol import MAX_WRITE_STREAM_CHUNK_BYTES


def make_fs(responses: dict | None = None) -> tuple[FileSystemManager, FakeTransport]:
    transport = FakeTransport(responses)
    return FileSystemManager(transport), transport


def ok_responses(total: int) -> dict:
    return {
        "fs/writeStream": {"handleId": "any"},
        "fs/writeStream/done": {"handleId": "any", "totalBytes": total},
        "fs/close": {},
    }


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
async def test_write_stream_local_abort_sends_close_on_data_channel():
    """字节源中途抛错：发 fs/close 中止（数据面通道），原异常传播"""
    fs, transport = make_fs(ok_responses(total=0))

    async def broken_source():
        yield b"partial"
        raise RuntimeError("source exploded")

    with pytest.raises(RuntimeError, match="source exploded"):
        await fs.write_stream("file:///tmp/out.bin", broken_source())

    # close 中止请求落在数据面通道（句柄状态随连接），且没有 done
    close_calls = [(m, p, c) for m, p, c in transport.requests if m == "fs/close"]
    assert len(close_calls) == 1
    assert close_calls[0][2] == CHANNEL_DATA
    assert "fs/writeStream/done" not in [m for m, _, _ in transport.requests]


@pytest.mark.asyncio
async def test_write_stream_abort_failure_is_best_effort():
    """中止时连接已死：close 失败被吞掉，原异常照常传播"""
    responses = ok_responses(total=0)
    responses["fs/close"] = ConnectionErrorStub("connection closed")
    fs, transport = make_fs(responses)

    def broken_source():
        yield b"partial"
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await fs.write_stream("file:///tmp/out.bin", broken_source())
    assert any(m == "fs/close" for m, _, _ in transport.requests)


class ConnectionErrorStub(Exception):
    pass
