"""
事件类型定义
"""

from typing import Literal, Union

from .base_model import NovaBaseModel
from .content import ToolCall
from .enums import StopReason
from .messages import AssistantMessage


class StartEvent(NovaBaseModel):
    """流开始事件"""

    type: Literal["start"] = "start"
    partial: AssistantMessage


class TextStartEvent(NovaBaseModel):
    """文本内容开始事件"""

    type: Literal["text_start"] = "text_start"
    content_index: int
    partial: AssistantMessage


class TextDeltaEvent(NovaBaseModel):
    """文本内容增量事件"""

    type: Literal["text_delta"] = "text_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class TextEndEvent(NovaBaseModel):
    """文本内容结束事件"""

    type: Literal["text_end"] = "text_end"
    content_index: int
    content: str
    partial: AssistantMessage


class ThinkingStartEvent(NovaBaseModel):
    """思考内容开始事件"""

    type: Literal["thinking_start"] = "thinking_start"
    content_index: int
    partial: AssistantMessage


class ThinkingDeltaEvent(NovaBaseModel):
    """思考内容增量事件"""

    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ThinkingEndEvent(NovaBaseModel):
    """思考内容结束事件"""

    type: Literal["thinking_end"] = "thinking_end"
    content_index: int
    content: str
    partial: AssistantMessage


class ToolCallStartEvent(NovaBaseModel):
    """工具调用开始事件"""

    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int
    partial: AssistantMessage


class ToolCallDeltaEvent(NovaBaseModel):
    """工具调用增量事件"""

    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ToolCallEndEvent(NovaBaseModel):
    """工具调用结束事件"""

    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage


class DoneEvent(NovaBaseModel):
    """完成事件"""

    type: Literal["done"] = "done"
    reason: StopReason
    message: AssistantMessage


class ErrorEvent(NovaBaseModel):
    """错误事件"""

    type: Literal["error"] = "error"
    reason: Literal["aborted", "error"]
    error: AssistantMessage


# 助手消息事件联合类型
AssistantMessageEvent = Union[
    StartEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    DoneEvent,
    ErrorEvent,
]
