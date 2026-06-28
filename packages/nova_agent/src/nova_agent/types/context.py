"""
Agent 上下文与循环配置类型定义。
"""

from typing import Any, Awaitable, Callable, List, Optional, Union

from pydantic import Field

from nova_ai import Message, Model, SimpleStreamOptions
from nova_ai.types.base_model import NovaBaseModel

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


class AgentContext(NovaBaseModel):
    """Agent context similar to SimpleStreamOptions but using AgentMessage."""

    system_prompt: Optional[str] = ""
    messages: List[AgentMessage] = Field(default_factory=list)
    tools: Optional[List[Any]] = Field(default=None)


class AgentLoopConfig(SimpleStreamOptions):
    """
    Configuration for the agent loop.
    Inherits all fields from SimpleStreamOptions and adds agent‑specific ones.
    """

    model: Model
    """The LLM model to use."""

    convert_to_llm: Optional[
        Callable[[List[AgentMessage]], Union[List[Message], Awaitable[List[Message]]]]
    ] = Field(default=None, exclude=True)
    """
    Converts AgentMessage[] to LLM‑compatible Message[] before each LLM call.
    Each AgentMessage must be converted to a UserMessage, AssistantMessage,
    or ToolResultMessage that the LLM can understand. Messages that cannot be
    converted (e.g., UI‑only notifications) should be filtered out.
    """

    transform_context: Optional[
        Callable[[List[AgentMessage], Optional[Any]], Awaitable[List[AgentMessage]]]
    ] = Field(default=None, exclude=True)
    """
    Optional transform applied to the context before `convert_to_llm`.
    Use this for operations that work at the AgentMessage level:
    - Context window management (pruning old messages)
    - Injecting context from external sources
    """

    get_api_key: Optional[
        Callable[[str], Union[Optional[str], Awaitable[Optional[str]]]]
    ] = Field(default=None, exclude=True)
    """
    Resolves an API key dynamically for each LLM call.
    Useful for short‑lived OAuth tokens that may expire during long‑running tool execution.
    """

    should_stop_after_turn: Optional[
        Callable[[ShouldStopAfterTurnContext], Union[bool, Awaitable[bool]]]
    ] = Field(default=None, exclude=True)
    """
    Called after each turn fully completes and `turn_end` has been emitted.
    If it returns true, the loop emits `agent_end` and exits before polling steering or follow-up queues.
    """

    prepare_next_turn: Optional[
        Callable[
            [PrepareNextTurnContext],
            Union[AgentLoopTurnUpdate, Awaitable[AgentLoopTurnUpdate], None],
        ]
    ] = Field(default=None, exclude=True)
    """
    Called after `turn_end` and before the loop decides whether another provider request should start.
    Return replacement context/model/thinking state to affect the next turn in this run.
    """

    get_steering_messages: Optional[Callable[[], Awaitable[List[AgentMessage]]]] = (
        Field(default=None, exclude=True)
    )
    """
    Returns steering messages to inject into the conversation mid‑run.
    Called after each tool execution to check for user interruptions.
    If messages are returned, remaining tool calls are skipped and these messages
    are added to the context before the next LLM call.
    """

    get_follow_up_messages: Optional[Callable[[], Awaitable[List[AgentMessage]]]] = (
        Field(default=None, exclude=True)
    )
    """
    Returns follow‑up messages to process after the agent would otherwise stop.
    Called when the agent has no more tool calls and no steering messages.
    If messages are returned, they're added to the context and the agent continues.
    """

    tool_execution: ToolExecutionMode = "parallel"
    """
    Tool execution strategy for assistant messages that contain multiple tool calls.
    - "sequential": each tool call is prepared, executed, and finalized before the next one starts.
    - "parallel": tool calls are prepared sequentially, then allowed tools execute concurrently.
    """

    before_tool_call: Optional[
        Callable[
            [BeforeToolCallContext, Optional[Any]],
            Union[BeforeToolCallResult, Awaitable[BeforeToolCallResult], None],
        ]
    ] = Field(default=None, exclude=True)
    """
    Called before a tool is executed, after arguments have been validated.
    Return BeforeToolCallResult(block=True) to prevent execution.
    """

    after_tool_call: Optional[
        Callable[
            [AfterToolCallContext, Optional[Any]],
            Union[AfterToolCallResult, Awaitable[AfterToolCallResult], None],
        ]
    ] = Field(default=None, exclude=True)
    """
    Called after a tool finishes executing, before `tool_execution_end` and tool-result message events are emitted.
    Return an AfterToolCallResult to override parts of the executed tool result.
    """

    stream_fn: Optional[Any] = Field(default=None, exclude=True)
    """
    Optional LLM stream function override.
    If provided, used instead of the default `stream_simple` from nova_ai.
    """


__all__ = [
    "AgentContext",
    "AgentLoopConfig",
]
