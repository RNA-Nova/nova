"""OpenAI 兼容端点的 SDK、流式、工具调用与错误测试。"""

import json

import httpx
import openai
import pytest
from nova_gateway.app import create_app
from nova_gateway.config import GatewayConfig
from nova_gateway.runtime import GatewayRuntime
from nova_protocol import (
    DoneEvent,
    StartEvent,
    StopReason,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCall,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

from ._helpers import model, partial, provider

pytestmark = pytest.mark.asyncio


def _app(events=None):
    provider_instance, streams = provider(events)
    runtime = GatewayRuntime(GatewayConfig(api_token="secret"))
    runtime.manager.set_provider(provider_instance)
    return (
        create_app(GatewayConfig(api_token="secret"), runtime=runtime),
        runtime,
        streams,
    )


def _text_events():
    message = partial(model())
    message.content = [TextContent(text="你好")]
    message.usage.input = 3
    message.usage.output = 2
    message.usage.total_tokens = 5
    message.timestamp = 1700000000000
    return [
        StartEvent(partial=message),
        TextStartEvent(content_index=0, partial=message),
        TextDeltaEvent(content_index=0, delta="你好", partial=message),
        TextEndEvent(content_index=0, content="你好", partial=message),
        DoneEvent(reason=StopReason.STOP, message=message),
    ]


def _body(model_id="model-a", **updates):
    body = {
        "model": f"fake/{model_id}",
        "messages": [{"role": "user", "content": "hi"}],
    }
    body.update(updates)
    return body


async def test_openai_models_requires_bearer():
    app, runtime, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get("/v1/models")
        assert missing.status_code == 401
        valid = await client.get(
            "/v1/models", headers={"Authorization": "Bearer secret"}
        )
        assert valid.status_code == 200
        assert valid.json()["data"][0]["id"] == "fake/model-a"
        assert valid.json()["data"][0]["owned_by"] == "fake"
    await runtime.close()


async def test_openai_non_stream_response_with_usage():
    app, runtime, _ = _app(_text_events())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["object"] == "chat.completion"
        assert payload["choices"][0]["message"]["content"] == "你好"
        assert payload["choices"][0]["finish_reason"] == "stop"
        assert payload["created"] == 1700000000
        assert payload["usage"] == {
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
        }
    await runtime.close()


async def test_openai_stream_chunks_and_done():
    app, runtime, _ = _app(_text_events())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(stream=True, stream_options={"include_usage": True}),
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 200
        chunks = []
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                chunks.append(line[6:])
        done = [json.loads(chunk) for chunk in chunks[:-1] if chunk != "[DONE]"]
        assert chunks[-1] == "[DONE]"
        assert done[0]["choices"][0]["delta"]["role"] == "assistant"
        assert done[1]["choices"][0]["delta"]["content"] == "你好"
        assert done[-1]["choices"][0]["finish_reason"] == "stop"
        assert done[-1]["usage"]["total_tokens"] == 5
    await runtime.close()


async def test_openai_tool_call_chunks():
    message = partial(model())
    call = ToolCall(id="call-1", name="read", arguments={"path": "x"})
    message.content.append(call)
    call_event = ToolCallStartEvent(content_index=0, partial=message)
    end = ToolCallEndEvent(content_index=0, tool_call=call, partial=message)
    app, runtime, _ = _app(
        [
            StartEvent(partial=message),
            call_event,
            end,
            DoneEvent(reason=StopReason.TOOL_USE, message=message),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(stream=True),
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 200
        chunks = []
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                chunks.append(json.loads(line[6:]))
        tool_chunks = [
            choice
            for chunk in chunks
            for choice in chunk["choices"]
            if choice["delta"].get("tool_calls")
        ]
        assert tool_chunks[0]["delta"]["tool_calls"][0]["id"] == "call-1"
        assert tool_chunks[0]["delta"]["tool_calls"][0]["function"]["name"] == "read"
        assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    await runtime.close()


async def test_openai_unknown_model_and_unsupported_format():
    app, runtime, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.post(
            "/v1/chat/completions",
            json=_body("missing"),
            headers={"Authorization": "Bearer secret"},
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["type"] == "invalid_request_error"
        unsupported = await client.post(
            "/v1/chat/completions",
            json=_body(response_format={"type": "json_object"}),
            headers={"Authorization": "Bearer secret"},
        )
        assert unsupported.status_code == 400
    await runtime.close()


async def test_openai_sdk_non_stream_client():
    app, runtime, _ = _app(_text_events())
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    client = openai.AsyncOpenAI(
        base_url="http://test/v1", api_key="secret", http_client=http_client
    )
    result = await client.chat.completions.create(
        model="fake/model-a",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.choices[0].message.content == "你好"
    assert result.usage is not None
    assert result.usage.total_tokens == 5
    await http_client.aclose()
    await runtime.close()


async def test_openai_error_payload_redacts_secrets():
    from nova_gateway.openai import openai_error_payload

    payload = openai_error_payload(
        RuntimeError("upstream failed: Bearer upstream-secret-token")
    )
    assert payload["error"]["message"] == "upstream failed: [REDACTED_SECRET]"
    assert "upstream-secret-token" not in json.dumps(payload)


async def test_openai_stream_usage_is_opt_in():
    app, runtime, _ = _app(_text_events())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(stream=True),
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 200
        chunks = [
            json.loads(line[6:])
            async for line in response.aiter_lines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        assert chunks
        assert all("usage" not in chunk for chunk in chunks)
    await runtime.close()
