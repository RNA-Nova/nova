"""NotificationRouter 统一通知分发测试（对位 Rust Inner 注册表分发语义）"""

from __future__ import annotations

import asyncio
import base64

import pytest

from nova_exec_server_client.notifications import (
    READ_STREAM_QUEUE_CAPACITY,
    NotificationRouter,
)


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


@pytest.mark.asyncio
async def test_dispatch_routes_by_handle_id():
    """一个通知只叫醒该醒的消费者：两条流各自收件，互不串扰"""
    router = NotificationRouter()
    qa = router.register_stream("h-a")
    qb = router.register_stream("h-b")

    await router.dispatch(chunk_msg("h-a", 0, b"aaa"))
    await router.dispatch(chunk_msg("h-b", 0, b"bbb"))

    assert qa.get_nowait().chunk == b"aaa"
    assert qb.get_nowait().chunk == b"bbb"
    assert qa.empty() and qb.empty()


@pytest.mark.asyncio
async def test_unknown_handle_is_dropped_not_error():
    """未知句柄（流已结束/已放弃）丢弃，不算错误"""
    router = NotificationRouter()
    await router.dispatch(chunk_msg("ghost", 0, b"x"))
    await router.dispatch(done_msg("ghost", 0))  # 不抛异常即正确


@pytest.mark.asyncio
async def test_unknown_method_is_ignored():
    """未知方法 debug 忽略（对位 Rust ignoring unknown notification）"""
    router = NotificationRouter()
    await router.dispatch({"method": "process/output", "params": {}})


@pytest.mark.asyncio
async def test_malformed_notification_warns_not_raises(caplog):
    """畸形载荷留痕不炸分发（接收循环保护）；提不出 handleId 时 fail 全表
    （向 Rust 严格姿态靠拢的兜底——不许静默丢弃让消费者干等）"""
    router = NotificationRouter()
    qa = router.register_stream("h-a")
    with caplog.at_level("WARNING", logger="nova_exec_server_client.notifications"):
        await router.dispatch(
            {"method": "fs/readStream/chunk", "params": {"handleId": 123}}
        )
    assert "failed to dispatch" in caplog.text
    # handleId 非字符串提不出 → 全表合成 failed 终态（本地断流，server_settled=False）
    event = await asyncio.wait_for(qa.get(), 1)
    assert event.kind == "failed"
    assert "malformed" in (event.error or "")
    assert event.server_settled is False
    assert "h-a" not in router._streams


@pytest.mark.asyncio
async def test_malformed_chunk_with_extractable_handle_fails_only_that_stream():
    """畸形 chunk 能提出 handleId：只 fail 该流（路由摘除 + 合成 failed 终态），
    其余流不受影响（对位 Rust 严格姿态的 SDK 从宽形态）"""
    router = NotificationRouter()
    qa = router.register_stream("h-a")
    qb = router.register_stream("h-b")

    # seq/chunk/eof 缺失 → 校验失败，但 handleId 可提取
    await router.dispatch(
        {"method": "fs/readStream/chunk", "params": {"handleId": "h-a"}}
    )

    event = await asyncio.wait_for(qa.get(), 1)
    assert event.kind == "failed"
    assert "malformed" in (event.error or "")
    assert event.server_settled is False
    assert "h-a" not in router._streams
    # 其余流不受影响：路由在、可正常收件
    assert "h-b" in router._streams
    await router.dispatch(chunk_msg("h-b", 0, b"ok"))
    assert (await asyncio.wait_for(qb.get(), 1)).chunk == b"ok"


@pytest.mark.asyncio
async def test_malformed_done_with_extractable_handle_fails_stream():
    """畸形 done（totalBytes 缺失）同样按 handleId fail 该流——消费者收终态
    而非干等（路由注册不再永不摘除）"""
    router = NotificationRouter()
    queue = router.register_stream("h-d")
    await router.dispatch(
        {"method": "fs/readStream/done", "params": {"handleId": "h-d"}}
    )
    event = await asyncio.wait_for(queue.get(), 1)
    assert event.kind == "failed" and "malformed" in (event.error or "")
    assert "h-d" not in router._streams


@pytest.mark.asyncio
async def test_done_with_error_becomes_failed_event():
    """done 携带 error → Failed 事件（旧版静默吞掉的缺陷已修）；服务端终态
    server_settled=True（服务端已自行收摊，消费者不得再补 fs/close）"""
    router = NotificationRouter()
    queue = router.register_stream("h-e")
    await router.dispatch(done_msg("h-e", 3, error="disk error"))
    event = await queue.get()
    assert event.kind == "failed"
    assert event.error == "disk error"
    assert event.server_settled is True


@pytest.mark.asyncio
async def test_done_carries_total_bytes():
    """done 无 error → Done 事件携带 totalBytes（消费者校验收齐用）"""
    router = NotificationRouter()
    queue = router.register_stream("h-d")
    await router.dispatch(chunk_msg("h-d", 0, b"ab"))
    await router.dispatch(done_msg("h-d", 2))
    assert (await queue.get()).kind == "chunk"
    event = await queue.get()
    assert event.kind == "done" and event.total_bytes == 2


@pytest.mark.asyncio
async def test_backpressure_slow_consumer_breaks_stream():
    """背压：队列满宁可断流（Failed 事件）也不阻塞连接级分发；断流终态
    满员转异步投递排队等位——已排队数据块一个不丢（对位 Rust
    finish_fs_read_stream 满员 spawn send）"""
    router = NotificationRouter()
    queue = router.register_stream("h-slow")
    # 填满队列（不消费）
    for seq in range(READ_STREAM_QUEUE_CAPACITY):
        await router.dispatch(chunk_msg("h-slow", seq, b"x"))
    # 再来一块 → 断流：注册被移除；失败终态异步等位，不挤掉已排数据块
    await router.dispatch(chunk_msg("h-slow", READ_STREAM_QUEUE_CAPACITY, b"x"))
    events = [queue.get_nowait() for _ in range(READ_STREAM_QUEUE_CAPACITY)]
    assert all(event.kind == "chunk" for event in events)
    terminal = await asyncio.wait_for(queue.get(), 1)
    assert terminal.kind == "failed"
    assert "too slow" in terminal.error
    # 背压断流是本地合成终态（server_settled=False）——服务端读句柄仍活，
    # 消费者须按未收摊路径补发 fs/close（对位 Rust PushReadRegistration::drop）
    assert terminal.server_settled is False
    # 断流后该句柄通知一律丢弃
    await router.dispatch(done_msg("h-slow", 999))


@pytest.mark.asyncio
async def test_done_reaches_full_queue():
    """终止事件必须可达且不丢数据块：队列满时转异步投递排队等位，慢消费者
    收齐全部数据块后收到 done（total_bytes 校验可通过——对位 Rust
    finish_fs_read_stream 满员 spawn send，不丢块不静默截断）"""
    router = NotificationRouter()
    queue = router.register_stream("h-full")
    for seq in range(READ_STREAM_QUEUE_CAPACITY):
        await router.dispatch(chunk_msg("h-full", seq, b"x"))
    await router.dispatch(done_msg("h-full", 999))
    # 已排队的数据块一个不丢
    events = [queue.get_nowait() for _ in range(READ_STREAM_QUEUE_CAPACITY)]
    assert all(event.kind == "chunk" for event in events)
    # 消费者腾出位置后终止事件异步送达
    terminal = await asyncio.wait_for(queue.get(), 1)
    assert terminal.kind == "done" and terminal.total_bytes == 999


@pytest.mark.asyncio
async def test_fail_channel_only_drains_matching_streams():
    """连接恢复失败按通道清扫：只 fail 该通道的挂起流"""
    router = NotificationRouter()
    control_q = router.register_stream("h-c", channel="control")
    data_q = router.register_stream("h-d", channel="data")

    router.fail_channel("data", "data connection lost")

    event = await data_q.get()
    assert event.kind == "failed" and "data connection lost" in event.error
    # 连接清扫：句柄随连接消亡（server_settled=True），消费者不得再补 fs/close
    assert event.server_settled is True
    assert control_q.empty()  # 控制面流不受影响
    # 幂等：再次清扫无残留可扫
    router.fail_channel("data", "again")


@pytest.mark.asyncio
async def test_duplicate_handle_registration_rejected():
    """句柄重复注册 = 协议违约（对位 Rust register_fs_read_stream 报错）"""
    router = NotificationRouter()
    router.register_stream("h-dup")
    with pytest.raises(ValueError, match="already registered"):
        router.register_stream("h-dup")
    router.unregister_stream("h-dup")
    router.register_stream("h-dup")  # 注销后可复用
