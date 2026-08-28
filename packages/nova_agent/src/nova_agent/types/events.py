"""
Agent 事件类型定义。

所有事件字段均与 TS 对齐为必填，避免运行时发现某个关键字段为 None。
事件是不可变值对象：构造后只读，统一经 ``_AgentEventBase`` 锁定 frozen。
"""

from typing import Any, List, Literal, Union

from nova_ai import AssistantMessageEvent, ToolResultMessage
from nova_ai.types.base_model import NovaBaseModel
from pydantic import ConfigDict, field_serializer

from .base import AgentMessage


def _dump_agent_message(value: Any) -> Any:
    """按**运行时类型**序列化消息。

    ``AgentMessage = Union[Message, CustomAgentMessage]``——pydantic 按声明
    联合序列化时，无字段的 ``CustomAgentMessage`` 基类会把 ``CustomMessage``、
    ``BashExecutionMessage`` 等子类剥成 ``{}``（线上 custom 消息全灭：
    扩展注入消息、bash 用户工具终态卡都到不了前端）。
    """
    dump_wire = getattr(value, "dump_wire", None)
    if callable(dump_wire):
        return dump_wire()
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value


class _AgentEventBase(NovaBaseModel):
    """Agent 事件公共基座：事件是不可变值对象。"""

    model_config = ConfigDict(frozen=True)

    @field_serializer("message", "messages", check_fields=False)
    def _ser_messages(self, value: Any) -> Any:
        if isinstance(value, list):
            return [_dump_agent_message(item) for item in value]
        return _dump_agent_message(value)


class AgentStartEvent(_AgentEventBase):
    type: Literal["agent_start"] = "agent_start"


class AgentEndEvent(_AgentEventBase):
    type: Literal["agent_end"] = "agent_end"
    messages: List[AgentMessage]


class TurnStartEvent(_AgentEventBase):
    type: Literal["turn_start"] = "turn_start"


class TurnEndEvent(_AgentEventBase):
    type: Literal["turn_end"] = "turn_end"
    message: AgentMessage
    tool_results: List[ToolResultMessage]


class MessageStartEvent(_AgentEventBase):
    type: Literal["message_start"] = "message_start"
    message: AgentMessage


class MessageUpdateEvent(_AgentEventBase):
    type: Literal["message_update"] = "message_update"
    message: AgentMessage
    assistant_message_event: AssistantMessageEvent


class MessageEndEvent(_AgentEventBase):
    type: Literal["message_end"] = "message_end"
    message: AgentMessage


class ToolExecutionStartEvent(_AgentEventBase):
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str
    tool_name: str
    args: Any


class ToolExecutionUpdateEvent(_AgentEventBase):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str
    tool_name: str
    args: Any
    partial_result: Any


class ToolExecutionEndEvent(_AgentEventBase):
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool


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
