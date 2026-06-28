"""
nova_agent 核心循环单元测试

使用 mock stream_fn，不依赖真实网络，验证：
- 基本 prompt 流程与事件顺序
- 工具调用、工具结果、并行/串行执行
- before_tool_call / after_tool_call hook
- prepare_next_turn / should_stop_after_turn hook
- 异常与终止路径（error / aborted / tool not found / execute 异常）
- convert_to_llm / transform_context / get_api_key
- steering_mode / follow_up_mode 队列 drain
- Agent.continue_ 与 run_agent_loop_continue
- Agent 生命周期方法（reset、并发保护、listener）
"""

from typing import Any, Callable, List

import pytest

from nova_agent import (
    Agent,
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    AbortSignal,
    AfterToolCallContext,
    AfterToolCallResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    CustomAgentMessage,
    MessageUpdateEvent,
    PrepareNextTurnContext,
    ShouldStopAfterTurnContext,
    AgentLoopTurnUpdate,
    ToolExecutionUpdateEvent,
)
from nova_ai import (
    AssistantMessage,
    DoneEvent,
    EventStream,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    ToolCall,
    ToolCallEndEvent,
    ToolResultMessage,
    UserMessage,
)
from nova_agent.agent_loop import run_agent_loop, run_agent_loop_continue


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
    parameters: dict = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    execution_mode: str = "sequential"

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text=f"slow-{params['value']}")],
            details={},
        )


class TerminateTool(AgentTool):
    """返回 terminate=True 的工具。"""

    name: str = "terminate"
    description: str = "Terminate the agent run"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text="done")],
            details={},
            terminate=True,
        )


def _make_assistant_message(
    model: Model, content: List[Any], stop_reason: str = "stop"
) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=content,
        api=model.api,
        provider=model.provider,
        model=model.id,
        stop_reason=stop_reason,
    )


def _text_stream(model: Model, text: str) -> EventStream:
    """构造一个只回复固定文本的 EventStream。"""
    stream = EventStream(
        is_complete=lambda e: getattr(e, "type", None) == "done",
        extract_result=lambda e: e.message,
    )

    partial = _make_assistant_message(model, [TextContent(text=text)])
    stream.push(StartEvent(partial=partial))
    if text:
        stream.push(TextDeltaEvent(delta=text, partial=partial))
        stream.push(TextEndEvent(content=text, partial=partial))
    stream.push(DoneEvent(reason="stop", message=partial))
    stream.end()
    return stream


def _tool_call_stream(
    model: Model, tool_name: str, arguments: dict, text_prefix: str = ""
) -> EventStream:
    """构造一个回复单个 tool call 的 EventStream。"""
    stream = EventStream(
        is_complete=lambda e: getattr(e, "type", None) == "done",
        extract_result=lambda e: e.message,
    )

    content: List[Any] = []
    if text_prefix:
        content.append(TextContent(text=text_prefix))
    tool_call = ToolCall(id="tc-1", name=tool_name, arguments=arguments)
    content.append(tool_call)
    partial = _make_assistant_message(model, content, stop_reason="toolUse")
    stream.push(StartEvent(partial=partial))
    if text_prefix:
        stream.push(TextDeltaEvent(delta=text_prefix, partial=partial))
        stream.push(TextEndEvent(content=text_prefix, partial=partial))
    stream.push(
        ToolCallEndEvent(
            content_index=len(content) - 1,
            tool_call=tool_call,
            partial=partial,
        )
    )
    stream.push(DoneEvent(reason="toolUse", message=partial))
    stream.end()
    return stream


def _abortable_tool_call_stream(
    tool_name: str, arguments: dict
) -> Callable[[Model, Any, Any], EventStream]:
    """第一次返回 tool call；若 signal 已 aborted，则返回 stop_reason='aborted' 的文本流。"""

    def stream_fn(model: Model, context: Any, options: Any) -> EventStream:
        signal = getattr(options, "signal", None)
        if signal and signal.aborted:
            return _final_stream(model, "aborted", stop_reason="aborted")
        return _tool_call_stream(model, tool_name, arguments)

    return stream_fn


def _tool_call_then_text_stream(
    tool_name: str, arguments: dict, text: str = "ok"
) -> Callable[[Model, Any, Any], EventStream]:
    """第一次返回 tool call，之后返回固定文本。"""
    step = 0

    def stream_fn(model: Model, context: Any, options: Any) -> EventStream:
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(model, tool_name, arguments)
        return _text_stream(model, text)

    return stream_fn


def _multi_tool_call_stream(model: Model, calls: List[tuple]) -> EventStream:
    """构造一个回复多个 tool call 的 EventStream。calls: [(name, args)]"""
    stream = EventStream(
        is_complete=lambda e: getattr(e, "type", None) == "done",
        extract_result=lambda e: e.message,
    )

    content: List[Any] = [
        ToolCall(id=f"tc-{i}", name=name, arguments=args)
        for i, (name, args) in enumerate(calls, 1)
    ]
    partial = _make_assistant_message(model, content, stop_reason="toolUse")
    stream.push(StartEvent(partial=partial))
    for idx, tc in enumerate(content):
        stream.push(ToolCallEndEvent(content_index=idx, tool_call=tc, partial=partial))
    stream.push(DoneEvent(reason="toolUse", message=partial))
    stream.end()
    return stream


@pytest.fixture
def dummy_model() -> Model:
    from nova_ai import KnownApi, KnownProvider, ModelCost

    return Model(
        id="mock-model",
        name="Mock Model",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.OPENAI,
        base_url="https://example.com",
        max_tokens=4096,
        context_window=8192,
        input_types=["text"],
        reasoning=False,
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
    )


# ----------------------------------------------------------------------
# Basic prompt flow
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_basic(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "hello"))
    agent.set_model(dummy_model)

    events: List[str] = []
    agent.subscribe(lambda e: events.append(e.type))

    await agent.prompt(UserMessage(role="user", content="hi"))

    assert events == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert [m.role for m in agent.state.messages] == ["user", "assistant"]
    assert agent.state.messages[-1].content[0].text == "hello"


# ----------------------------------------------------------------------
# Tool execution
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_flow(dummy_model):
    """第一次返回 tool call，第二次返回文本回复。"""
    calls = 0

    async def stream_fn(model, context, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _tool_call_stream(model, "echo", {"message": "world"})
        return _text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    events: List[str] = []
    agent.subscribe(lambda e: events.append(e.type))

    await agent.prompt(UserMessage(role="user", content="call echo"))

    assert "tool_execution_start" in events
    assert "tool_execution_end" in events
    assert any(m.role == "toolResult" for m in agent.state.messages)
    assert agent.state.messages[-1].role == "assistant"


# ----------------------------------------------------------------------
# Hooks
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_tool_call_block(dummy_model):
    calls = []

    async def before(ctx: BeforeToolCallContext, signal):
        calls.append(ctx.tool_call.name)
        return BeforeToolCallResult(block=True, reason="blocked by test")

    agent = Agent(
        stream_fn=_tool_call_then_text_stream("echo", {"message": "world"}),
        before_tool_call=before,
    )
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt(UserMessage(role="user", content="call echo"))

    assert calls == ["echo"]
    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    assert tool_result.is_error is True
    assert "blocked by test" in tool_result.content[0].text


@pytest.mark.asyncio
async def test_after_tool_call_override(dummy_model):
    calls = []

    async def after(ctx: AfterToolCallContext, signal):
        calls.append(ctx.tool_call.name)
        return AfterToolCallResult(content=[TextContent(text="overridden")])

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(model, "echo", {"message": "world"})
        return _text_stream(model, "ok")

    agent = Agent(
        stream_fn=stream_fn,
        after_tool_call=after,
    )
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt(UserMessage(role="user", content="call echo"))

    assert calls == ["echo"]
    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    assert tool_result.content[0].text == "overridden"


@pytest.mark.asyncio
async def test_should_stop_after_turn(dummy_model):
    calls = []

    async def should_stop(ctx: ShouldStopAfterTurnContext):
        calls.append(ctx.message.content[0].text)
        return True

    agent = Agent(
        stream_fn=lambda m, c, o: _text_stream(m, "stop here"),
        should_stop_after_turn=should_stop,
    )
    agent.set_model(dummy_model)

    await agent.prompt(UserMessage(role="user", content="run"))

    assert calls == ["stop here"]
    assert agent.state.messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_prepare_next_turn_updates_context(dummy_model):
    """prepare_next_turn 修改 context，应在下一轮请求中生效。"""
    calls = []

    async def prepare(ctx: PrepareNextTurnContext):
        calls.append(True)
        ctx.context.system_prompt = "updated system prompt"
        return AgentLoopTurnUpdate()

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(model, "echo", {"message": "world"})
        return _text_stream(model, context.system_prompt)

    agent = Agent(
        stream_fn=stream_fn,
        prepare_next_turn=prepare,
    )
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt(UserMessage(role="user", content="run"))

    assert calls == [True, True]
    assert agent.state.messages[-1].role == "assistant"
    assert agent.state.messages[-1].content[0].text == "updated system prompt"


# ----------------------------------------------------------------------
# Tool execution mode
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_tool_execution(dummy_model):
    """单个 assistant 消息包含两个 tool call，验证并行执行。"""

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _multi_tool_call_stream(
                model,
                [("echo", {"message": "a"}), ("echo", {"message": "b"})],
            )
        return _text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn, tool_execution="parallel")
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    events: List[str] = []
    agent.subscribe(lambda e: events.append(e.type))

    await agent.prompt(UserMessage(role="user", content="call both"))

    tool_results = [m for m in agent.state.messages if m.role == "toolResult"]
    assert len(tool_results) == 2
    assert {r.content[0].text for r in tool_results} == {"echo: a", "echo: b"}


@pytest.mark.asyncio
async def test_sequential_mode_forces_sequential(dummy_model):
    """工具声明 execution_mode=sequential，即使 config 是 parallel 也串行。"""
    order = []

    class LoggingSlowTool(SlowTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            order.append(params["value"])
            return AgentToolResult(
                content=[TextContent(text=f"slow-{params['value']}")],
                details={},
            )

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _multi_tool_call_stream(
                model,
                [("slow", {"value": "first"}), ("slow", {"value": "second"})],
            )
        return _text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn, tool_execution="parallel")
    agent.set_model(dummy_model)
    agent.set_tools([LoggingSlowTool()])

    await agent.prompt(UserMessage(role="user", content="call both"))

    assert order == ["first", "second"]


# ----------------------------------------------------------------------
# prepare_arguments & terminate
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_arguments_converts_types(dummy_model):
    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(model, "square", {"x": "5"})
        return _text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    agent.set_tools([SquareTool()])

    await agent.prompt(UserMessage(role="user", content="square"))

    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    assert tool_result.content[0].text == "25"


@pytest.mark.asyncio
async def test_terminate_stops_after_tool_batch(dummy_model):
    """工具返回 terminate=True，agent 应在工具批次后停止，不再发起新请求。"""
    second_call = False

    async def stream_fn(model, context, options):
        nonlocal second_call
        if not second_call:
            second_call = True
            return _tool_call_stream(model, "terminate", {})
        return _text_stream(model, "should not reach")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    agent.set_tools([TerminateTool()])

    await agent.prompt(UserMessage(role="user", content="terminate"))

    assert agent.state.messages[-1].role == "toolResult"


# ----------------------------------------------------------------------
# run_agent_loop low-level API
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_loop_api(dummy_model):
    context = AgentContext(system_prompt="sys", messages=[])
    config = AgentLoopConfig(
        model=dummy_model,
        stream_fn=lambda m, c, o: _text_stream(m, "reply"),
    )

    async def emit(event):
        pass

    new_messages = await run_agent_loop(
        [UserMessage(role="user", content="hi")],
        context,
        config,
        emit,
    )

    assert any(m.role == "assistant" for m in new_messages)
    assert new_messages[-1].role == "assistant"


# ==============================================================================
# Additional comprehensive coverage
# ==============================================================================

import asyncio


def _final_stream(
    model: Model, text: str = "", stop_reason: str = "stop"
) -> EventStream:
    """构造一个以任意 stop_reason 结束的 EventStream。"""
    stream = EventStream(
        is_complete=lambda e: getattr(e, "type", None) == "done",
        extract_result=lambda e: e.message,
    )
    content: List[Any] = [TextContent(text=text)] if text else []
    partial = _make_assistant_message(model, content, stop_reason=stop_reason)
    stream.push(StartEvent(partial=partial))
    if text:
        stream.push(TextDeltaEvent(delta=text, partial=partial))
        stream.push(TextEndEvent(content=text, partial=partial))
    stream.push(DoneEvent(reason=stop_reason, message=partial))
    stream.end()
    return stream


class RaisingTool(AgentTool):
    """execute 抛出异常的工具。"""

    name: str = "raising"
    description: str = "Always raises"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        raise RuntimeError("boom")


class UpdatingTool(AgentTool):
    """会通过 on_update 发送中间结果的工具。"""

    name: str = "updating"
    description: str = "Sends an update"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        if on_update:
            on_update(
                AgentToolResult(content=[TextContent(text="partial")], details={})
            )
        return AgentToolResult(content=[TextContent(text="final")], details={})


class UIMessage(CustomAgentMessage):
    """自定义 UI 消息，用于测试 convert_to_llm / transform_context。"""

    role: str = "ui"
    text: str = ""


# ------------------------------------------------------------------------------
# Stop reasons and failure paths
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_reason_error_ends_agent(dummy_model):
    """模型返回 stop_reason='error' 时应直接结束运行。"""
    calls = 0

    async def stream_fn(model, context, options):
        nonlocal calls
        calls += 1
        return _final_stream(model, "oops", stop_reason="error")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    await agent.prompt(UserMessage(role="user", content="hi"))

    assert calls == 1
    assert agent.state.messages[-1].role == "assistant"
    assert agent.state.messages[-1].stop_reason == "error"


@pytest.mark.asyncio
async def test_stop_reason_aborted_ends_agent(dummy_model):
    """模型返回 stop_reason='aborted' 时应直接结束运行。"""
    agent = Agent(
        stream_fn=lambda m, c, o: _final_stream(m, "stopped", stop_reason="aborted")
    )
    agent.set_model(dummy_model)
    await agent.prompt(UserMessage(role="user", content="hi"))

    assert agent.state.messages[-1].stop_reason == "aborted"


@pytest.mark.asyncio
async def test_tool_not_found_produces_error_result(dummy_model):
    """assistant 调用了未注册的工具时应生成 error toolResult 并终止。"""
    agent = Agent(stream_fn=_tool_call_then_text_stream("missing", {"x": 1}))
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt(UserMessage(role="user", content="call missing"))

    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    assert tool_result.is_error is True
    assert "missing" in tool_result.content[0].text
    assert agent.state.messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_tool_execute_raises_exception(dummy_model):
    """工具 execute 抛出异常时应生成 error toolResult 并继续对话。"""
    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(model, "raising", {})
        return _text_stream(model, "recovered")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    agent.set_tools([RaisingTool()])

    await agent.prompt(UserMessage(role="user", content="raise"))

    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    assert tool_result.is_error is True
    assert "boom" in tool_result.content[0].text
    assert agent.state.messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_before_tool_call_exception_becomes_error(dummy_model):
    """before_tool_call 抛异常时应将工具结果标记为 error 并继续。"""
    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(model, "echo", {"message": "world"})
        return _text_stream(model, "ok")

    def before(ctx, signal):
        raise ValueError("before failed")

    agent = Agent(stream_fn=stream_fn, before_tool_call=before)
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt(UserMessage(role="user", content="call"))

    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    assert tool_result.is_error is True
    assert "before failed" in tool_result.content[0].text


@pytest.mark.asyncio
async def test_after_tool_call_exception_becomes_error(dummy_model):
    """after_tool_call 抛异常时应将工具结果标记为 error 并继续。"""
    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(model, "echo", {"message": "world"})
        return _text_stream(model, "ok")

    def after(ctx, signal):
        raise ValueError("after failed")

    agent = Agent(stream_fn=stream_fn, after_tool_call=after)
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt(UserMessage(role="user", content="call"))

    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    assert tool_result.is_error is True
    assert "after failed" in tool_result.content[0].text


@pytest.mark.asyncio
async def test_after_tool_call_sets_terminate_and_is_error(dummy_model):
    """after_tool_call 可覆盖 terminate 与 is_error，直接终止批次。"""
    agent = Agent(
        stream_fn=_abortable_tool_call_stream("echo", {"message": "world"}),
        after_tool_call=lambda ctx, signal: AfterToolCallResult(
            content=[TextContent(text="forced error")],
            is_error=True,
            terminate=True,
        ),
    )
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt(UserMessage(role="user", content="call"))

    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    assert tool_result.is_error is True
    assert tool_result.content[0].text == "forced error"
    assert agent.state.messages[-1].role == "toolResult"


@pytest.mark.asyncio
async def test_abort_signal_terminates_tool_batch(dummy_model):
    """AbortSignal 在准备阶段被设置时，当前工具调用应立即终止。"""
    signal = AbortSignal()

    async def emit(event):
        pass

    async def before(ctx, signal):
        signal.set()
        return None

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(model, "echo", {"message": "world"})
        return _final_stream(model, "aborted", stop_reason="aborted")

    new_messages = await run_agent_loop(
        [UserMessage(role="user", content="call")],
        AgentContext(system_prompt="sys", messages=[], tools=[EchoTool()]),
        AgentLoopConfig(model=dummy_model, before_tool_call=before),
        emit,
        signal=signal,
        stream_fn=stream_fn,
    )

    tool_results = [m for m in new_messages if m.role == "toolResult"]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is True
    assert "aborted" in tool_results[0].content[0].text


# ------------------------------------------------------------------------------
# convert_to_llm / transform_context / get_api_key
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convert_to_llm_filters_and_transforms(dummy_model):
    """convert_to_llm 能过滤自定义消息并向 LLM 注入额外消息。"""
    seen_messages = []

    def convert(messages):
        result = [m for m in messages if getattr(m, "role", None) != "ui"]
        result.append(UserMessage(role="user", content="converted"))
        return result

    async def stream_fn(model, context, options):
        seen_messages.extend(context.messages)
        return _text_stream(model, "ok")

    agent = Agent(
        initial_state={
            "messages": [
                UserMessage(role="user", content="hi"),
                UIMessage(role="ui", text="ui-only"),
            ]
        },
        stream_fn=stream_fn,
        convert_to_llm=convert,
    )
    agent.set_model(dummy_model)

    await agent.prompt(UserMessage(role="user", content="go"))

    assert all(getattr(m, "role", None) != "ui" for m in seen_messages)
    assert any(getattr(m, "content", "") == "converted" for m in seen_messages)
    # Agent 自身状态中仍保留 UI 消息
    assert any(isinstance(m, UIMessage) for m in agent.state.messages)


@pytest.mark.asyncio
async def test_transform_context_prunes_messages(dummy_model):
    """transform_context 能在 convert 之前裁剪 AgentMessage 上下文。"""
    seen_messages = []

    async def transform(messages, signal):
        return [m for m in messages if m.content != "old"]

    async def stream_fn(model, context, options):
        seen_messages.extend(context.messages)
        return _text_stream(model, "ok")

    agent = Agent(
        initial_state={
            "messages": [
                UserMessage(role="user", content="old"),
                UserMessage(role="user", content="recent"),
            ]
        },
        stream_fn=stream_fn,
        transform_context=transform,
    )
    agent.set_model(dummy_model)

    await agent.prompt(UserMessage(role="user", content="go"))

    assert all(m.content != "old" for m in seen_messages)
    assert any(m.content == "recent" for m in seen_messages)


@pytest.mark.asyncio
async def test_get_api_key_used(dummy_model):
    """config.get_api_key 解析出的 key 应传给 stream_fn 的 options。"""
    seen_options = []

    async def stream_fn(model, context, options):
        seen_options.append(options)
        return _text_stream(model, "ok")

    async def emit(event):
        pass

    await run_agent_loop(
        [UserMessage(role="user", content="hi")],
        AgentContext(system_prompt="sys", messages=[]),
        AgentLoopConfig(model=dummy_model, get_api_key=lambda provider: "secret-key"),
        emit,
        stream_fn=stream_fn,
    )

    assert seen_options[0].api_key == "secret-key"


# ------------------------------------------------------------------------------
# Queue modes
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steering_mode_one_at_a_time(dummy_model):
    """steering_mode='one-at-a-time' 每次只 drain 一条 steering 消息。"""
    calls = 0
    enqueued = False

    async def stream_fn(model, context, options):
        nonlocal calls
        calls += 1
        return _text_stream(model, f"turn-{calls}")

    async def prepare(ctx):
        nonlocal enqueued
        if not enqueued:
            enqueued = True
            agent.steer(UserMessage(role="user", content="A"))
            agent.steer(UserMessage(role="user", content="B"))
        return AgentLoopTurnUpdate()

    agent = Agent(
        stream_fn=stream_fn,
        steering_mode="one-at-a-time",
        prepare_next_turn=prepare,
    )
    agent.set_model(dummy_model)

    await agent.prompt(UserMessage(role="user", content="start"))

    assert calls == 3
    user_messages = [m for m in agent.state.messages if m.role == "user"]
    assert [m.content for m in user_messages] == ["start", "A", "B"]


@pytest.mark.asyncio
async def test_steering_mode_all(dummy_model):
    """steering_mode='all' 一次性 drain 所有 steering 消息。"""
    calls = 0
    enqueued = False

    async def stream_fn(model, context, options):
        nonlocal calls
        calls += 1
        return _text_stream(model, f"turn-{calls}")

    async def prepare(ctx):
        nonlocal enqueued
        if not enqueued:
            enqueued = True
            agent.steer(UserMessage(role="user", content="A"))
            agent.steer(UserMessage(role="user", content="B"))
        return AgentLoopTurnUpdate()

    agent = Agent(
        stream_fn=stream_fn,
        steering_mode="all",
        prepare_next_turn=prepare,
    )
    agent.set_model(dummy_model)

    await agent.prompt(UserMessage(role="user", content="start"))

    assert calls == 2


@pytest.mark.asyncio
async def test_follow_up_mode_one_at_a_time(dummy_model):
    """follow_up_mode='one-at-a-time' 每次只 drain 一条 follow_up 消息。"""
    calls = 0

    async def stream_fn(model, context, options):
        nonlocal calls
        calls += 1
        return _text_stream(model, f"turn-{calls}")

    agent = Agent(stream_fn=stream_fn, follow_up_mode="one-at-a-time")
    agent.set_model(dummy_model)
    agent.follow_up(UserMessage(role="user", content="A"))
    agent.follow_up(UserMessage(role="user", content="B"))

    await agent.prompt(UserMessage(role="user", content="start"))

    assert calls == 3


@pytest.mark.asyncio
async def test_follow_up_mode_all(dummy_model):
    """follow_up_mode='all' 一次性 drain 所有 follow_up 消息。"""
    calls = 0

    async def stream_fn(model, context, options):
        nonlocal calls
        calls += 1
        return _text_stream(model, f"turn-{calls}")

    agent = Agent(stream_fn=stream_fn, follow_up_mode="all")
    agent.set_model(dummy_model)
    agent.follow_up(UserMessage(role="user", content="A"))
    agent.follow_up(UserMessage(role="user", content="B"))

    await agent.prompt(UserMessage(role="user", content="start"))

    assert calls == 2


# ------------------------------------------------------------------------------
# prepare_next_turn replacing model / thinking_level
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_next_turn_replaces_model(dummy_model):
    """prepare_next_turn 返回的新 model 应在下一轮请求中生效。"""
    model2 = dummy_model.model_copy(update={"id": "model-2"})
    step = 0
    seen_models = []

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        seen_models.append(model.id)
        if step == 1:
            return _tool_call_stream(model, "echo", {"message": "world"})
        return _text_stream(model, "ok")

    async def prepare(ctx):
        if step == 1:
            return AgentLoopTurnUpdate(model=model2)
        return AgentLoopTurnUpdate()

    agent = Agent(stream_fn=stream_fn, prepare_next_turn=prepare)
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt(UserMessage(role="user", content="run"))

    assert seen_models == ["mock-model", "model-2"]


# ------------------------------------------------------------------------------
# Continue / run_agent_loop_continue
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_loop_continue_api(dummy_model):
    """低层 run_agent_loop_continue 能从当前 context 继续。"""
    context = AgentContext(
        system_prompt="sys",
        messages=[UserMessage(role="user", content="continue me")],
    )
    config = AgentLoopConfig(
        model=dummy_model,
        stream_fn=lambda m, c, o: _text_stream(m, "continued"),
    )

    async def emit(event):
        pass

    new_messages = await run_agent_loop_continue(context, config, emit)

    assert any(m.role == "assistant" for m in new_messages)
    assert context.messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_agent_continue_from_assistant_with_queued_steering(dummy_model):
    """Agent.continue_() 从 assistant 消息继续时会先处理 steering 队列。"""
    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "reply"))
    agent.set_model(dummy_model)
    agent.replace_messages(
        [
            UserMessage(role="user", content="hi"),
            _make_assistant_message(dummy_model, [TextContent(text="ok")]),
        ]
    )
    agent.steer(UserMessage(role="user", content="steer"))

    await agent.continue_()

    assert agent.state.messages[-1].role == "assistant"
    assert any(m.content == "steer" for m in agent.state.messages if m.role == "user")


@pytest.mark.asyncio
async def test_agent_continue_from_tool_result(dummy_model):
    """Agent.continue_() 能从 toolResult 消息继续。"""
    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "reply"))
    agent.set_model(dummy_model)
    agent.replace_messages(
        [
            UserMessage(role="user", content="hi"),
            _make_assistant_message(
                dummy_model,
                [ToolCall(id="tc-1", name="echo", arguments={})],
                stop_reason="toolUse",
            ),
            ToolResultMessage(
                role="toolResult",
                tool_call_id="tc-1",
                tool_name="echo",
                content=[TextContent(text="result")],
            ),
        ]
    )

    await agent.continue_()

    assert agent.state.messages[-1].role == "assistant"


# ------------------------------------------------------------------------------
# Lifecycle, concurrency, listeners
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_with_multiple_messages(dummy_model):
    """Agent.prompt 支持传入多条初始消息。"""
    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "ok"))
    agent.set_model(dummy_model)

    events: List[str] = []
    agent.subscribe(lambda e: events.append(e.type))

    await agent.prompt(
        [
            UserMessage(role="user", content="first"),
            UserMessage(role="user", content="second"),
        ]
    )

    assert events.count("message_start") == 3  # two prompts + one assistant
    assert events.count("message_end") == 3
    assert [m.role for m in agent.state.messages] == ["user", "user", "assistant"]


@pytest.mark.asyncio
async def test_sync_and_async_listeners(dummy_model):
    """同步与异步 listener 都应收到事件。"""
    sync_events: List[str] = []
    async_events: List[str] = []

    async def async_listener(event):
        async_events.append(event.type)

    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "ok"))
    agent.set_model(dummy_model)
    agent.subscribe(lambda e: sync_events.append(e.type))
    agent.subscribe(async_listener)

    await agent.prompt(UserMessage(role="user", content="hi"))

    assert "agent_start" in sync_events
    assert "agent_end" in async_events


@pytest.mark.asyncio
async def test_tool_execution_update_event(dummy_model):
    """工具通过 on_update 推送中间结果时应发出 tool_execution_update 事件。"""
    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(model, "updating", {})
        return _text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    agent.set_tools([UpdatingTool()])

    updates: List[ToolExecutionUpdateEvent] = []

    def listener(event):
        if event.type == "tool_execution_update":
            updates.append(event)

    agent.subscribe(listener)
    await agent.prompt(UserMessage(role="user", content="call"))

    assert len(updates) == 1
    assert updates[0].partial_result.content[0].text == "partial"


@pytest.mark.asyncio
async def test_reset_clears_state_and_queues(dummy_model):
    """Agent.reset() 应清空消息、steering/follow_up 队列与错误状态。"""
    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "ok"))
    agent.set_model(dummy_model)
    agent.append_message(UserMessage(role="user", content="x"))
    agent.steer(UserMessage(role="user", content="s"))
    agent.follow_up(UserMessage(role="user", content="f"))
    agent.state.error_message = "error"

    agent.reset()

    assert agent.state.messages == []
    assert not agent.has_queued_messages()
    assert agent.state.error_message is None


@pytest.mark.asyncio
async def test_concurrent_prompt_raises(dummy_model):
    """Agent 正在处理时再次调用 prompt 应抛出 RuntimeError。"""
    started = asyncio.Event()

    async def slow_stream(model, context, options):
        started.set()
        await asyncio.sleep(0.2)
        return _text_stream(model, "ok")

    agent = Agent(stream_fn=slow_stream)
    agent.set_model(dummy_model)

    task = asyncio.create_task(agent.prompt(UserMessage(role="user", content="hi")))
    await started.wait()

    with pytest.raises(RuntimeError, match="already processing"):
        await agent.prompt(UserMessage(role="user", content="again"))

    await task


@pytest.mark.asyncio
async def test_message_update_events_for_text_prefix(dummy_model):
    """tool call 前面带文本前缀时也应正确产生 message_update 事件。"""
    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(
                model, "echo", {"message": "world"}, text_prefix="thinking..."
            )
        return _text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)

    updates: List[MessageUpdateEvent] = []

    def listener(event):
        if event.type == "message_update":
            updates.append(event)

    agent.subscribe(listener)
    await agent.prompt(UserMessage(role="user", content="call"))

    # text_prefix 产生 text_delta + 可能的 toolcall 事件；至少存在一条 message_update
    assert len(updates) >= 1
