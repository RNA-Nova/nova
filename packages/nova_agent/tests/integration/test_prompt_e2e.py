"""真实 provider 上的 prompt、事件生命周期与请求上下文契约。"""

import json

import pytest
from _helpers.messages import assistant_message, text_of
from integration._helpers.tools import extract_text

from nova_agent import Agent

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),
]


async def test_prompt_publishes_complete_agent_lifecycle(runtime):
    manager, model = runtime
    agent = Agent(stream_fn=manager.stream_simple)
    agent.set_model(model)
    events = []
    agent.subscribe(lambda event, _signal: events.append(event.type))

    await agent.prompt("hi")

    assert events[0] == "agent_start"
    assert events[-1] == "agent_end"
    assert events.index("agent_start") < events.index("message_end")
    assert events.index("message_end") < events.index("agent_end")
    assert [message.role for message in agent.state.messages[:2]] == [
        "user",
        "assistant",
    ]
    reply = assistant_message(agent.state.messages[-1])
    assert reply.error_message is None
    assert extract_text(reply)


async def test_second_request_contains_first_turn_marker(runtime):
    manager, model = runtime
    payloads = []
    agent = Agent(
        stream_fn=manager.stream_simple,
        on_payload=lambda payload, _identity: payloads.append(payload),
    )
    agent.set_model(model)
    marker = "nova-agent-memory-marker-731"

    await agent.prompt(f"请记住标记 {marker}，只回复 ok。")
    await agent.prompt("继续对话，只回复 ok。")

    assert len(payloads) >= 2
    assert marker in json.dumps(payloads[1], ensure_ascii=False)


async def test_system_prompt_reaches_provider_payload(runtime):
    manager, model = runtime
    payloads = []
    marker = "nova-agent-system-marker-952"
    agent = Agent(
        stream_fn=manager.stream_simple,
        initial_state={"system_prompt": marker},
        on_payload=lambda payload, _identity: payloads.append(payload),
    )
    agent.set_model(model)

    await agent.prompt("hi")

    assert len(payloads) == 1
    assert marker in json.dumps(payloads[0], ensure_ascii=False)
