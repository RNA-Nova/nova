"""
Hook 上下文与结果类型定义。
"""

from typing import Any, List, Optional, TYPE_CHECKING, Union

from nova_ai import (
    AssistantMessage,
    ImageContent,
    Model,
    TextContent,
    ThinkingLevel,
    ToolResultMessage,
)
from nova_ai.types.base_model import NovaBaseModel

from .base import AgentMessage, AgentToolCall
from .tool import AgentToolResult


class BeforeToolCallResult(NovaBaseModel):
    """Result returned from beforeToolCall."""

    block: Optional[bool] = None
    reason: Optional[str] = None


class AfterToolCallResult(NovaBaseModel):
    """Partial override returned from afterToolCall."""

    content: Optional[List[Union[TextContent, ImageContent]]] = None
    details: Optional[Any] = None
    is_error: Optional[bool] = None
    terminate: Optional[bool] = None


class BeforeToolCallContext(NovaBaseModel):
    """Context passed to beforeToolCall."""

    assistant_message: AssistantMessage
    tool_call: AgentToolCall
    args: Any
    context: "AgentContext"


class AfterToolCallContext(NovaBaseModel):
    """Context passed to afterToolCall."""

    assistant_message: AssistantMessage
    tool_call: AgentToolCall
    args: Any
    result: AgentToolResult[Any]
    is_error: bool
    context: "AgentContext"


class ShouldStopAfterTurnContext(NovaBaseModel):
    """Context passed to shouldStopAfterTurn."""

    message: AssistantMessage
    tool_results: List[ToolResultMessage]
    context: "AgentContext"
    new_messages: List[AgentMessage]
    turn_index: int = 0


class AgentLoopTurnUpdate(NovaBaseModel):
    """Replacement runtime state used by the agent loop before starting another provider request."""

    context: Optional["AgentContext"] = None
    model: Optional[Model] = None
    thinking_level: Optional[ThinkingLevel] = None


class PrepareNextTurnContext(ShouldStopAfterTurnContext):
    """Context passed to prepareNextTurn."""

    pass


if TYPE_CHECKING:
    from .context import AgentContext


__all__ = [
    "BeforeToolCallResult",
    "AfterToolCallResult",
    "BeforeToolCallContext",
    "AfterToolCallContext",
    "ShouldStopAfterTurnContext",
    "AgentLoopTurnUpdate",
    "PrepareNextTurnContext",
]
