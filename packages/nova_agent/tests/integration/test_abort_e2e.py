"""真实 provider 流与真实工具执行中的 abort 契约。"""

import asyncio

import pytest
from _helpers.messages import assistant_message, tool_result_message
from integration._helpers.tools import SlowTool
from nova_protocol import StopReason

from nova_agent import Agent

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),
]


async def test_abort_during_provider_stream_has_aborted_terminal_state(runtime):
    manager, model = runtime
    agent = Agent(stream_fn=manager.stream_simple)
    agent.set_model(model)
    aborted = asyncio.Event()

    def abort_on_update(event, _signal):
        if event.type == "message_update" and not aborted.is_set():
            aborted.set()
            agent.abort()

    agent.subscribe(abort_on_update)
    run = agent.start_prompt("请详细解释量子计算，内容越长越好。")
    await run

    assert aborted.is_set()
    assert assistant_message(agent.state.messages[-1]).stop_reason is StopReason.ABORTED
    assert not agent.state.is_streaming


async def test_abort_during_tool_execution_finishes_tool_result(runtime):
    manager, model = runtime
    agent = Agent(stream_fn=manager.stream_simple)
    agent.set_model(model)
    agent.set_tools([SlowTool()])
    tool_started = asyncio.Event()

    def on_event(event, _signal):
        if event.type == "tool_execution_start":
            tool_started.set()

    agent.subscribe(on_event)
    run = agent.start_prompt("必须调用 slow_tool 工具，调用后保持等待。")
    await tool_started.wait()
    agent.abort()
    await run

    result = tool_result_message(
        next(
            message for message in agent.state.messages if message.role == "toolResult"
        )
    )
    assert result.is_error is True
    assert not agent.state.is_streaming
