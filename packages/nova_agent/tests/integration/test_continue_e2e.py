"""真实 provider 上从工具结果续跑的契约。"""

import pytest
from _helpers.messages import assistant_message
from integration._helpers.tools import EchoTool, GetDateTool, GetTimeTool
from nova_protocol import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from nova_agent import Agent

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),
]


async def test_continue_from_tool_result(runtime):
    manager, model = runtime
    agent = Agent(stream_fn=manager.stream_simple)
    agent.set_model(model)
    agent.set_tools([EchoTool()])
    call = ToolCall(id="call-1", name="echo", arguments={"message": "continue"})
    agent.replace_messages(
        [
            UserMessage(content="请处理工具结果。"),
            AssistantMessage(content=[call]),
            ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=[TextContent(text="echo: continue")],
            ),
        ]
    )

    await agent.continue_()

    reply = assistant_message(agent.state.messages[-1])
    assert reply.error_message is None


async def test_continue_with_multiple_tool_results(runtime):
    manager, model = runtime
    agent = Agent(stream_fn=manager.stream_simple)
    agent.set_model(model)
    agent.set_tools([GetDateTool(), GetTimeTool()])
    date_call = ToolCall(id="date-1", name="get_date", arguments={})
    time_call = ToolCall(id="time-1", name="get_time", arguments={})
    agent.replace_messages(
        [
            UserMessage(content="请汇总日期和时间。"),
            AssistantMessage(content=[date_call, time_call]),
            ToolResultMessage(
                tool_call_id=date_call.id,
                tool_name=date_call.name,
                content=[TextContent(text="2026-06-10")],
            ),
            ToolResultMessage(
                tool_call_id=time_call.id,
                tool_name=time_call.name,
                content=[TextContent(text="12:00")],
            ),
        ]
    )

    await agent.continue_()

    reply = assistant_message(agent.state.messages[-1])
    assert reply.error_message is None
