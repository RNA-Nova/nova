"""agent_loop / agent_loop_continue facade（EventStream 包装）测试。"""

import pytest
from helpers import text_stream, tool_call_then_text_stream
from nova_agent import (
    AgentContext,
    AgentLoopConfig,
    agent_loop,
    agent_loop_continue,
)
from nova_ai import SimpleStreamOptions, UserMessage


def _config(dummy_model) -> AgentLoopConfig:
    return AgentLoopConfig(stream_options=SimpleStreamOptions(), model=dummy_model)


@pytest.mark.asyncio
async def test_agent_loop_stream_events_and_result(dummy_model):
    """facade 返回的流可消费完整事件序列，result() 返回全部新消息。"""
    stream = agent_loop(
        [UserMessage(role="user", content="hi")],
        AgentContext(system_prompt="sys", messages=[]),
        _config(dummy_model),
        stream_fn=lambda m, c, o: text_stream(m, "reply"),
    )

    event_types = [event.type async for event in stream]
    assert event_types[0] == "agent_start"
    assert "turn_start" in event_types
    assert "message_start" in event_types
    assert "message_end" in event_types
    assert event_types[-1] == "agent_end"

    messages = await stream.result()
    # prompt + assistant 回复都在返回值里
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[-1].content[0].text == "reply"


@pytest.mark.asyncio
async def test_agent_loop_tool_flow_result_includes_tool_result(dummy_model):
    """带工具调用的 run，result() 包含 toolResult 消息。"""
    stream = agent_loop(
        [UserMessage(role="user", content="call")],
        AgentContext(system_prompt="sys", messages=[], tools=[]),
        _config(dummy_model),
        stream_fn=tool_call_then_text_stream("echo", {"message": "x"}),
    )
    async for _ in stream:
        pass

    messages = await stream.result()
    roles = [m.role for m in messages]
    assert "toolResult" in roles
    assert roles[-1] == "assistant"


@pytest.mark.asyncio
async def test_agent_loop_error_propagates_to_result(dummy_model):
    """stream_fn 抛错时，facade 把异常端到 result()（不静默）。"""

    def broken_stream(model, context, options):
        raise RuntimeError("stream exploded")

    stream = agent_loop(
        [UserMessage(role="user", content="hi")],
        AgentContext(system_prompt="sys", messages=[]),
        _config(dummy_model),
        stream_fn=broken_stream,
    )
    async for _ in stream:
        pass

    with pytest.raises(RuntimeError, match="stream exploded"):
        await stream.result()


def test_agent_loop_continue_validates_context_synchronously(dummy_model):
    """agent_loop_continue 的 context 校验是同步的（在创建流之前）。"""
    from helpers import make_assistant_message

    config = _config(dummy_model)

    with pytest.raises(ValueError, match="no messages"):
        agent_loop_continue(AgentContext(messages=[]), config)

    assistant = make_assistant_message(dummy_model, [])
    with pytest.raises(ValueError, match="assistant"):
        agent_loop_continue(AgentContext(messages=[assistant]), config)


@pytest.mark.asyncio
async def test_agent_loop_continue_streams_events(dummy_model):
    """agent_loop_continue 正常路径：从 user 消息继续并产出事件。"""
    context = AgentContext(
        system_prompt="sys",
        messages=[UserMessage(role="user", content="continue me")],
    )
    stream = agent_loop_continue(
        context,
        _config(dummy_model),
        stream_fn=lambda m, c, o: text_stream(m, "continued"),
    )

    event_types = [event.type async for event in stream]
    assert event_types[0] == "agent_start"
    assert event_types[-1] == "agent_end"

    messages = await stream.result()
    assert messages[-1].role == "assistant"
    assert messages[-1].content[0].text == "continued"
