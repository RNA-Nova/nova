"""openai_completions 过线端到端（respx 假 LLM——params→HTTP→SSE 全真跑）。

断言两面：产出的 AssistantMessage（"回来的"）与 capture 的请求体
（"发出去的"）——codex wiremock 层对位（设施在 tests/_helpers/fake_llm.py）。
"""

import httpx
import pytest
import respx
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

from nova_ai.api_impls.openai_completions import OpenAICompletionsOptions, stream
from tests._helpers.fake_llm import (
    RequestCapture,
    mount_openai_chat,
    mount_openai_transport_error,
    oai_chunk,
    oai_tool_call,
)

pytestmark = pytest.mark.asyncio


def _model() -> Model:
    return Model(
        id="test-model",
        name="Test",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider="test-provider",
        base_url="https://api.openai.com/v1",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=1, output=2, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )


async def _run(context: Context):
    event_stream = stream(
        _model(), context, OpenAICompletionsOptions(api_key="sk-test")
    )
    async for _ in event_stream:
        pass
    return await event_stream.result()


@respx.mock
async def test_text_turn_and_request_body():
    """文本 turn：SSE 解析正确 + 请求体完整（model/stream/messages/system）。"""
    capture = RequestCapture()
    mount_openai_chat(
        [oai_chunk(content="你好"), oai_chunk(content="，世界", finish="stop")],
        capture,
    )
    result = await _run(
        Context(messages=[UserMessage(content="hi")], system_prompt="你是助手")
    )
    assert result.stop_reason == StopReason.STOP
    text = "".join(b.text for b in result.content if b.type == "text")
    assert text == "你好，世界"

    body = RequestCapture.body_json(capture.single())
    assert body["model"] == "test-model"
    assert body["stream"] is True
    assert body["messages"][0] == {"role": "system", "content": "你是助手"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}
    assert "tools" not in body


@respx.mock
async def test_tool_call_turn_and_tools_in_body():
    """tool call 流（分片到达）+ 请求体带工具定义。"""
    capture = RequestCapture()
    mount_openai_chat(
        [
            oai_chunk(tool_calls=[oai_tool_call(0, id="tc-1", name="search")]),
            oai_chunk(tool_calls=[oai_tool_call(0, arguments='{"q":')]),
            oai_chunk(
                tool_calls=[oai_tool_call(0, arguments='"nova"}')],
                finish="tool_calls",
            ),
        ],
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
    result = await _run(Context(messages=[UserMessage(content="查一下")], tools=tools))
    assert result.stop_reason == StopReason.TOOL_USE
    call = next(b for b in result.content if b.type == "toolCall")
    assert call.id == "tc-1"
    assert call.name == "search"
    assert call.arguments == {"q": "nova"}

    body = RequestCapture.body_json(capture.single())
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "search"
    assert body["tools"][0]["function"]["parameters"]["required"] == ["q"]


@respx.mock
async def test_conflict_maps_overloaded_kind():
    """409 → ConflictError → OVERLOADED（经真线归一上 kind）。"""
    capture = RequestCapture()
    mount_openai_chat(
        [],
        capture,
        status=409,
        error_body={
            "error": {"message": "conflict, slow down", "type": "server_error"}
        },
    )
    result = await _run(Context(messages=[UserMessage(content="hi")]))
    assert result.stop_reason == StopReason.ERROR
    assert result.error_kind is NovaErrorKind.OVERLOADED
    assert len(capture) == 1


@respx.mock
async def test_rate_limit_maps_kind():
    """429 → RateLimitError → RATE_LIMITED。"""
    capture = RequestCapture()
    mount_openai_chat([], capture, status=429)
    result = await _run(Context(messages=[UserMessage(content="hi")]))
    assert result.error_kind is NovaErrorKind.RATE_LIMITED


@respx.mock
async def test_transport_error_maps_connection_failed():
    """传输错误（SDK 包成 APIConnectionError）→ HTTP_CONNECTION_FAILED。"""
    capture = RequestCapture()
    mount_openai_transport_error(httpx.ConnectError("refused"), capture)
    result = await _run(Context(messages=[UserMessage(content="hi")]))
    assert result.stop_reason == StopReason.ERROR
    assert result.error_kind is NovaErrorKind.HTTP_CONNECTION_FAILED
    assert len(capture) == 1
