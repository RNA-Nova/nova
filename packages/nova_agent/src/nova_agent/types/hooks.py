"""Agent 请求、回合与工具钩子的上下文和结果类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Literal, Optional, Union

if TYPE_CHECKING:
    from .context import AgentContext

from nova_protocol import (
    AgentMessage,
    AgentToolResult,
    AssistantMessage,
    ImageContent,
    Model,
    ModelThinkingLevel,
    TextContent,
    ToolResultMessage,
    Usage,
)

from .tool import AgentToolCall


@dataclass(frozen=True)
class BeforeToolCallResult:
    """block 阻止执行；terminate 参与整批提前终止判定。"""

    block: Optional[bool] = None
    reason: Optional[str] = None
    terminate: Optional[bool] = None


@dataclass(frozen=True)
class AfterToolCallResult:
    """逐字段覆盖工具结果；缺席字段保持原值，不做深合并。"""

    content: Optional[List[Union[TextContent, ImageContent]]] = None
    details: Optional[Any] = None
    structured_content: Optional[Any] = None
    usage: Optional[Usage] = None
    is_error: Optional[bool] = None
    terminate: Optional[bool] = None


@dataclass(frozen=True)
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: AgentToolCall
    args: Any
    context: AgentContext


@dataclass(frozen=True)
class AfterToolCallContext:
    assistant_message: AssistantMessage
    tool_call: AgentToolCall
    args: Any
    result: AgentToolResult[Any]
    is_error: bool
    context: AgentContext


@dataclass(frozen=True)
class AgentTurnContext:
    """一个已完成回合的助手消息、工具结果、当前转录与本轮新增消息。"""

    message: AssistantMessage
    tool_results: List[ToolResultMessage]
    context: AgentContext
    new_messages: List[AgentMessage]
    turn_index: int = 0


@dataclass(frozen=True)
class AgentTurnDecision:
    """finish_turn 的显式调度决策；None 则保持自然调度。"""

    action: Literal["continue", "end"]


@dataclass(frozen=True)
class AgentLoopTurnUpdate:
    """下一回合的运行时替换与待追加转录消息。"""

    context: Optional[AgentContext] = None
    messages: Optional[List[AgentMessage]] = None
    model: Optional[Model] = None
    thinking_level: Optional[ModelThinkingLevel] = None


@dataclass(frozen=True)
class PrepareNextTurnContext(AgentTurnContext):
    """prepare_next_turn 使用完成回合的同一上下文。"""


@dataclass(frozen=True)
class PrepareRequestContext:
    """一次 provider 请求前的当前运行时状态。"""

    context: AgentContext
    model: Model
    thinking_level: ModelThinkingLevel


@dataclass(frozen=True)
class AgentRequestUpdate:
    """prepare_request 可替换的请求状态；不携带追加消息。"""

    context: Optional[AgentContext] = None
    model: Optional[Model] = None
    thinking_level: Optional[ModelThinkingLevel] = None


__all__ = [
    "AfterToolCallContext",
    "AfterToolCallResult",
    "AgentLoopTurnUpdate",
    "AgentRequestUpdate",
    "AgentTurnContext",
    "AgentTurnDecision",
    "BeforeToolCallContext",
    "BeforeToolCallResult",
    "PrepareNextTurnContext",
    "PrepareRequestContext",
]
