"""
nova_agent Agent 包装类单元测试

覆盖 Agent 级 API：状态管理、订阅、生命周期、队列、hook 透传、on_payload/on_response。
"""

import asyncio
from typing import Any, List

import pytest
from helpers import (
    AbortableTool,
    EchoTool,
    abortable_tool_call_stream,
    make_assistant_message,
    text_stream,
    tool_call_stream,
    tool_call_then_text_stream,
)
from nova_ai import (
    DoneEvent,
    EventStream,
    ImageContent,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    ProviderResponse,
    StartEvent,
    TextContent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCall,
    UserMessage,
)

from nova_agent import (
    Agent,
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
)

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
    assert config.stream_options.timeout == 123.0


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
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    agent.set_model(dummy_model)

    events: List[str] = []

    def listener(event, signal=None):
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
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "hello"))
    agent.set_model(dummy_model)

    events: List[str] = []
    agent.subscribe(lambda e, signal=None: events.append(e.type))

    await agent.prompt(UserMessage(role="user", content=[TextContent(text="hi")]))

    assert "agent_start" in events
    assert "agent_end" in events
    assert [m.role for m in agent.state.messages] == ["user", "assistant"]
    assert agent.state.messages[-1].content[0].text == "hello"


@pytest.mark.asyncio
async def test_agent_prompt_with_string_and_images(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    agent.set_model(dummy_model)

    await agent.prompt(
        "look",
        images=[ImageContent(type="image", data="base64", mime_type="image/png")],
    )

    last = agent.state.messages[-1]
    assert last.role == "assistant"


@pytest.mark.asyncio
async def test_agent_continue_from_user_message(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "continued"))
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
            return text_stream(model, "first")
        return text_stream(model, "steered")

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
            return text_stream(model, "first")
        return text_stream(model, "followed")

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
        stream_fn=abortable_tool_call_stream("abortable", {}),
        before_tool_call=before,
    )
    agent.set_model(dummy_model)
    agent.set_tools([AbortableTool()])

    events: List[str] = []
    agent.subscribe(lambda e, signal=None: events.append(e.type))

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
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    agent.set_model(dummy_model)

    await agent.prompt("hi")
    await agent.wait_for_idle()
    assert not agent.state.is_streaming


# ------------------------------------------------------------------------------
# on_payload / on_response
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_on_payload_and_on_response(dummy_model):
    payloads: List[Any] = []
    responses: List[ProviderResponse] = []

    def on_payload(payload, model):
        payloads.append((payload, model.id))

    def on_response(response, model):
        responses.append(response)

    def stream_fn(model, context, options):
        if options and options.on_payload:
            options.on_payload({"model": model.id}, model)
        if options and options.on_response:
            options.on_response(
                ProviderResponse(status=200, headers={"x-test": "yes"}), model
            )
        return text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn, on_payload=on_payload, on_response=on_response)
    agent.set_model(dummy_model)

    await agent.prompt("hi")

    assert len(payloads) == 1
    assert payloads[0][0]["model"] == dummy_model.id
    assert payloads[0][1] == dummy_model.id
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
        stream_fn=lambda m, c, o: text_stream(m, "ok"), convert_to_llm=convert
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
        stream_fn=lambda m, c, o: text_stream(m, "ok"), transform_context=transform
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

    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"), get_api_key=get_key)
    agent.set_model(dummy_model)

    await agent.prompt("hi")
    assert called == [dummy_model.provider]


# ------------------------------------------------------------------------------
# 无模型 fail-fast
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_prompt_without_model_raises():
    """未配置模型时 prompt 应立即报错，而不是走到 provider 层失败。"""
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    assert not agent.state.has_configured_model()

    with pytest.raises(RuntimeError, match="No model configured"):
        await agent.prompt("hi")

    assert agent.state.messages == []
    assert not agent.state.is_streaming


# ------------------------------------------------------------------------------
# 监听器顺序与派发语义
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_listeners_called_in_subscription_order(dummy_model):
    """监听器按订阅顺序逐个被调用（dict 保序）。"""
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    agent.set_model(dummy_model)

    calls: List[str] = []
    agent.subscribe(lambda e, signal=None: calls.append("first"))
    agent.subscribe(lambda e, signal=None: calls.append("second"))

    await agent.prompt("hi")

    assert len(calls) > 0
    assert len(calls) % 2 == 0
    assert calls == ["first", "second"] * (len(calls) // 2)


@pytest.mark.asyncio
async def test_agent_listener_unsubscribe_during_dispatch(dummy_model):
    """监听器在回调中退订其他监听器，不应打断或破坏本次派发（快照迭代）。"""
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    agent.set_model(dummy_model)

    seen: List[tuple] = []
    holder: dict = {}

    def second(event, signal=None):
        seen.append(("second", event.type))

    def first(event, signal=None):
        seen.append(("first", event.type))
        holder["unsub_second"]()

    holder["unsub_second"] = agent.subscribe(second)
    agent.subscribe(first)

    await agent.prompt("hi")

    # 快照按事件派发：second 在 agent_start 派发期间被退订，
    # 该事件不受影响（second 已收到），从下一个事件起不再收到。
    second_events = [t for name, t in seen if name == "second"]
    first_events = [t for name, t in seen if name == "first"]
    assert second_events == ["agent_start"]
    assert len(first_events) > len(second_events)
    assert first_events[0] == "agent_start"
    assert "agent_end" in first_events


@pytest.mark.asyncio
async def test_agent_sync_listener_returning_coroutine_is_awaited(dummy_model):
    """同步函数返回 coroutine 的监听器也应被 await（isawaitable 语义）。"""
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    agent.set_model(dummy_model)

    seen: List[str] = []

    async def record(event_type: str) -> None:
        seen.append(event_type)

    def listener(event, signal=None):
        # 非 async def，但返回 coroutine
        return record(event.type)

    agent.subscribe(listener)
    await agent.prompt("hi")

    assert "agent_start" in seen
    assert "agent_end" in seen


# ------------------------------------------------------------------------------
# continue_ 错误路径
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_continue_without_messages_raises(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    agent.set_model(dummy_model)

    with pytest.raises(RuntimeError, match="No messages to continue from"):
        await agent.continue_()


@pytest.mark.asyncio
async def test_agent_continue_from_assistant_without_queued_raises(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    agent.set_model(dummy_model)
    await agent.prompt("hi")

    # 末尾是 assistant 且 steering / follow_up 队列均为空
    with pytest.raises(RuntimeError, match="assistant"):
        await agent.continue_()


# ------------------------------------------------------------------------------
# thinking 内容块
# ------------------------------------------------------------------------------


def _thinking_stream(model: Model) -> EventStream:
    stream = EventStream(
        is_complete=lambda e: getattr(e, "type", None) == "done",
        extract_result=lambda e: e.message,
    )
    partial = make_assistant_message(
        model,
        [
            ThinkingContent(type="thinking", thinking="let me think"),
            TextContent(text="answer"),
        ],
    )
    stream.push(StartEvent(partial=partial))
    stream.push(ThinkingStartEvent(content_index=0, partial=partial))
    stream.push(
        ThinkingDeltaEvent(content_index=0, delta="let me think", partial=partial)
    )
    stream.push(
        ThinkingEndEvent(content_index=0, content="let me think", partial=partial)
    )
    stream.push(DoneEvent(reason="stop", message=partial))
    stream.end()
    return stream


@pytest.mark.asyncio
async def test_agent_thinking_content_preserved(dummy_model):
    """流式 thinking 事件后，assistant 消息保留 thinking 内容块。"""
    agent = Agent(stream_fn=lambda m, c, o: _thinking_stream(m))
    agent.set_model(dummy_model)

    await agent.prompt("hi")

    last = agent.state.messages[-1]
    assert last.role == "assistant"
    assert [c.type for c in last.content] == ["thinking", "text"]
    assert last.content[0].thinking == "let me think"
    assert last.content[1].text == "answer"


# ------------------------------------------------------------------------------
# pending_tool_calls 状态跟踪
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_pending_tool_calls_tracked(dummy_model):
    """tool_execution_start/end 期间 pending_tool_calls 正确增减，run 结束后清空。"""
    agent = Agent(stream_fn=tool_call_then_text_stream("echo", {"message": "x"}))
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    observed: List[bool] = []

    def listener(event, signal=None):
        if event.type == "tool_execution_start":
            observed.append(event.tool_call_id in agent.state.pending_tool_calls)
        elif event.type == "tool_execution_end":
            observed.append(event.tool_call_id in agent.state.pending_tool_calls)

    agent.subscribe(listener)
    await agent.prompt("run")

    assert observed == [True, False]
    assert agent.state.pending_tool_calls == set()


# ------------------------------------------------------------------------------
# initial_state 严格校验 / wait_for_idle shield / convert_to_llm 无 role 过滤
# ------------------------------------------------------------------------------


def test_initial_state_rejects_unknown_keys(dummy_model):
    """dict 形式 initial_state 含未知 key 时应立即 TypeError，而不是静默忽略。"""
    with pytest.raises(TypeError, match="Unknown initial_state keys"):
        Agent(initial_state={"model": dummy_model, "bogus_key": 1})


def test_default_convert_to_llm_filters_messages_without_role(dummy_model):
    """不带 role 字段的自定义消息应被默认 convert_to_llm 过滤而不是炸掉。"""
    from nova_agent import CustomAgentMessage
    from nova_agent.utils import default_convert_to_llm

    class Notification(CustomAgentMessage):
        text: str = ""

    messages = [
        UserMessage(role="user", content="hi"),
        Notification(text="ui only"),
    ]
    out = default_convert_to_llm(messages)
    assert len(out) == 1
    assert out[0].role == "user"


@pytest.mark.asyncio
async def test_wait_for_idle_shielded_from_cancellation(dummy_model):
    """等待 wait_for_idle 的协程被取消时，正在运行的 run 不应被传染取消。"""

    async def slow_stream(model, context, options):
        # 让 run 持续一小段时间，保证 waiter 取消时 run 仍在进行
        await asyncio.sleep(0.05)
        return text_stream(model, "ok")

    agent = Agent(stream_fn=slow_stream)
    agent.set_model(dummy_model)

    run_task = asyncio.create_task(agent.prompt("hi"))
    # 等 run 真正开始（_running_task 已创建）
    for _ in range(100):
        if agent.state.is_streaming:
            break
        await asyncio.sleep(0)
    assert agent.state.is_streaming

    waiter = asyncio.create_task(agent.wait_for_idle())
    # 让 waiter 进入 shield 等待
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    # run 不被影响，正常完成
    await run_task
    assert agent.state.messages[-1].role == "assistant"
    assert not agent.state.is_streaming
