"""工具调用契约 e2e：工具下发/调用回传/结果往返（锁协议形态）。"""

import asyncio

import pytest
from nova_protocol import (
    Context,
    StopReason,
    TextContent,
    Tool,
    ToolResultMessage,
    UserMessage,
)

from nova_ai.stream_options import SimpleStreamOptions

from ._helpers.auth import build_manager
from ._helpers.probes import collect_probes
from ._helpers.streams import extract_text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),  # 真实网络 + 思考模型（默认 30s 是密封套件的）
]

_PROBES = collect_probes()

_WEATHER_TOOL = Tool(
    name="get_weather",
    description="Get weather for a city",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)


def _tool_calls(result):
    return [c for c in result.content if c.type == "toolCall"]


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_tool_call_accepted(probe):
    """带工具的请求被接受；若模型调用工具，形态必须合法。"""
    manager = await build_manager()
    ctx = Context(
        messages=[UserMessage(content="What's the weather in Beijing?")],
        tools=[_WEATHER_TOOL],
    )
    async with asyncio.timeout(120):
        result = await manager.complete_simple(probe.model, ctx)
    assert result.error_message is None
    for tool_call in _tool_calls(result):
        assert tool_call.name == "get_weather"
        assert isinstance(tool_call.arguments, dict)


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_tool_result_round_trip(probe):
    """工具调用 + ToolResultMessage 回传 + 模型继续回复。"""
    manager = await build_manager()
    ctx = Context(
        messages=[
            UserMessage(content="Use the get_weather tool to check Beijing's weather.")
        ],
        tools=[_WEATHER_TOOL],
    )
    async with asyncio.timeout(120):
        result1 = await manager.complete_simple(probe.model, ctx)
    assert result1.error_message is None
    tool_calls = _tool_calls(result1)
    if not tool_calls:
        pytest.skip(f"{probe.label} 本轮未调用工具（模型自由裁量）")

    ctx.messages.append(result1)
    ctx.messages.append(
        ToolResultMessage(
            tool_call_id=tool_calls[0].id,
            tool_name=tool_calls[0].name,
            content=[TextContent(text="Sunny, 25°C")],
        )
    )
    async with asyncio.timeout(120):
        result2 = await manager.complete_simple(probe.model, ctx)
    assert result2.error_message is None
    assert result2.stop_reason == StopReason.STOP
    assert len(extract_text(result2)) > 0
