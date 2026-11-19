"""本地重试、退避、幂等键与 request ID 行为测试。"""

import json

import httpx
import pytest
import respx
from nova_gateway_protocol import ModelRef, StreamRequest
from nova_protocol import Context, NovaErrorKind, UserMessage

from nova_gateway_client import (
    GatewayAuthenticationError,
    GatewayInternalServerError,
    GatewayRateLimitError,
    GatewayTimeoutError,
    NovaGatewayClient,
)

pytestmark = pytest.mark.asyncio


def _request() -> StreamRequest:
    return StreamRequest(
        model=ModelRef(provider="test-provider", id="model-a"),
        context=Context(messages=[UserMessage(content="hi")]),
    )


def _models_payload():
    return {
        "models": [
            {
                "provider": "test-provider",
                "id": "model-a",
                "name": "Model A",
                "type": "chat",
                "reasoning": True,
                "inputTypes": ["text"],
            }
        ]
    }


def _client(**overrides):
    async def no_sleep(_seconds):
        return None

    options = {
        "max_retries": 2,
        "sleep_fn": no_sleep,
        "jitter_fn": lambda: 0.0,
        **overrides,
    }
    return NovaGatewayClient("https://gateway.test", "token", **options)


@respx.mock
async def test_list_models_retries_transient_response_and_tracks_attempts():
    route = respx.get("https://gateway.test/api/models").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=_models_payload()),
        ]
    )
    async with _client() as client:
        result = await client.list_models_with_response()
    assert result.status_code == 200
    assert result.retries_taken == 1
    assert result.data.models[0].id == "model-a"
    assert len(route.calls) == 2
    first, second = (call.request for call in route.calls)
    assert first.headers["x-request-id"] == second.headers["x-request-id"]
    assert "idempotency-key" not in first.headers


@respx.mock
async def test_stream_retries_and_reuses_idempotency_key():
    route = respx.post("https://gateway.test/api/stream").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(
                200,
                content=b'data: {"type":"done","reason":"stop","usage":{}}\n\n',
            ),
        ]
    )
    sleeps = []

    async def record_sleep(seconds):
        sleeps.append(seconds)

    async with _client(sleep_fn=record_sleep) as client:
        stream = client.stream(_request())
        events = [event async for event in stream]
    assert events[-1].type == "done"
    assert stream.retries_taken == 1
    assert sleeps == [2.0]
    first, second = (call.request for call in route.calls)
    assert first.headers["idempotency-key"] == second.headers["idempotency-key"]
    assert first.headers["x-request-id"] == second.headers["x-request-id"]


@respx.mock
async def test_retry_exhaustion_raises_typed_rate_limit():
    respx.post("https://gateway.test/api/stream").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(429, headers={"Retry-After": "2"}),
        ]
    )
    async with _client(max_retries=1) as client:
        with pytest.raises(GatewayRateLimitError) as caught:
            [event async for event in client.stream(_request())]
    assert caught.value.kind is NovaErrorKind.RATE_LIMITED
    assert caught.value.retry_after_ms == 2000


@respx.mock
async def test_non_retryable_error_does_not_retry():
    route = respx.get("https://gateway.test/api/models").mock(
        return_value=httpx.Response(401)
    )
    async with _client() as client:
        with pytest.raises(GatewayAuthenticationError):
            await client.list_models()
    assert len(route.calls) == 1


@respx.mock
async def test_should_retry_false_skips_retry():
    route = respx.get("https://gateway.test/api/models").mock(
        return_value=httpx.Response(500, headers={"x-should-retry": "false"})
    )
    async with _client() as client:
        with pytest.raises(GatewayInternalServerError):
            await client.list_models()
    assert len(route.calls) == 1


@respx.mock
async def test_timeout_after_retries_has_typed_error():
    respx.get("https://gateway.test/api/models").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    async with _client(max_retries=1) as client:
        with pytest.raises(GatewayTimeoutError) as caught:
            await client.list_models()
    assert caught.value.request.method == "GET"
