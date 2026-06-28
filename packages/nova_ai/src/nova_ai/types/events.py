"""
事件类型定义
"""

from typing import Union, Literal, Optional
from pydantic import Field
from .base_model import NovaBaseModel

from .messages import AssistantMessage
from .content import ToolCall
from .enums import StopReason


class StartEvent(NovaBaseModel):
    """流开始事件"""
    type: Literal["start"] = "start"
    partial: AssistantMessage = Field(default_factory=AssistantMessage)


class TextStartEvent(NovaBaseModel):
    """文本内容开始事件"""
    type: Literal["text_start"] = "text_start"
    content_index: int = 0
    partial: AssistantMessage = Field(default_factory=AssistantMessage)


class TextDeltaEvent(NovaBaseModel):
    """文本内容增量事件"""
    type: Literal["text_delta"] = "text_delta"
    content_index: int = 0
    delta: str = ""
    partial: AssistantMessage = Field(default_factory=AssistantMessage)


class TextEndEvent(NovaBaseModel):
    """文本内容结束事件"""
    type: Literal["text_end"] = "text_end"
    content_index: int = 0
    content: Optional[str] = None
    partial: AssistantMessage = Field(default_factory=AssistantMessage)


class ThinkingStartEvent(NovaBaseModel):
    """思考内容开始事件"""
    type: Literal["thinking_start"] = "thinking_start"
    content_index: int = 0
    partial: AssistantMessage = Field(default_factory=AssistantMessage)


class ThinkingDeltaEvent(NovaBaseModel):
    """思考内容增量事件"""
    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int = 0
    delta: str = ""
    partial: AssistantMessage = Field(default_factory=AssistantMessage)


class ThinkingEndEvent(NovaBaseModel):
    """思考内容结束事件"""
    type: Literal["thinking_end"] = "thinking_end"
    content_index: int = 0
    content: Optional[str] = None
    partial: AssistantMessage = Field(default_factory=AssistantMessage)


class ToolCallStartEvent(NovaBaseModel):
    """工具调用开始事件"""
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int = 0
    partial: AssistantMessage = Field(default_factory=AssistantMessage)


class ToolCallDeltaEvent(NovaBaseModel):
    """工具调用增量事件"""
    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int = 0
    delta: str = ""
    partial: AssistantMessage = Field(default_factory=AssistantMessage)


class ToolCallEndEvent(NovaBaseModel):
    """工具调用结束事件"""
    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int = 0
    tool_call: ToolCall = Field(default_factory=ToolCall)
    partial: AssistantMessage = Field(default_factory=AssistantMessage)


class DoneEvent(NovaBaseModel):
    """完成事件"""
    type: Literal["done"] = "done"
    reason: StopReason = StopReason.STOP
    message: AssistantMessage = Field(default_factory=AssistantMessage)


class ErrorEvent(NovaBaseModel):
    """错误事件"""
    type: Literal["error"] = "error"
    reason: Literal["aborted", "error"] = "error"
    error: AssistantMessage = Field(default_factory=AssistantMessage)


# 助手消息事件联合类型
AssistantMessageEvent = Union[
    StartEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
    DoneEvent,
    ErrorEvent
]
