"""测试共享 builder：工具实现与类型化 EventStream 构造。"""

from typing import Any, Callable, List, Union

from nova_ai import AssistantMessageEventStream
from nova_async import AbortedError
from nova_protocol import (
    AgentToolResult,
    AssistantMessage,
    AssistantMessageEvent,
    DoneEvent,
    ErrorEvent,
    Model,
    StartEvent,
    StopReason,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    ToolCall,
    ToolCallEndEvent,
)

from nova_agent import AgentTool, ToolExecutionMode


class EchoTool(AgentTool):
    """基础 echo 工具。"""

    name: str = "echo"
    description: str = "Echo the input message"
    parameters: dict = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }
    label: str = "Echo"

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text=f"echo: {params.get('message', '')}")],
            details={"input": params.get("message", "")},
        )


class SquareTool(AgentTool):
    """返回数字平方，测试 prepare_arguments。"""

    name: str = "square"
    description: str = "Return the square of a number"
    label: str = "Square"
    parameters: dict = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }

    def prepare_arguments(self, args: Any) -> Any:
        if isinstance(args.get("x"), str):
            args = dict(args)
            args["x"] = int(args["x"])
        return args

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text=str(params["x"] ** 2))],
            details={"result": params["x"] ** 2},
        )


class SlowTool(AgentTool):
    """execution_mode 覆盖为 sequential 的工具。"""

    name: str = "slow"
    description: str = "A tool that must run sequentially"
    label: str = "Slow"
    parameters: dict = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    execution_mode: Union[ToolExecutionMode, None] = "sequential"

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text=f"slow-{params['value']}")], details={}
        )


class TerminateTool(AgentTool):
    """返回 terminate=True 的工具。"""

    name: str = "terminate"
    description: str = "Terminate the agent run"
    label: str = "Terminate"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text="done")], details={}, terminate=True
        )


class RaisingTool(AgentTool):
    """execute 抛出异常的工具。"""

    name: str = "raising"
    description: str = "Always raises"
    label: str = "Raising"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        raise RuntimeError("boom")


class UpdatingTool(AgentTool):
    """会通过 on_update 发送中间结果的工具。"""

    name: str = "updating"
    description: str = "Sends an update"
    label: str = "Updating"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        if on_update:
            on_update(
                AgentToolResult(content=[TextContent(text="partial")], details={})
            )
        return AgentToolResult(content=[TextContent(text="final")], details={})


class AbortableTool(AgentTool):
    """可中断工具，用于测试 agent.abort()。"""

    name: str = "abortable"
    description: str = "Waits for abort signal"
    label: str = "Abortable"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        assert signal is not None
        await signal.wait()
        raise AbortedError("Operation aborted")


def make_assistant_message(
    model: Model,
    content: List[Any],
    stop_reason: Union[StopReason, str] = StopReason.STOP,
) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=content,
        api=model.api,
        provider=model.provider,
        model=model.id,
        stop_reason=StopReason(stop_reason),
    )


def make_stream(events: List[AssistantMessageEvent]) -> AssistantMessageEventStream:
    """按给定事件列表构造正式 assistant 流（end 收尾）。"""
    stream = AssistantMessageEventStream()
    for event in events:
        stream.push(event)
    stream.end()
    return stream


def _done(reason: StopReason, message: AssistantMessage) -> DoneEvent:
    if reason is StopReason.LENGTH:
        return DoneEvent(reason=StopReason.LENGTH, message=message)
    if reason is StopReason.TOOL_USE:
        return DoneEvent(reason=StopReason.TOOL_USE, message=message)
    return DoneEvent(reason=StopReason.STOP, message=message)


def _error(reason: StopReason, message: AssistantMessage) -> ErrorEvent:
    if reason is StopReason.ABORTED:
        return ErrorEvent(reason=StopReason.ABORTED, error=message)
    return ErrorEvent(reason=StopReason.ERROR, error=message)


def text_stream(model: Model, text: str) -> AssistantMessageEventStream:
    """构造一个只回复固定文本的 EventStream。"""
    partial = make_assistant_message(model, [TextContent(text=text)])
    events: List[AssistantMessageEvent] = [StartEvent(partial=partial)]
    if text:
        events.append(TextDeltaEvent(content_index=0, delta=text, partial=partial))
        events.append(TextEndEvent(content_index=0, content=text, partial=partial))
    events.append(_done(StopReason.STOP, partial))
    return make_stream(events)


def final_stream(
    model: Model,
    text: str = "",
    stop_reason: Union[StopReason, str] = StopReason.STOP,
) -> AssistantMessageEventStream:
    """构造一个以任意 stop_reason 结束的 EventStream。"""
    reason = StopReason(stop_reason)
    content: List[Any] = [TextContent(text=text)] if text else []
    partial = make_assistant_message(model, content, stop_reason=reason)
    events: List[AssistantMessageEvent] = [StartEvent(partial=partial)]
    if text:
        events.append(TextDeltaEvent(content_index=0, delta=text, partial=partial))
        events.append(TextEndEvent(content_index=0, content=text, partial=partial))
    if reason in (StopReason.ERROR, StopReason.ABORTED):
        events.append(_error(reason, partial))
    else:
        events.append(_done(reason, partial))
    return make_stream(events)


def tool_call_stream(
    model: Model, tool_name: str, arguments: dict, text_prefix: str = ""
) -> AssistantMessageEventStream:
    """构造一个回复单个 tool call 的 EventStream。"""
    content: List[Any] = []
    if text_prefix:
        content.append(TextContent(text=text_prefix))
    tool_call = ToolCall(id="tc-1", name=tool_name, arguments=arguments)
    content.append(tool_call)
    partial = make_assistant_message(model, content, stop_reason=StopReason.TOOL_USE)
    events: List[AssistantMessageEvent] = [StartEvent(partial=partial)]
    if text_prefix:
        events.append(
            TextDeltaEvent(content_index=0, delta=text_prefix, partial=partial)
        )
        events.append(
            TextEndEvent(content_index=0, content=text_prefix, partial=partial)
        )
    events.append(
        ToolCallEndEvent(
            content_index=len(content) - 1, tool_call=tool_call, partial=partial
        )
    )
    events.append(_done(StopReason.TOOL_USE, partial))
    return make_stream(events)


def multi_tool_call_stream(
    model: Model, calls: List[tuple]
) -> AssistantMessageEventStream:
    """构造一个回复多个 tool call 的 EventStream。calls: [(name, args)]"""
    content: List[Any] = [
        ToolCall(id=f"tc-{i}", name=name, arguments=args)
        for i, (name, args) in enumerate(calls, 1)
    ]
    partial = make_assistant_message(model, content, stop_reason=StopReason.TOOL_USE)
    events: List[AssistantMessageEvent] = [StartEvent(partial=partial)]
    for idx, tool_call in enumerate(content):
        events.append(
            ToolCallEndEvent(content_index=idx, tool_call=tool_call, partial=partial)
        )
    events.append(_done(StopReason.TOOL_USE, partial))
    return make_stream(events)


def abortable_tool_call_stream(
    tool_name: str, arguments: dict
) -> Callable[[Model, Any, Any], AssistantMessageEventStream]:
    """第一次返回 tool call；若 signal 已 aborted，则返回 aborted 流。"""

    def stream_fn(
        model: Model, context: Any, options: Any
    ) -> AssistantMessageEventStream:
        signal = getattr(options, "signal", None)
        if signal and signal.aborted:
            return final_stream(model, "aborted", stop_reason=StopReason.ABORTED)
        return tool_call_stream(model, tool_name, arguments)

    return stream_fn


def tool_call_then_text_stream(
    tool_name: str, arguments: dict, text: str = "ok"
) -> Callable[[Model, Any, Any], AssistantMessageEventStream]:
    """第一次返回 tool call，之后返回固定文本。"""
    step = 0

    def stream_fn(
        model: Model, context: Any, options: Any
    ) -> AssistantMessageEventStream:
        nonlocal step
        step += 1
        if step == 1:
            return tool_call_stream(model, tool_name, arguments)
        return text_stream(model, text)

    return stream_fn


__all__ = [
    "EchoTool",
    "SquareTool",
    "SlowTool",
    "TerminateTool",
    "RaisingTool",
    "UpdatingTool",
    "AbortableTool",
    "make_assistant_message",
    "make_stream",
    "text_stream",
    "final_stream",
    "tool_call_stream",
    "multi_tool_call_stream",
    "abortable_tool_call_stream",
    "tool_call_then_text_stream",
]
