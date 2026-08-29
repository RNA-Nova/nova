"""fs.read_stream 单元测试：统一分发接入、done 收尾校验（error/totalBytes）、背压断流"""

from __future__ import annotations

import asyncio
import base64

import pytest
from fake_transport import FakeTransport

from nova_executor_client import ExecutorClient, FileSystemError
from nova_executor_client.fs import FileSystemManager


def chunk_msg(handle: str, seq: int, data: bytes, eof: bool = False) -> dict:
    return {
        "method": "fs/readStream/chunk",
        "params": {
            "handleId": handle,
            "seq": seq,
            "chunk": base64.b64encode(data).decode(),
            "eof": eof,
        },
    }


def done_msg(handle: str, total: int, error: str | None = None) -> dict:
    params = {"handleId": handle, "totalBytes": total}
    if error is not None:
        params["error"] = error
    return {"method": "fs/readStream/done", "params": params}


def make_fs() -> tuple[FileSystemManager, FakeTransport]:
    transport = FakeTransport({"fs/readStream": {"handleId": "any", "totalSize": 3}})
    return FileSystemManager(transport), transport


async def start_stream(fs: FileSystemManager, transport: FakeTransport, **kwargs):
    """驱动异步生成器发出 fs/readStream 请求，返回 (生成器, handle_id, 首块任务)"""
    agen = fs.read_stream("file:///tmp/x", **kwargs)
    first = asyncio.create_task(agen.__anext__())
    for _ in range(50):
        if transport.requests:
            break
        await asyncio.sleep(0.01)
    assert transport.requests, "fs/readStream 请求未发出"
    method, params, _ = transport.requests[0]
    assert method == "fs/readStream"
    assert params["handleId"].startswith("s-")
    return agen, params["handleId"], first


@pytest.mark.asyncio
async def test_read_stream_happy_path():
    """逐块产出字节，done 收齐校验通过即结束"""
    fs, transport = make_fs()
    agen, handle_id, first = await start_stream(fs, transport)

    await transport.handlers[0](chunk_msg(handle_id, 0, b"hel"))
    assert await first == b"hel"

    second = asyncio.create_task(agen.__anext__())
    await transport.handlers[0](chunk_msg(handle_id, 1, b"lo", eof=True))
    assert await second == b"lo"

    # done 收尾：totalBytes 与实收一致 → 生成器正常结束
    await transport.handlers[0](done_msg(handle_id, 5))
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()


@pytest.mark.asyncio
async def test_read_stream_done_error_raises():
    """done 携带 error → FileSystemError（旧版静默吞掉的缺陷已修）"""
    fs, transport = make_fs()
    agen, handle_id, first = await start_stream(fs, transport)

    await transport.handlers[0](chunk_msg(handle_id, 0, b"ab"))
    assert await first == b"ab"
    await transport.handlers[0](done_msg(handle_id, 2, error="disk error"))
    with pytest.raises(FileSystemError, match="disk error"):
        await agen.__anext__()


@pytest.mark.asyncio
async def test_read_stream_total_bytes_mismatch_raises():
    """done 的 totalBytes 与实收不齐 → FileSystemError（丢块/截断不静默）。

    同时钉死语义：eof=True 块不再提前结束流——收尾只认 done。
    """
    fs, transport = make_fs()
    agen, handle_id, first = await start_stream(fs, transport)

    await transport.handlers[0](chunk_msg(handle_id, 0, b"ab", eof=True))
    assert await first == b"ab"
    # 服务端实发 10 字节，客户端只收到 2（断线丢块场景）
    await transport.handlers[0](done_msg(handle_id, 10))
    with pytest.raises(FileSystemError, match="incomplete"):
        await agen.__anext__()


@pytest.mark.asyncio
async def test_read_stream_open_failure_unregisters():
    """开流请求失败：路由注销不残留（对位 Rust open_push 失败清理）"""
    transport = FakeTransport({"fs/readStream": FileSystemErrorStub("no such file")})
    fs = FileSystemManager(transport)

    with pytest.raises(FileSystemErrorStub):
        async for _ in fs.read_stream("file:///tmp/missing"):
            pass
    assert fs._router._streams == {}


@pytest.mark.asyncio
async def test_read_stream_consumer_abandon_cleans_up():
    """消费者中途放弃（break/aclose）：路由注销，后续通知按未知句柄丢弃"""
    fs, transport = make_fs()
    agen, handle_id, first = await start_stream(fs, transport)

    await transport.handlers[0](chunk_msg(handle_id, 0, b"x"))
    assert await first == b"x"
    await agen.aclose()
    assert handle_id not in fs._router._streams
    # 迟到的通知静默丢弃，不炸
    await transport.handlers[0](chunk_msg(handle_id, 1, b"y"))
    await transport.handlers[0](done_msg(handle_id, 2))


@pytest.mark.asyncio
async def test_read_stream_connection_failed_fails_consumer():
    """连接恢复失败 → 挂起消费者收 FileSystemError（而非干等）"""
    fs, transport = make_fs()
    agen, handle_id, first = await start_stream(fs, transport)

    fs._router.fail_channel(None, "exec-server transport disconnected")
    with pytest.raises(FileSystemError, match="disconnected"):
        await first


@pytest.mark.asyncio
async def test_read_stream_via_client_survives_data_channel_reconnect():
    """端到端：connections=2 客户端，数据面断线重连带 resumeSessionId，
    注册表跨重连存活，重连后推送继续路由到同一消费队列"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport(
            {
                "initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"},
                "fs/readStream": {"handleId": "any", "totalSize": 4},
            }
        )
        sink.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory, connections=2)
    await client.connect()
    try:
        control, data = sink
        agen = client.fs.read_stream("file:///tmp/x")
        first = asyncio.create_task(agen.__anext__())
        stream_requests = []
        for _ in range(50):
            stream_requests = [r for r in data.requests if r[0] == "fs/readStream"]
            if stream_requests:
                break
            await asyncio.sleep(0.01)
        assert stream_requests, "fs/readStream 未落到数据面连接"
        handle_id = stream_requests[0][1]["handleId"]

        await data.handlers[0](chunk_msg(handle_id, 0, b"ab"))
        assert await first == b"ab"

        # 数据面连接断开 → 恢复：新传输重连 + initialize 带 resumeSessionId
        next_anext = asyncio.create_task(agen.__anext__())
        data.drop("test drop")
        for _ in range(100):
            if len(sink) >= 3 and sink[2].connected and sink[2].requests:
                break
            await asyncio.sleep(0.01)
        resumed = sink[2]
        assert resumed.requests[0][0] == "initialize"
        assert resumed.requests[0][1]["resumeSessionId"] == "fake-session"

        # 服务端 resume 后把推送重定向到新连接：同一消费队列继续收件
        await resumed.handlers[0](chunk_msg(handle_id, 1, b"cd", eof=True))
        assert await next_anext == b"cd"
        await resumed.handlers[0](done_msg(handle_id, 4))
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
    finally:
        await client.disconnect()


class FileSystemErrorStub(Exception):
    pass
