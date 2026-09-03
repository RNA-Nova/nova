"""
Agent 上下文与循环配置类型定义。
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional, Union

from nova_ai import AbortSignal, Message, Model, SimpleStreamOptions

from .base import AgentMessage, ToolExecutionMode
from .hooks import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentLoopTurnUpdate,
    BeforeToolCallContext,
    BeforeToolCallResult,
    PrepareNextTurnContext,
    ShouldStopAfterTurnContext,
)
from .tool import AgentTool


@dataclass
class AgentContext:
    """Agent context similar to SimpleStreamOptions but using AgentMessage.

    运行时快照，不由 Pydantic 校验；构造后会被循环原地 mutate（如 messages.append）。
    """

    system_prompt: Optional[str] = ""
    messages: List[AgentMessage] = field(default_factory=list)
    tools: Optional[List[AgentTool]] = None


@dataclass(frozen=True)
class AgentLoopConfig:
    """Configuration for the agent loop.

    使用组合持有 ``SimpleStreamOptions``（给 ``nova_ai`` 的纯数据选项），
    其余字段为运行时 callbacks 与策略开关。避免继承 Pydantic 导致的
    ``model_dump / model_validate`` hack。

    配置构造后不可变：循环内的更新一律走 ``dataclasses.replace`` 产生新实例。
    """

    stream_options: SimpleStreamOptions
    """传给 ``nova_ai`` 的流式选项（model、api_key、signal 等数据字段）。"""

    model: Model
    """The LLM model to use."""

    convert_to_llm: Optional[
        Callable[[List[AgentMessage]], Union[List[Message], Awaitable[List[Message]]]]
    ] = None
    """
    Converts AgentMessage[] to LLM‑compatible Message[] before each LLM call.
    Each AgentMessage must be converted to a UserMessage, AssistantMessage,
    or ToolResultMessage that the LLM can understand. Messages that cannot be
    converted (e.g., UI‑only notifications) should be filtered out.
    """

    transform_context: Optional[
        Callable[
            [List[AgentMessage], Optional[AbortSignal]], Awaitable[List[AgentMessage]]
        ]
    ] = None
    """
    Optional transform applied to the context before `convert_to_llm`.
    Use this for operations that work at the AgentMessage level:
    - Context window management (pruning old messages)
    - Injecting context from external sources
    """

    get_api_key: Optional[
        Callable[[str], Union[Optional[str], Awaitable[Optional[str]]]]
    ] = None
    """
    Resolves an API key dynamically for each LLM call.
    Useful for short‑lived OAuth tokens that may expire during long‑running tool execution.
    """

    should_stop_after_turn: Optional[
        Callable[[ShouldStopAfterTurnContext], Union[bool, Awaitable[bool]]]
    ] = None
    """
    Called after each turn fully completes and `turn_end` has been emitted.
    If it returns true, the loop emits `agent_end` and exits before polling steering or follow-up queues.
    """

    prepare_next_turn: Optional[
        Callable[
            [PrepareNextTurnContext],
            Union[AgentLoopTurnUpdate, Awaitable[AgentLoopTurnUpdate], None],
        ]
    ] = None
    """
    Called after `turn_end` and before the loop decides whether another provider request should start.
    Return replacement context/model/thinking state to affect the next turn in this run.
    """

    get_steering_messages: Optional[Callable[[], Awaitable[List[AgentMessage]]]] = None
    """
    Returns steering messages to inject into the conversation mid‑run.
    Called after the current assistant turn finishes executing its tool calls,
    unless ``should_stop_after_turn`` exits first. If messages are returned, they
    are added to the context before the next LLM call. Tool calls from the
    current assistant message are not skipped.
    """

    get_follow_up_messages: Optional[Callable[[], Awaitable[List[AgentMessage]]]] = None
    """
    Returns follow-up messages to process after the agent would otherwise stop.
    Called when the agent has no more tool calls and no steering messages.
    If messages are returned, they're added to the context and the agent continues.
    """

    tool_execution: ToolExecutionMode = "parallel"
    """
    Tool execution strategy for assistant messages that contain multiple tool calls.
    - "sequential": the whole batch runs strictly one call at a time.
    - "parallel": calls are prepared in submission order, then executed through a
      fair read‑write gate — tools declaring per‑tool "sequential" take the write
      gate (exclusive) while the rest share the read gate (concurrent).
    """

    before_tool_call: Optional[
        Callable[
            [BeforeToolCallContext, Optional[AbortSignal]],
            Union[BeforeToolCallResult, Awaitable[BeforeToolCallResult], None],
        ]
    ] = None
    """
    Called before a tool is executed, after arguments have been validated.
    Return BeforeToolCallResult(block=True) to prevent execution.
    """

    after_tool_call: Optional[
        Callable[
            [AfterToolCallContext, Optional[AbortSignal]],
            Union[AfterToolCallResult, Awaitable[AfterToolCallResult], None],
        ]
    ] = None
    """
    Called after a tool finishes executing, before `tool_execution_end` and tool-result message events are emitted.
    Return an AfterToolCallResult to override parts of the executed tool result.
    """


__all__ = [
    "AgentContext",
    "AgentLoopConfig",
]
