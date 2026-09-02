"""
基础类型与类型别名。
"""

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Protocol, Union

from nova_ai import Message, ModelThinkingLevel, ToolCall
from nova_ai.types.base_model import NovaBaseModel

if TYPE_CHECKING:
    from nova_ai import AssistantMessageEventStream, Context, Model, SimpleStreamOptions


class CustomAgentMessage(NovaBaseModel):
    """Base class for custom agent messages. Extend this to add your own message types."""

    pass


# AgentMessage can be either a standard Message or any custom message
AgentMessage = Union[Message, CustomAgentMessage]

# A single tool call content block emitted by an assistant message.
AgentToolCall = ToolCall


class StreamFn(Protocol):
    """
    Stream function signature — 与 ``Models.stream_simple`` 保持一致，
    允许同步或异步返回 ``AssistantMessageEventStream``。
    """

    def __call__(
        self,
        model: "Model",
        context: "Context",
        options: "SimpleStreamOptions",
    ) -> Union[
        "AssistantMessageEventStream", Awaitable["AssistantMessageEventStream"]
    ]: ...


if TYPE_CHECKING:
    from .events import AgentEvent

# Event sink used by the agent loop to emit AgentEvents.
AgentEventSink = Callable[["AgentEvent"], Awaitable[None]]


# Configuration for how tool calls from a single assistant message are executed.
ToolExecutionMode = Literal["sequential", "parallel"]

# Controls how many queued user messages are injected at a queue drain point.
QueueMode = Literal["all", "one-at-a-time"]


__all__ = [
    "CustomAgentMessage",
    "AgentMessage",
    "AgentToolCall",
    "StreamFn",
    "ModelThinkingLevel",
    "ToolExecutionMode",
    "QueueMode",
]
