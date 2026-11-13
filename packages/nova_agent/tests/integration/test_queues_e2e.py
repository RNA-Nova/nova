"""真实 provider 上的 steering 与 follow-up 队列契约。"""

import pytest
from nova_protocol import UserMessage

from nova_agent import Agent

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),
]


async def test_steering_during_stream_is_processed(runtime):
    manager, model = runtime
    agent = Agent(stream_fn=manager.stream_simple)
    agent.set_model(model)
    marker = "nova-agent-steering-marker"
    injected = False

    def inject_on_stream(event, _signal):
        nonlocal injected
        if event.type == "message_update" and not injected:
            injected = True
            agent.steer(UserMessage(content=marker))

    agent.subscribe(inject_on_stream)
    await agent.prompt("先回答第一条，随后处理插队消息。")

    assert injected
    assert any(
        isinstance(message, UserMessage) and message.content == marker
        for message in agent.state.messages
    )
    assert (
        len(
            [message for message in agent.state.messages if message.role == "assistant"]
        )
        >= 2
    )


async def test_follow_up_runs_after_natural_completion(runtime):
    manager, model = runtime
    agent = Agent(stream_fn=manager.stream_simple)
    agent.set_model(model)
    marker = "nova-agent-follow-up-marker"
    agent.follow_up(UserMessage(content=marker))

    await agent.prompt("先完成第一轮。")

    assert any(
        isinstance(message, UserMessage) and message.content == marker
        for message in agent.state.messages
    )
    assert (
        len(
            [message for message in agent.state.messages if message.role == "assistant"]
        )
        >= 2
    )
