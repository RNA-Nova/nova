"""
Agent 事件类型定义。
"""

from typing import Any, List, Literal, Optional, Union

from pydantic import Field

from nova_ai import AssistantMessageEvent, ToolResultMessage
from nova_ai.types.base_model import NovaBaseModel

from .base import AgentMessage


class AgentStartEvent(NovaBaseModel):
    type: Literal["agent_start"] = "agent_start"


class AgentEndEvent(NovaBaseModel):
    type: Literal["agent_end"] = "agent_end"
    messages: Optional[List[AgentMessage]] = Field(default=None)


class TurnStartEvent(NovaBaseModel):
    type: Literal["turn_start"] = "turn_start"


class TurnEndEvent(NovaBaseModel):
    type: Literal["turn_end"] = "turn_end"
    message: Optional[AgentMessage] = Field(default=None)
    tool_results: Optional[List[ToolResultMessage]] = Field(default=None)


class MessageStartEvent(NovaBaseModel):
    type: Literal["message_start"] = "message_start"
    message: Optional[AgentMessage] = Field(default=None)


class MessageUpdateEvent(NovaBaseModel):
    type: Literal["message_update"] = "message_update"
    message: Optional[AgentMessage] = Field(default=None)
    assistant_message_event: Optional[AssistantMessageEvent] = Field(default=None)


class MessageEndEvent(NovaBaseModel):
    type: Literal["message_end"] = "message_end"
    message: Optional[AgentMessage] = Field(default=None)


class ToolExecutionStartEvent(NovaBaseModel):
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: Optional[str] = Field(default=None)
    tool_name: Optional[str] = Field(default=None)
    args: Optional[Any] = Field(default=None)


class ToolExecutionUpdateEvent(NovaBaseModel):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: Optional[str] = Field(default=None)
    tool_name: Optional[str] = Field(default=None)
    args: Optional[Any] = Field(default=None)
    partial_result: Optional[Any] = Field(default=None)


class ToolExecutionEndEvent(NovaBaseModel):
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: Optional[str] = Field(default=None)
    tool_name: Optional[str] = Field(default=None)
    result: Optional[Any] = Field(default=None)
    is_error: Optional[bool] = Field(default=None)


# Union of all possible agent events
AgentEvent = Union[
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
]


__all__ = [
    "AgentStartEvent",
    "AgentEndEvent",
    "TurnStartEvent",
    "TurnEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "MessageEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "ToolExecutionEndEvent",
    "AgentEvent",
]
