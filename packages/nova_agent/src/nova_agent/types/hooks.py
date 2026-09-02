"""
Hook 上下文与结果类型定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

from nova_ai import (
    AssistantMessage,
    ImageContent,
    Model,
    ModelThinkingLevel,
    TextContent,
    ToolResultMessage,
)

from .base import AgentMessage, AgentToolCall
from .tool import AgentToolResult


@dataclass(frozen=True)
class BeforeToolCallResult:
    """Result returned from beforeToolCall."""

    block: Optional[bool] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class AfterToolCallResult:
    """Partial override returned from afterToolCall."""

    content: Optional[List[Union[TextContent, ImageContent]]] = None
    details: Optional[Any] = None
    is_error: Optional[bool] = None
    terminate: Optional[bool] = None


@dataclass(frozen=True)
class BeforeToolCallContext:
    """Context passed to beforeToolCall."""

    assistant_message: AssistantMessage
    tool_call: AgentToolCall
    args: Any
    context: AgentContext


@dataclass(frozen=True)
class AfterToolCallContext:
    """Context passed to afterToolCall."""

    assistant_message: AssistantMessage
    tool_call: AgentToolCall
    args: Any
    result: AgentToolResult[Any]
    is_error: bool
    context: AgentContext


@dataclass(frozen=True)
class ShouldStopAfterTurnContext:
    """Context passed to shouldStopAfterTurn."""

    message: AssistantMessage
    tool_results: List[ToolResultMessage]
    context: AgentContext
    new_messages: List[AgentMessage]
    turn_index: int = 0


@dataclass(frozen=True)
class AgentLoopTurnUpdate:
    """Replacement runtime state used by the agent loop before starting another provider request."""

    context: Optional[AgentContext] = None
    model: Optional[Model] = None
    thinking_level: Optional[ModelThinkingLevel] = None


@dataclass(frozen=True)
class PrepareNextTurnContext(ShouldStopAfterTurnContext):
    """Context passed to prepareNextTurn."""

    pass


__all__ = [
    "BeforeToolCallResult",
    "AfterToolCallResult",
    "BeforeToolCallContext",
    "AfterToolCallContext",
    "ShouldStopAfterTurnContext",
    "AgentLoopTurnUpdate",
    "PrepareNextTurnContext",
]
