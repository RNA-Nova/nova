"""
流式处理测试
"""

import asyncio

import pytest
from nova_protocol import (
    AssistantMessage,
    DoneEvent,
    ErrorEvent,
    KnownApi,
    KnownProvider,
    StopReason,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    Usage,
)

from nova_ai.streaming import AssistantMessageEventStream


class TestStreamingEvents:
    """流式事件测试"""

    def test_text_start_event(self):
        event = TextStartEvent(content_index=0, partial=AssistantMessage(content=[]))
        assert event.type == "text_start"
        assert event.content_index == 0

    def test_text_delta_event(self):
        event = TextDeltaEvent(
            content_index=0,
            delta="hello",
            partial=AssistantMessage(content=[]),
        )
        assert event.type == "text_delta"
        assert event.delta == "hello"

    def test_text_end_event(self):
        event = TextEndEvent(
            content_index=0,
            content="hello",
            partial=AssistantMessage(content=[]),
        )
        assert event.type == "text_end"
        assert event.content == "hello"

    def test_thinking_start_event(self):
        event = ThinkingStartEvent(
            content_index=0, partial=AssistantMessage(content=[])
        )
        assert event.type == "thinking_start"

    def test_thinking_delta_event(self):
        event = ThinkingDeltaEvent(
            content_index=0,
            delta="thinking...",
            partial=AssistantMessage(content=[]),
        )
        assert event.type == "thinking_delta"
        assert event.delta == "thinking..."

    def test_thinking_end_event(self):
        event = ThinkingEndEvent(
            content_index=0,
            content="thought",
            partial=AssistantMessage(content=[]),
        )
        assert event.type == "thinking_end"
        assert event.content == "thought"

    def test_tool_call_start_event(self):
        event = ToolCallStartEvent(
            content_index=0,
            partial=AssistantMessage(content=[]),
        )
        assert event.type == "toolcall_start"

    def test_tool_call_delta_event(self):
        event = ToolCallDeltaEvent(
            content_index=0,
            delta='{"q": "test"}',
            partial=AssistantMessage(content=[]),
        )
        assert event.type == "toolcall_delta"

    def test_tool_call_end_event(self):
        event = ToolCallEndEvent(
            content_index=0,
            tool_call=ToolCall(id="tc1", tool_name="search", arguments={}),
            partial=AssistantMessage(content=[]),
        )
        assert event.type == "toolcall_end"

    def test_done_event(self):
        event = DoneEvent(
            reason=StopReason.STOP,
            message=AssistantMessage(content=[]),
        )
        assert event.type == "done"
        assert event.reason == StopReason.STOP

    def test_error_event(self):
        event = ErrorEvent(error=AssistantMessage(content=[]), reason="error")
        assert event.type == "error"
        assert event.reason == "error"


class TestAssistantMessageContent:
    """AssistantMessage content 序列化测试"""

    def test_text_content(self):
        msg = AssistantMessage(content=[TextContent(text="hello")])
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextContent)

    def test_thinking_content(self):
        msg = AssistantMessage(content=[ThinkingContent(text="thinking")])
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], ThinkingContent)

    def test_tool_call_content(self):
        msg = AssistantMessage(
            content=[
                ToolCall(id="tc1", tool_name="search", arguments={"q": "test"}),
            ]
        )
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], ToolCall)

    def test_mixed_content(self):
        msg = AssistantMessage(
            content=[
                ThinkingContent(text="let me think"),
                TextContent(text="result"),
            ]
        )
        assert len(msg.content) == 2
        assert isinstance(msg.content[0], ThinkingContent)
        assert isinstance(msg.content[1], TextContent)


class TestEndSemantics:
    """end() 契约（有意分歧：fail-loud，对齐 pi undefined 语义的收紧）。"""

    def test_end_without_result_on_empty_stream_raises(self):
        """空流无参 end()：result() 抛 RuntimeError——生产者漏发终态事件时响亮失败。"""
        stream = AssistantMessageEventStream()
        stream.end()
        with pytest.raises(RuntimeError, match="ended without result"):
            asyncio.run(stream.result())

    def test_end_after_complete_event_is_noop(self):
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
        assert asyncio.run(stream.result()) is message

    def test_end_with_result_still_wins_over_nothing(self):
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
        assert asyncio.run(stream.result()) is message


class TestDriveOwnership:
    """drive() 驱动任务归属（流自持后台任务，防 GC 中途回收）。"""

    @pytest.mark.asyncio
    async def test_drive_sets_task_and_clears_after_done(self):
        stream = AssistantMessageEventStream()

        async def produce():
            stream.end(result=AssistantMessage(content=[]))

        task = stream.drive(produce())
        assert stream.task is task

        await stream.result()
        await asyncio.sleep(0)  # 让 done callback 跑完
        assert stream.task is None  # 完成自清

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_drive_on_completed_stream_raises(self):
        stream = AssistantMessageEventStream()
        stream.end(result=AssistantMessage(content=[]))

        async def noop():
            return None

        with pytest.raises(RuntimeError, match="completed stream"):
            stream.drive(noop())
