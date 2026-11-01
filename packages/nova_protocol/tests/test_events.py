"""流式事件词汇（events.py）测试——构造与字段形状。"""

from nova_protocol import (
    AssistantMessage,
    DoneEvent,
    ErrorEvent,
    StartEvent,
    StopReason,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingContent,
    ThinkingEndEvent,
    ToolCall,
    ToolCallEndEvent,
)


def _msg(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)])


class TestEvents:
    def test_start_event(self):
        msg = _msg("start")
        e = StartEvent(partial=msg)
        assert e.type == "start"
        assert e.partial == msg

    def test_text_delta_event(self):
        msg = _msg("hello")
        e = TextDeltaEvent(content_index=0, delta="hello", partial=msg)
        assert e.type == "text_delta"
        assert e.delta == "hello"

    def test_text_end_event_content_is_str(self):
        """text_end 的 content 是字符串（对齐 TS）。"""
        msg = _msg("hello world")
        e = TextEndEvent(content_index=0, content="hello world", partial=msg)
        assert e.type == "text_end"
        assert e.content == "hello world"

    def test_thinking_end_event_content_is_str(self):
        """thinking_end 的 content 是字符串（对齐 TS）。"""
        msg = AssistantMessage(content=[ThinkingContent(thinking="thinking...")])
        e = ThinkingEndEvent(content_index=0, content="thinking...", partial=msg)
        assert e.type == "thinking_end"
        assert e.content == "thinking..."

    def test_toolcall_end_event(self):
        tc = ToolCall(id="tc1", name="search")
        msg = AssistantMessage(content=[tc])
        e = ToolCallEndEvent(content_index=0, tool_call=tc, partial=msg)
        assert e.type == "toolcall_end"
        assert e.tool_call.name == "search"

    def test_done_event(self):
        msg = _msg("done")
        e = DoneEvent(reason=StopReason.STOP, message=msg)
        assert e.type == "done"
        assert e.reason == StopReason.STOP

    def test_error_event(self):
        msg = AssistantMessage(error_message="something wrong")
        e = ErrorEvent(reason="error", error=msg)
        assert e.type == "error"
