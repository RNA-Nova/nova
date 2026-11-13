"""agent_loop / agent_loop_continue facade（EventStream 包装）测试。"""

import asyncio
from typing import NoReturn

import pytest
from _helpers.agent import (
    EchoTool,
    make_assistant_message,
    text_stream,
    tool_call_then_text_stream,
)
from _helpers.messages import assistant_message, text_of, tool_result_message
from nova_ai import AssistantMessageEventStream, SimpleStreamOptions
from nova_protocol import Context, Model, StartEvent, TextContent, UserMessage

from nova_agent import AgentContext, AgentLoopConfig, agent_loop, agent_loop_continue


def _config(dummy_model) -> AgentLoopConfig:
    return AgentLoopConfig(stream_options=SimpleStreamOptions(), model=dummy_model)


@pytest.mark.asyncio
async def test_agent_loop_stream_events_and_result(dummy_model):
    """facade 返回的流可消费完整事件序列，result() 返回全部新消息。"""
    stream = agent_loop(
        [UserMessage(role="user", content="hi")],
        AgentContext(messages=[]),
        _config(dummy_model),
        stream_fn=lambda model, context, options: text_stream(model, "reply"),
    )

    event_types = [event.type async for event in stream]
    assert event_types[0] == "agent_start"
    assert "turn_start" in event_types
    assert "message_start" in event_types
    assert "message_end" in event_types
    assert event_types[-1] == "agent_end"

    messages = await stream.result()
    assert [message.role for message in messages] == ["user", "assistant"]
    assert text_of(assistant_message(messages[-1])) == "reply"


@pytest.mark.asyncio
async def test_agent_loop_tool_flow_result_includes_tool_result(dummy_model):
    """带工具调用的 run，result() 包含成功执行的 toolResult。"""
    stream = agent_loop(
        [UserMessage(role="user", content="call")],
        AgentContext(messages=[], tools=[EchoTool()]),
        _config(dummy_model),
        stream_fn=tool_call_then_text_stream("echo", {"message": "x"}),
    )
    async for _ in stream:
        pass

    messages = await stream.result()
    tool_result = tool_result_message(
        next(message for message in messages if message.role == "toolResult")
    )
    assert tool_result.is_error is False
    assert tool_result.content == [TextContent(text="echo: x")]
    assert messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_agent_loop_error_propagates_to_result(dummy_model):
    """stream_fn 抛错时，facade 把异常端到 result()（不静默）。"""

    def broken_stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> NoReturn:
        raise RuntimeError("stream exploded")

    stream = agent_loop(
        [UserMessage(role="user", content="hi")],
        AgentContext(messages=[]),
        _config(dummy_model),
        stream_fn=broken_stream,
    )
    async for _ in stream:
        pass

    with pytest.raises(RuntimeError, match="stream exploded"):
        await stream.result()


def test_agent_loop_continue_validates_context_synchronously(dummy_model):
    """agent_loop_continue 的 context 校验是同步的（在创建流之前）。"""
    config = _config(dummy_model)

    with pytest.raises(ValueError, match="no messages"):
        agent_loop_continue(AgentContext(messages=[]), config)

    assistant = make_assistant_message(dummy_model, [])
    with pytest.raises(ValueError, match="assistant"):
        agent_loop_continue(AgentContext(messages=[assistant]), config)


@pytest.mark.asyncio
async def test_agent_loop_continue_streams_events(dummy_model):
    """agent_loop_continue 正常路径：从 user 消息继续并产出事件。"""
    context = AgentContext(messages=[UserMessage(role="user", content="continue me")])
    stream = agent_loop_continue(
        context,
        _config(dummy_model),
        stream_fn=lambda model, context, options: text_stream(model, "continued"),
    )

    event_types = [event.type async for event in stream]
    assert event_types[0] == "agent_start"
    assert event_types[-1] == "agent_end"

    messages = await stream.result()
    assert text_of(assistant_message(messages[-1])) == "continued"


@pytest.mark.asyncio
async def test_agent_loop_applies_default_identity(dummy_model):
    """低层 facade 与高层 Agent 一样，缺省也携带 Nova 请求身份。"""
    identities = []

    async def stream_fn(model, context, options):
        identities.append(options.identity)
        return text_stream(model, "ok")

    stream = agent_loop(
        [UserMessage(role="user", content="hi")],
        AgentContext(messages=[]),
        _config(dummy_model),
        stream_fn=stream_fn,
    )
    async for _ in stream:
        pass
    await stream.result()

    identity = identities[0]
    assert identity is not None
    assert identity.attribution.name == "nova"


@pytest.mark.asyncio
async def test_agent_loop_external_cancel_abandons_and_joins_provider(dummy_model):
    started = asyncio.Event()
    finished = asyncio.Event()
    signals = []
    drivers = []

    def stream_fn(model, context, options):
        provider = AssistantMessageEventStream()
        provider.push(StartEvent(partial=make_assistant_message(model, [])))
        signals.append(options.signal)

        async def drive_provider():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        drivers.append(provider.drive(drive_provider()))
        return provider

    stream = agent_loop(
        [UserMessage(role="user", content="hi")],
        AgentContext(messages=[]),
        _config(dummy_model),
        stream_fn=stream_fn,
    )
    await started.wait()
    assert stream.task is not None
    stream.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stream.task

    await asyncio.gather(*drivers, return_exceptions=True)
    assert finished.is_set()
    signal = signals[0]
    assert signal is not None and signal.aborted
