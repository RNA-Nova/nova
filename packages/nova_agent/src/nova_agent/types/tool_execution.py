"""
Tool execution intermediate types used by the agent loop.
"""

from typing import Any, List, Literal, Union

from nova_ai import ToolResultMessage
from nova_ai.types.base_model import NovaBaseModel

from .base import AgentToolCall
from .tool import AgentToolResult


class ExecutedToolCallOutcome(NovaBaseModel):
    """Raw outcome after a tool has been executed."""

    result: AgentToolResult[Any]
    is_error: bool


class FinalizedToolCallOutcome(NovaBaseModel):
    """Final outcome after afterToolCall hooks have been applied."""

    tool_call: AgentToolCall
    result: AgentToolResult[Any]
    is_error: bool


class ExecutedToolCallBatch(NovaBaseModel):
    """Batch of tool result messages produced from a single assistant message."""

    messages: List[ToolResultMessage] = []
    terminate: bool = False


class _ImmediateToolCallOutcome(NovaBaseModel):
    kind: Literal["immediate"] = "immediate"
    result: AgentToolResult[Any]
    is_error: bool


class _PreparedToolCallModel(NovaBaseModel):
    kind: Literal["prepared"] = "prepared"
    tool_call: AgentToolCall
    tool: Any
    args: Any


PreparedToolCall = Union[_PreparedToolCallModel, _ImmediateToolCallOutcome]


__all__ = [
    "ExecutedToolCallOutcome",
    "FinalizedToolCallOutcome",
    "ExecutedToolCallBatch",
    "PreparedToolCall",
]
