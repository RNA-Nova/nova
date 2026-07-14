"""
nova_agent Agent 包装类单元测试

覆盖 Agent 级 API：状态管理、订阅、生命周期、队列、hook 透传、on_payload/on_response。
"""

import asyncio
from typing import Any, Callable, List

import pytest

from nova_agent import (
    Agent,
    AgentContext,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentTool,
    AgentToolResult,
    AbortSignal,
    AfterToolCallContext,
    AfterToolCallResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    PrepareNextTurnContext,
    ShouldStopAfterTurnContext,
)
from nova_ai import (
    AssistantMessage,
    DoneEvent,
    EventStream,
    ImageContent,
    Model,
    ProviderResponse,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    ToolCall,
    ToolCallEndEvent,
    UserMessage,
)
from nova_ai import KnownApi, KnownProvider, ModelCost


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
            details={},
        )


class AbortableTool(AgentTool):
    """可中断工具，用于测试 agent.abort()。"""

    name: str = "abortable"
    description: str = "Waits for abort signal"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        for _ in range(100):
            if signal and signal.aborted:
                raise Exception("Operation aborted")
            await asyncio.sleep(0.01)
        return AgentToolResult(content=[TextContent(text="done")], details={})


@pytest.fixture
def dummy_model() -> Model:
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


def _tool_call_stream(model: Model, tool_name: str, arguments: dict) -> EventStream:
    stream = EventStream(
        is_complete=lambda e: getattr(e, "type", None) == "done",
        extract_result=lambda e: e.message,
    )
    tool_call = ToolCall(id="tc-1", name=tool_name, arguments=arguments)
    partial = _make_assistant_message(model, [tool_call], stop_reason="toolUse")
    stream.push(StartEvent(partial=partial))
    stream.push(ToolCallEndEvent(content_index=0, tool_call=tool_call, partial=partial))
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
            partial = _make_assistant_message(
                model, [TextContent(text="aborted")], stop_reason="aborted"
            )
            stream = EventStream(
                is_complete=lambda e: getattr(e, "type", None) == "done",
                extract_result=lambda e: e.message,
            )
            stream.push(StartEvent(partial=partial))
            stream.push(DoneEvent(reason="aborted", message=partial))
            stream.end()
            return stream
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


# ------------------------------------------------------------------------------
# 初始化与状态
# ------------------------------------------------------------------------------


def test_agent_init_defaults(dummy_model):
    agent = Agent()
    assert agent.state is not None
    assert agent.state.messages == []
    assert agent.state.tools == []
    assert agent.state.is_streaming is False


def test_agent_init_custom_state(dummy_model):
    agent = Agent(
        initial_state={
            "system_prompt": "hello",
            "messages": [UserMessage(role="user", content=[TextContent(text="hi")])],
        }
    )
    assert agent.state.system_prompt == "hello"
    assert len(agent.state.messages) == 1


def test_agent_timeout_propagates_to_loop_config(dummy_model):
    agent = Agent(initial_state={"model": dummy_model}, timeout=123.0)
    config = agent._create_loop_config()
    assert config.timeout == 123.0


def test_agent_state_mutators(dummy_model):
    agent = Agent()
    agent.set_system_prompt("sys")
    agent.set_model(dummy_model)
    agent.set_thinking_level("medium")
    agent.set_tools([EchoTool()])
    agent.append_message(UserMessage(role="user", content=[TextContent(text="a")]))

    assert agent.state.system_prompt == "sys"
    assert agent.state.model == dummy_model
    assert agent.state.thinking_level == "medium"
    assert agent.state.tools == [EchoTool()]
    assert len(agent.state.messages) == 1

    agent.replace_messages([UserMessage(role="user", content=[TextContent(text="b")])])
    assert len(agent.state.messages) == 1
    assert agent.state.messages[0].content[0].text == "b"

    agent.clear_messages()
    assert agent.state.messages == []


# ------------------------------------------------------------------------------
# 订阅
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_subscribe_and_unsubscribe(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "ok"))
    agent.set_model(dummy_model)

    events: List[str] = []

    def listener(event):
        events.append(event.type)

    unsub = agent.subscribe(listener)
    await agent.prompt("hi")
    assert len(events) > 0

    unsub()
    events.clear()
    await agent.prompt("hi")
    assert events == []


# ------------------------------------------------------------------------------
# prompt / continue
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_prompt_basic(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "hello"))
    agent.set_model(dummy_model)

    events: List[str] = []
    agent.subscribe(lambda e: events.append(e.type))

    await agent.prompt(UserMessage(role="user", content=[TextContent(text="hi")]))

    assert "agent_start" in events
    assert "agent_end" in events
    assert [m.role for m in agent.state.messages] == ["user", "assistant"]
    assert agent.state.messages[-1].content[0].text == "hello"


@pytest.mark.asyncio
async def test_agent_prompt_with_string_and_images(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "ok"))
    agent.set_model(dummy_model)

    await agent.prompt(
        "look",
        images=[ImageContent(type="image", data="base64", mime_type="image/png")],
    )

    last = agent.state.messages[-1]
    assert last.role == "assistant"


@pytest.mark.asyncio
async def test_agent_continue_from_user_message(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "continued"))
    agent.set_model(dummy_model)
    agent.replace_messages([UserMessage(role="user", content=[TextContent(text="go")])])

    await agent.continue_()

    assert len(agent.state.messages) == 2
    assert agent.state.messages[-1].role == "assistant"
    assert agent.state.messages[-1].content[0].text == "continued"


# ------------------------------------------------------------------------------
# 队列
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_steer_queue_drained_on_continue(dummy_model):
    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _text_stream(model, "first")
        return _text_stream(model, "steered")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)

    await agent.prompt("hi")
    assert agent.state.messages[-1].content[0].text == "first"

    agent.steer(UserMessage(role="user", content=[TextContent(text="steer this")]))
    await agent.continue_()

    assert any(
        m.role == "user" and m.content[0].text == "steer this"
        for m in agent.state.messages
    )
    assert agent.state.messages[-1].role == "assistant"
    assert agent.state.messages[-1].content[0].text == "steered"


@pytest.mark.asyncio
async def test_agent_follow_up_queue_drained_on_continue(dummy_model):
    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _text_stream(model, "first")
        return _text_stream(model, "followed")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)

    await agent.prompt("hi")
    agent.follow_up(UserMessage(role="user", content=[TextContent(text="follow up")]))
    await agent.continue_()

    assert any(
        m.role == "user" and m.content[0].text == "follow up"
        for m in agent.state.messages
    )
    assert agent.state.messages[-1].content[0].text == "followed"


# ------------------------------------------------------------------------------
# 生命周期：abort / reset / 并发保护
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_abort_during_tool_execution(dummy_model):
    started = asyncio.Event()

    async def before(ctx: BeforeToolCallContext, signal):
        started.set()
        return None

    agent = Agent(
        stream_fn=_abortable_tool_call_stream("abortable", {}),
        before_tool_call=before,
    )
    agent.set_model(dummy_model)
    agent.set_tools([AbortableTool()])

    events: List[str] = []
    agent.subscribe(lambda e: events.append(e.type))

    task = asyncio.create_task(agent.prompt("run"))
    await started.wait()
    agent.abort()
    await task

    assert "tool_execution_start" in events
    assert "tool_execution_end" in events
    tool_results = [m for m in agent.state.messages if m.role == "toolResult"]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is True


@pytest.mark.asyncio
async def test_agent_wait_for_idle(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "ok"))
    agent.set_model(dummy_model)

    await agent.prompt("hi")
    await agent.wait_for_idle()
    assert not agent.state.is_streaming


def test_agent_reset(dummy_model):
    agent = Agent()
    agent.set_model(dummy_model)
    agent.append_message(UserMessage(role="user", content=[TextContent(text="x")]))
    agent.steer(UserMessage(role="user", content=[TextContent(text="y")]))
    agent.follow_up(UserMessage(role="user", content=[TextContent(text="z")]))

    agent.reset()

    assert agent.state.messages == []
    assert agent.state.error_message is None
    assert not agent.state.is_streaming
    assert not agent.has_queued_messages()


@pytest.mark.asyncio
async def test_agent_concurrent_prompt_raises(dummy_model):
    async def slow_stream(model, context, options):
        # async generator that waits a bit so the second prompt can collide
        async def gen():
            yield StartEvent(
                partial=_make_assistant_message(model, [TextContent(text="x")])
            )
            await asyncio.sleep(0.1)
            yield DoneEvent(
                reason="stop",
                message=_make_assistant_message(model, [TextContent(text="x")]),
            )

        return gen()

    agent = Agent(stream_fn=slow_stream)
    agent.set_model(dummy_model)

    task = asyncio.create_task(agent.prompt("hi"))
    await asyncio.sleep(0.01)

    with pytest.raises(RuntimeError):
        await agent.prompt("again")

    await task


# ------------------------------------------------------------------------------
# on_payload / on_response
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_on_payload_and_on_response(dummy_model):
    payloads: List[Any] = []
    responses: List[ProviderResponse] = []

    def on_payload(payload):
        payloads.append(payload)

    def on_response(response, model):
        responses.append(response)

    def stream_fn(model, context, options):
        if options and options.on_payload:
            options.on_payload({"model": model.id})
        if options and options.on_response:
            options.on_response(
                ProviderResponse(status=200, headers={"x-test": "yes"}), model
            )
        return _text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn, on_payload=on_payload, on_response=on_response)
    agent.set_model(dummy_model)

    await agent.prompt("hi")

    assert len(payloads) == 1
    assert payloads[0]["model"] == dummy_model.id
    assert len(responses) == 1
    assert responses[0].status == 200
    assert responses[0].headers["x-test"] == "yes"


# ------------------------------------------------------------------------------
# Hook 透传
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_convert_to_llm_hook(dummy_model):
    called = []

    def convert(messages):
        called.append(len(messages))
        return messages

    agent = Agent(
        stream_fn=lambda m, c, o: _text_stream(m, "ok"), convert_to_llm=convert
    )
    agent.set_model(dummy_model)

    await agent.prompt("hi")
    assert called == [1]


@pytest.mark.asyncio
async def test_agent_transform_context_hook(dummy_model):
    called = []

    async def transform(messages, signal):
        called.append(len(messages))
        return messages

    agent = Agent(
        stream_fn=lambda m, c, o: _text_stream(m, "ok"), transform_context=transform
    )
    agent.set_model(dummy_model)

    await agent.prompt("hi")
    assert called == [1]


@pytest.mark.asyncio
async def test_agent_get_api_key_hook(dummy_model):
    called = []

    def get_key(provider):
        called.append(provider)
        return "test-key"

    agent = Agent(stream_fn=lambda m, c, o: _text_stream(m, "ok"), get_api_key=get_key)
    agent.set_model(dummy_model)

    await agent.prompt("hi")
    assert called == [dummy_model.provider]


@pytest.mark.asyncio
async def test_agent_should_stop_after_turn(dummy_model):
    called = []

    async def should_stop(ctx: ShouldStopAfterTurnContext):
        called.append(True)
        return True

    agent = Agent(
        stream_fn=lambda m, c, o: _text_stream(m, "stop here"),
        should_stop_after_turn=should_stop,
    )
    agent.set_model(dummy_model)

    await agent.prompt("run")
    assert called == [True]


@pytest.mark.asyncio
async def test_agent_before_tool_call_block(dummy_model):
    async def before(ctx: BeforeToolCallContext, signal):
        return BeforeToolCallResult(block=True, reason="test")

    agent = Agent(
        stream_fn=_tool_call_then_text_stream("echo", {"message": "x"}),
        before_tool_call=before,
    )
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt("run")
    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    assert tool_result.is_error is True
    assert "test" in tool_result.content[0].text


@pytest.mark.asyncio
async def test_agent_after_tool_call_override(dummy_model):
    async def after(ctx: AfterToolCallContext, signal):
        return AfterToolCallResult(content=[TextContent(text="override")])

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return _tool_call_stream(model, "echo", {"message": "x"})
        return _text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn, after_tool_call=after)
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt("run")
    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    assert tool_result.content[0].text == "override"


@pytest.mark.asyncio
async def test_agent_prepare_next_turn_replaces_model_and_thinking_level(dummy_model):
    new_model = dummy_model.model_copy(update={"id": "new-model"})
    seen_models: List[str] = []
    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        seen_models.append(model.id)
        if step == 1:
            return _tool_call_stream(model, "echo", {"message": "x"})
        return _text_stream(model, "done")

    async def prepare(ctx: PrepareNextTurnContext):
        return AgentLoopTurnUpdate(model=new_model, thinking_level="high")

    agent = Agent(stream_fn=stream_fn, prepare_next_turn=prepare)
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt("run")

    assert seen_models == [dummy_model.id, new_model.id]
