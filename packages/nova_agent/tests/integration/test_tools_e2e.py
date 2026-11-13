"""真实模型工具调用与本地工具 hooks 的端到端契约。"""

import pytest
from _helpers.messages import text_of, tool_result_message
from integration._helpers.tools import EchoTool, ErrorTool, GetDateTool, GetTimeTool
from nova_protocol import TextContent, ToolResultMessage

from nova_agent import AfterToolCallResult, Agent, BeforeToolCallResult

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),
]


def _only_tool_result(agent: Agent) -> ToolResultMessage:
    return tool_result_message(
        next(
            message for message in agent.state.messages if message.role == "toolResult"
        )
    )


async def test_tool_result_roundtrip(runtime):
    manager, model = runtime
    agent = Agent(stream_fn=manager.stream_simple)
    agent.set_model(model)
    agent.set_tools([EchoTool()])

    await agent.prompt('必须调用 echo 工具，参数 {"message": "hello"}。')

    result = _only_tool_result(agent)
    assert result.is_error is False
    assert result.content == [TextContent(text="echo: hello")]
    assert agent.state.messages[-1].role == "assistant"


async def test_tool_exception_becomes_error_result(runtime):
    manager, model = runtime
    agent = Agent(stream_fn=manager.stream_simple)
    agent.set_model(model)
    agent.set_tools([ErrorTool()])

    await agent.prompt("必须调用 error_tool 工具，不要解释。")

    result = _only_tool_result(agent)
    assert result.is_error is True
    assert "intentional tool error" in text_of(result)


async def test_before_tool_call_blocks_real_execution(runtime):
    manager, model = runtime
    called = False

    async def before(ctx, signal):
        nonlocal called
        called = True
        return BeforeToolCallResult(block=True, reason="blocked by integration test")

    agent = Agent(stream_fn=manager.stream_simple, before_tool_call=before)
    agent.set_model(model)
    agent.set_tools([EchoTool()])
    await agent.prompt('必须调用 echo 工具，参数 {"message": "hello"}。')

    assert called
    result = _only_tool_result(agent)
    assert result.is_error is True
    assert result.content == [TextContent(text="blocked by integration test")]


async def test_after_tool_call_overrides_real_result(runtime):
    manager, model = runtime

    async def after(ctx, signal):
        return AfterToolCallResult(content=[TextContent(text="overridden")])

    agent = Agent(stream_fn=manager.stream_simple, after_tool_call=after)
    agent.set_model(model)
    agent.set_tools([EchoTool()])
    await agent.prompt('必须调用 echo 工具，参数 {"message": "hello"}。')

    assert _only_tool_result(agent).content == [TextContent(text="overridden")]


async def test_multiple_tool_calls_all_receive_results(runtime):
    manager, model = runtime
    agent = Agent(stream_fn=manager.stream_simple)
    agent.set_model(model)
    agent.set_tools([GetDateTool(), GetTimeTool()])

    await agent.prompt("必须同时调用 get_date 和 get_time 两个工具，然后汇总结果。")

    results = [
        message
        for message in agent.state.messages
        if isinstance(message, ToolResultMessage)
    ]
    assert {result.tool_name for result in results} == {"get_date", "get_time"}
    assert all(not result.is_error for result in results)
