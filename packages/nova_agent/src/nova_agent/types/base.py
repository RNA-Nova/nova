"""
基础类型与类型别名（组件内部：行为签名与循环局部 Literal 词汇）。

跨组件序列化的消息/事件词汇已迁 ``nova_protocol``（批次 ②）；
本模块只留行为契约与运行时别名。
"""

from typing import TYPE_CHECKING, Awaitable, Callable, Literal, Protocol, Union

from nova_protocol import ToolCall

if TYPE_CHECKING:
    from nova_ai import AssistantMessageEventStream
    from nova_ai.stream_options import SimpleStreamOptions
    from nova_protocol import AgentEvent, Context, Model


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


# Event sink used by the agent loop to emit AgentEvents.
AgentEventSink = Callable[["AgentEvent"], Awaitable[None]]

# Configuration for how tool calls from a single assistant message are executed.
ToolExecutionMode = Literal["sequential", "parallel"]

# Controls how many queued user messages are injected at a queue drain point.
QueueMode = Literal["all", "one-at-a-time"]

__all__ = [
    "AgentToolCall",
    "StreamFn",
    "AgentEventSink",
    "ToolExecutionMode",
    "QueueMode",
]
