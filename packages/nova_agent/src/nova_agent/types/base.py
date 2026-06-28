"""
基础类型与类型别名。
"""

from typing import Any, Awaitable, Callable, Literal, Protocol, TYPE_CHECKING, Union

from nova_ai import Message, ThinkingLevel, ToolCall
from nova_ai.types.base_model import NovaBaseModel


class CustomAgentMessage(NovaBaseModel):
    """Base class for custom agent messages. Extend this to add your own message types."""

    pass


# AgentMessage can be either a standard Message or any custom message
AgentMessage = Union[Message, CustomAgentMessage]

# A single tool call content block emitted by an assistant message.
AgentToolCall = ToolCall


class StreamFn(Protocol):
    """Stream function signature – can be sync or async (returns a Promise in TS)."""

    def __call__(self, *args: Any) -> Union[Any, Awaitable[Any]]:
        """Matches the signature of streamSimple from nova_ai."""
        ...


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
    "ThinkingLevel",
    "ToolExecutionMode",
    "QueueMode",
]
