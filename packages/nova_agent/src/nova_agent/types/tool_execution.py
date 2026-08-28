"""
Tool execution intermediate types used by the agent loop.
"""

from dataclasses import dataclass, field
from typing import Any, List, Literal, Union

from nova_ai import ToolResultMessage

from .base import AgentToolCall
from .tool import AgentToolResult


@dataclass(frozen=True)
class ExecutedToolCallOutcome:
    """Raw outcome after a tool has been executed."""

    result: AgentToolResult[Any]
    is_error: bool


@dataclass(frozen=True)
class FinalizedToolCallOutcome:
    """Final outcome after afterToolCall hooks have been applied."""

    tool_call: AgentToolCall
    result: AgentToolResult[Any]
    is_error: bool


@dataclass(frozen=True)
class ExecutedToolCallBatch:
    """Batch of tool result messages produced from a single assistant message."""

    messages: List[ToolResultMessage] = field(default_factory=list)
    terminate: bool = False


@dataclass(frozen=True)
class _ImmediateToolCallOutcome:
    """A tool call whose outcome was determined during preparation (e.g. error or block)."""

    result: AgentToolResult[Any]
    is_error: bool
    kind: Literal["immediate"] = field(default="immediate", init=False)


@dataclass(frozen=True)
class _PreparedToolCallModel:
    """A tool call that has been prepared and is ready for execution."""

    tool_call: AgentToolCall
    tool: Any
    args: Any
    kind: Literal["prepared"] = field(default="prepared", init=False)


PreparedToolCall = Union[_PreparedToolCallModel, _ImmediateToolCallOutcome]


__all__ = [
    "ExecutedToolCallOutcome",
    "FinalizedToolCallOutcome",
    "ExecutedToolCallBatch",
    "PreparedToolCall",
]
