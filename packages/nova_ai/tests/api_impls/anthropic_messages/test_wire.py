"""anthropic_messages 过线端到端（httpx2 假 LLM + 请求捕获）。

断言两面：产出的 AssistantMessage 与 capture 的请求体
（model/stream/max_tokens/system/tools）——设施在 tests/_helpers/fake_llm.py。
"""

import pytest
from nova_ai.api_impls.anthropic_messages import AnthropicOptions, stream
from nova_protocol import (
    Context,
    KnownApi,
    Model,
    ModelCost,
    NovaErrorKind,
    StopReason,
    Tool,
    UserMessage,
)
from tests._helpers.fake_llm import (
    RequestCapture,
    ant_error,
    ant_message_start,
    ant_text_turn,
    ant_tool_use_turn,
    anthropic_client,
)

pytestmark = pytest.mark.asyncio


def _model() -> Model:
    return Model(
        id="claude-x",
        name="x",
        api=KnownApi.ANTHROPIC_MESSAGES,
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=3, output=15, cache_read=0.3, cache_write=3.75),
        context_window=200000,
        max_tokens=64000,
    )


async def _run(context: Context, client):
    event_stream = stream(
        _model(), context, AnthropicOptions(api_key="sk-test", client=client)
    )
    async for _ in event_stream:
        pass
    return await event_stream.result()


async def test_text_turn_and_request_body():
    """文本 turn + 请求体（model/stream/max_tokens/system/messages）。"""
    capture = RequestCapture()
    client = anthropic_client(ant_text_turn("你好，世界"), capture)
    result = await _run(
        Context(messages=[UserMessage(content="hi")], system_prompt="你是助手"),
        client,
    )
    assert result.stop_reason == StopReason.STOP
    text = "".join(b.text for b in result.content if b.type == "text")
    assert text == "你好，世界"

    body = RequestCapture.body_json(capture.single())
    assert body["model"] == "claude-x"
    assert body["stream"] is True
    assert isinstance(body["max_tokens"], int)
    assert "你是助手" in str(body.get("system", ""))
    assert body["messages"][0]["role"] == "user"


async def test_tool_use_turn_and_tools_in_body():
    """tool_use turn（input_json 分片）+ 请求体带工具定义。"""
    capture = RequestCapture()
    client = anthropic_client(
        ant_tool_use_turn(id="toolu_1", name="search", arguments='{"q": "nova"}'),
        capture,
    )
    tools = [
        Tool(
            name="search",
            description="搜索",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        )
    ]
    result = await _run(
        Context(messages=[UserMessage(content="查一下")], tools=tools), client
    )
    assert result.stop_reason == StopReason.TOOL_USE
    call = next(b for b in result.content if b.type == "toolCall")
    assert call.id == "toolu_1"
    assert call.name == "search"
    assert call.arguments == {"q": "nova"}

    body = RequestCapture.body_json(capture.single())
    assert body["tools"][0]["name"] == "search"
    assert body["tools"][0]["input_schema"]["required"] == ["q"]


async def test_overloaded_frame_maps_kind():
    """error 帧（overloaded_error）→ OVERLOADED，且恰好一次请求。"""
    capture = RequestCapture()
    client = anthropic_client(
        [ant_message_start(), ant_error("overloaded_error", "Overloaded")],
        capture,
    )
    result = await _run(Context(messages=[UserMessage(content="hi")]), client)
    assert result.stop_reason == StopReason.ERROR
    assert result.error_kind is NovaErrorKind.OVERLOADED
    assert len(capture) == 1


async def test_unauthorized_status_maps_kind():
    """401 → AuthenticationError → UNAUTHORIZED。"""
    capture = RequestCapture()
    client = anthropic_client(
        [],
        capture,
        status=401,
        error_body={
            "type": "error",
            "error": {"type": "authentication_error", "message": "invalid key"},
        },
    )
    result = await _run(Context(messages=[UserMessage(content="hi")]), client)
    assert result.error_kind is NovaErrorKind.UNAUTHORIZED
