"""工具执行中间结果与单次子调用配置。"""

from dataclasses import dataclass, field
from typing import Any, Callable, List, Literal, Optional, Protocol, Union

from nova_protocol import (
    AbortSignal,
    AgentToolResult,
    AssistantMessage,
    ToolResultMessage,
)

from .base import AgentToolCall
from .context import AgentContext
from .tool import AgentTool


class ToolCallHooks(Protocol):
    """准备与收尾工具调用所需的 before/after 钩子结构。"""

    @property
    def before_tool_call(self) -> Optional[Callable[..., Any]]: ...

    @property
    def after_tool_call(self) -> Optional[Callable[..., Any]]: ...


@dataclass(frozen=True)
class ExecutedToolCallOutcome:
    """工具执行后的原始结果；duration_ms 不含 after 钩子。"""

    result: AgentToolResult[Any]
    is_error: bool
    duration_ms: Optional[int] = None


@dataclass(frozen=True)
class AgentToolCallOutcome:
    """after_tool_call 后的最终结果；未实际执行时 duration_ms 为 None。"""

    tool_call: AgentToolCall
    result: AgentToolResult[Any]
    is_error: bool
    duration_ms: Optional[int] = None


@dataclass(frozen=True)
class ExecutedToolCallBatch:
    messages: List[ToolResultMessage] = field(default_factory=list)
    terminate: bool = False


@dataclass(frozen=True)
class ImmediateToolCallOutcome:
    """准备阶段已确定的错误/拦截结果。"""

    result: AgentToolResult[Any]
    is_error: bool
    kind: Literal["immediate"] = field(default="immediate", init=False)


@dataclass(frozen=True)
class PreparedToolCallModel:
    """已找到工具、校验参数并通过 before 钩子的可执行调用。"""

    tool_call: AgentToolCall
    tool: AgentTool[Any, Any]
    args: Any
    kind: Literal["prepared"] = field(default="prepared", init=False)


@dataclass(frozen=True, kw_only=True)
class RunToolCallOptions:
    """run_tool_call 的单次执行配置；不发事件、不写转录。"""

    tools: List[AgentTool[Any, Any]]
    assistant_message: AssistantMessage
    context: AgentContext
    signal: Optional[AbortSignal] = None
    on_update: Optional[Callable[[AgentToolResult[Any]], Any]] = None
    before_tool_call: Optional[Callable[..., Any]] = None
    after_tool_call: Optional[Callable[..., Any]] = None


PreparedToolCall = Union[PreparedToolCallModel, ImmediateToolCallOutcome]

__all__ = [
    "ExecutedToolCallOutcome",
    "AgentToolCallOutcome",
    "ExecutedToolCallBatch",
    "ImmediateToolCallOutcome",
    "PreparedToolCall",
    "PreparedToolCallModel",
    "RunToolCallOptions",
    "ToolCallHooks",
]
