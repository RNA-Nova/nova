"""
流式处理测试
"""

import asyncio
import time

import pytest
from nova_ai.streaming import AssistantMessageEventStream, EventStream
from nova_protocol import (
    AssistantMessage,
    DoneEvent,
    KnownApi,
    KnownProvider,
    StopReason,
    TextContent,
    TextStartEvent,
    Usage,
)


@pytest.mark.asyncio
class TestEndSemantics:
    """end() 契约（有意分歧：fail-loud，对齐 pi undefined 语义的收紧）。"""

    async def test_end_without_result_on_empty_stream_raises(self):
        """空流无参 end()：result() 抛 RuntimeError——生产者漏发终态事件时响亮失败。"""
        stream = AssistantMessageEventStream()
        stream.end()
        with pytest.raises(RuntimeError, match="ended without result"):
            await stream.result()

    async def test_end_after_complete_event_is_noop(self):
        """push 过 done/error（complete 事件置 _done）后 end() 是 no-op——
        不会覆盖已提取的 result，也不会抛 RuntimeError。"""
        message = AssistantMessage(
            role="assistant",
            content=[],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            model="m",
            usage=Usage(),
            stop_reason=StopReason.STOP,
        )
        stream = AssistantMessageEventStream()
        stream.push(DoneEvent(reason=StopReason.STOP, message=message))
        stream.end()  # no-op：已完成
        assert await stream.result() is message

    async def test_end_with_result_still_wins_over_nothing(self):
        """end(result=...) 显式给果：无 complete 事件也能正常收尾。"""
        message = AssistantMessage(
            role="assistant",
            content=[],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            model="m",
            usage=Usage(),
            stop_reason=StopReason.STOP,
        )
        stream = AssistantMessageEventStream()
        stream.end(result=message)
        assert await stream.result() is message

    async def test_none_is_a_valid_terminal_result(self):
        """通用流必须区分“无终值”与合法 None，包括完成后首次读结果。"""
        extracted = EventStream(lambda event: event == "done", lambda _event: None)
        extracted.push("done")
        assert await asyncio.wait_for(extracted.result(), 0.1) is None

        explicit = EventStream(lambda event: event == "done", lambda _event: None)
        explicit.end(result=None)
        assert await asyncio.wait_for(explicit.result(), 0.1) is None


@pytest.mark.asyncio
class TestDriveOwnership:
    """drive() 驱动任务归属（流自持后台任务，防 GC 中途回收）。"""

    async def test_drive_sets_task_and_clears_after_done(self):
        stream = AssistantMessageEventStream()

        async def produce():
            stream.end(result=AssistantMessage(content=[]))

        task = stream.drive(produce())
        assert stream.task is task

        await stream.result()
        await asyncio.sleep(0)  # 让 done callback 跑完
        assert stream.task is None  # 完成自清

    async def test_driver_survives_gc(self):
        """无外部引用的驱动任务也能跑完——流是唯一持有者。

        回归：裸 ``asyncio.create_task`` 无强引用时任务可能被 GC 中途回收
        （流式静默冻死）；drive() 后即使主动 gc.collect() 也必须完成。
        """
        import gc

        stream = AssistantMessageEventStream()
        final = AssistantMessage(content=[TextContent(text="ok")])

        async def produce():
            await asyncio.sleep(0.01)  # 挂起窗口：给 GC 回收的机会
            stream.push(DoneEvent(reason=StopReason.STOP, message=final))
            stream.end(result=final)

        stream.drive(produce())  # 返回值故意不接——无任何外部引用
        gc.collect()
        gc.collect()

        assert await asyncio.wait_for(stream.result(), timeout=1.0) is final

    async def test_drive_on_completed_stream_raises(self):
        stream = AssistantMessageEventStream()
        stream.end(result=AssistantMessage(content=[]))

        async def noop():
            return None

        with pytest.raises(RuntimeError, match="completed stream"):
            stream.drive(noop())

    async def test_concurrent_consumer_fails_fast(self):
        """单消费者契约：第二个消费者重叠等待时 __anext__ 直接抛错（不静默挂死）。"""
        stream = AssistantMessageEventStream()

        async def first():
            async for _ in stream:
                pass

        task = asyncio.ensure_future(first())
        await asyncio.sleep(0)  # 让第一个消费者进入等待态
        with pytest.raises(RuntimeError, match="single-consumer"):
            await stream.__anext__()
        stream.end(result=AssistantMessage(content=[]))
        await task

    async def test_end_is_idempotent_and_wakes_waiters(self):
        """end() 幂等：完成事件后再调用仍唤醒等待者、不覆盖已定结果。"""
        stream = AssistantMessageEventStream()
        final = AssistantMessage(content=[TextContent(text="ok")])
        stream.push(DoneEvent(reason=StopReason.STOP, message=final))
        # 完成事件已收尾（_done 已置位）——再调 end() 不得炸、不得覆盖结果
        stream.end()
        stream.end()
        assert await stream.result() is final


@pytest.mark.asyncio
class TestStructuralClosure:
    """v2 结构闭合：驱动任务任何退出形态都终结流（Queue.shutdown 兜底）。"""

    async def test_external_cancel_terminates_stream(self):
        """外部掀桌（task.cancel 驱动任务）→ 消费者收到终结、result 落定——
        不悬挂（v1"驱动暴毙→流不终结"潜伏通道的回归锁）。"""
        stream = AssistantMessageEventStream()

        async def hang():
            await asyncio.Event().wait()  # 永不就绪

        task = stream.drive(hang())
        consumed = asyncio.Event()

        async def consume():
            async for _ in stream:
                pass
            consumed.set()

        consumer = asyncio.ensure_future(consume())
        await asyncio.sleep(0)  # 让消费者进入等待态
        task.cancel()
        await asyncio.wait_for(consumed.wait(), timeout=1.0)
        await consumer
        with pytest.raises(RuntimeError, match="ended without result"):
            await stream.result()

    async def test_driver_exception_flows_to_result(self):
        """驱动暴毙（异常）→ 错误进 result 通道（不再悬挂），消费者正常终结。"""
        stream = AssistantMessageEventStream()

        async def boom():
            raise ValueError("driver died")

        stream.drive(boom())
        with pytest.raises(ValueError, match="driver died"):
            await asyncio.wait_for(stream.result(), timeout=1.0)
        # 消费者侧同样终结
        assert [event async for event in stream] == []

    async def test_buffer_drains_before_termination(self):
        """排干语义：终态/关通道前缓冲的事件全部送达（不重不漏）。"""
        stream = AssistantMessageEventStream()
        final = AssistantMessage(content=[TextContent(text="ok")])
        stream.push(
            TextStartEvent(content_index=0, partial=AssistantMessage(content=[]))
        )
        stream.push(DoneEvent(reason=StopReason.STOP, message=final))
        stream.end()
        received = [event async for event in stream]
        assert [event.type for event in received] == ["text_start", "done"]
        assert await stream.result() is final

    async def test_maxsize_knob(self):
        """背压参数位就位：有界构造可用，界内行为不变（溢出语义预留）。"""
        stream: EventStream[str, str] = EventStream(
            is_complete=lambda e: e == "end",
            extract_result=lambda e: e,
            maxsize=1,
        )
        stream.push("a")
        stream.end(result="a")
        assert [event async for event in stream] == ["a"]
        assert await stream.result() == "a"


@pytest.mark.asyncio
async def test_drive_cancel_before_first_step_terminates_stream():
    """首步前取消（协程体一行未跑）也终结流——终结兜底在 done 回调层
    （体内 try/finally 对未起跑任务不执行的回归锁）。"""
    stream = AssistantMessageEventStream()

    async def hang():
        await asyncio.Event().wait()

    task = stream.drive(hang())
    task.cancel()  # 同一 tick——协程还没起跑
    with pytest.raises(asyncio.CancelledError):
        await task  # 任务落定（done 回调已调度）
    await asyncio.sleep(0)  # 引导：done 回调跑一拍
    assert stream.done is True
    with pytest.raises(RuntimeError, match="ended without result"):
        await stream.result()


@pytest.mark.asyncio
async def test_result_waiter_cancellation_does_not_poison_shared_future():
    stream: EventStream[str, str] = EventStream(
        is_complete=lambda event: event == "done", extract_result=lambda event: "ok"
    )
    waiter = asyncio.create_task(stream.result())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    stream.end(result="ok")
    assert await stream.result() == "ok"


@pytest.mark.asyncio
async def test_response_duration_is_timed_only_for_current_stream():
    # 先建流再生成消息，匹配真实响应顺序；反向构造会在跨毫秒边界偶发跳过盖戳。
    stream = AssistantMessageEventStream()
    message = AssistantMessage(content=[], timestamp=int(time.time() * 1000))
    stream.push(DoneEvent(reason=StopReason.STOP, message=message))
    assert message.duration_ms is not None and message.duration_ms >= 0

    old = AssistantMessage(content=[], timestamp=1)
    deferred = AssistantMessageEventStream()
    deferred.end(result=old)
    assert old.duration_ms is None

    existing = AssistantMessage(
        content=[], timestamp=int(time.time() * 1000), duration_ms=42
    )
    forwarded = AssistantMessageEventStream()
    forwarded.end(result=existing)
    assert existing.duration_ms == 42
