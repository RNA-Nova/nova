"""Agent 回调与回合 hooks 在真实请求管线中的契约。"""

import pytest
from nova_protocol import SystemMessage

from nova_agent import Agent, AgentLoopTurnUpdate, AgentTurnDecision

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),
]


async def test_payload_and_response_callbacks(runtime):
    manager, model = runtime
    payloads = []
    responses = []
    agent = Agent(
        stream_fn=manager.stream_simple,
        on_payload=lambda payload, _identity: payloads.append(payload),
        on_response=lambda response, _identity: responses.append(response),
    )
    agent.set_model(model)

    await agent.prompt("hi")

    assert len(payloads) == 1
    assert isinstance(payloads[0], dict)
    assert len(responses) == 1
    assert responses[0].status == 200
    assert "content-type" in {key.lower() for key in responses[0].headers}


async def test_finish_turn_end_stops_after_one_request(runtime):
    manager, model = runtime
    calls = 0

    def finish(ctx, signal):
        nonlocal calls
        calls += 1
        return AgentTurnDecision(action="end")

    agent = Agent(stream_fn=manager.stream_simple, finish_turn=finish)
    agent.set_model(model)
    await agent.prompt("hi")

    assert calls == 1
    assert (
        len(
            [message for message in agent.state.messages if message.role == "assistant"]
        )
        == 1
    )


async def test_prepare_next_turn_adds_context_for_second_request(runtime):
    manager, model = runtime
    prepared = 0
    finished = 0

    def prepare(ctx, signal):
        nonlocal prepared
        prepared += 1
        return AgentLoopTurnUpdate(messages=[SystemMessage(content="prepared-marker")])

    def finish(ctx, signal):
        nonlocal finished
        finished += 1
        if finished == 1:
            return AgentTurnDecision(action="continue")
        return AgentTurnDecision(action="end")

    agent = Agent(
        stream_fn=manager.stream_simple,
        prepare_next_turn=prepare,
        finish_turn=finish,
    )
    agent.set_model(model)
    await agent.prompt("hi")

    assert prepared == 1
    assert any(
        isinstance(message, SystemMessage) and message.content == "prepared-marker"
        for message in agent.state.messages
    )
    assert (
        len(
            [message for message in agent.state.messages if message.role == "assistant"]
        )
        == 2
    )
