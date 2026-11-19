"""原生端点的鉴权、事件、错误与断连契约测试。"""

import asyncio
import json

import httpx
import pytest
from nova_gateway.app import create_app
from nova_gateway.config import GatewayConfig
from nova_gateway.runtime import GatewayRuntime
from nova_protocol import (
    DoneEvent,
    ErrorEvent,
    NovaErrorKind,
    StartEvent,
    StopReason,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
)

from ._helpers import model, partial, provider, serve

pytestmark = pytest.mark.asyncio


def _events(model):
    partial_message = partial(model)
    return [
        StartEvent(partial=partial_message),
        TextStartEvent(content_index=0, partial=partial_message),
        TextDeltaEvent(content_index=0, delta="你好", partial=partial_message),
        TextEndEvent(content_index=0, content="你好", partial=partial_message),
        DoneEvent(reason=StopReason.STOP, message=partial_message),
    ]


def _app(events=None, *, pending: bool = False, token: str = "secret"):
    provider_instance, streams = provider(events, pending=pending)
    runtime = GatewayRuntime(GatewayConfig(api_token=token))
    runtime.manager.set_provider(provider_instance)
    app = create_app(GatewayConfig(api_token=token), runtime=runtime)
    return app, runtime, streams


async def _request_events(client: httpx.AsyncClient, body: dict) -> list[dict]:
    response = await client.post(
        "/api/stream", json=body, headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 200
    events = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _body(model_id: str = "model-a") -> dict:
    return {
        "model": {"provider": "fake", "id": model_id},
        "context": {"messages": [{"role": "user", "content": "hi", "timestamp": 0}]},
        "options": {},
    }


async def test_health_is_public_but_models_require_bearer():
    app, runtime, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        health = await client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": True}
        missing = await client.get("/api/models")
        assert missing.status_code == 401
        assert missing.json()["error"]["kind"] == "unauthorized"
        valid = await client.get(
            "/api/models", headers={"Authorization": "Bearer secret"}
        )
        assert valid.status_code == 200
        assert valid.json()["models"][0]["id"] == "model-a"
    await runtime.close()


async def test_unknown_model_is_typed_404():
    app, runtime, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/stream",
            json=_body("missing"),
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["kind"] == "modelNotFound"
    await runtime.close()


async def test_native_stream_rebuilds_public_events():
    app, runtime, _ = _app(_events(model()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        events = await _request_events(client, _body())
        assert [event["type"] for event in events] == [
            "start",
            "text_start",
            "text_delta",
            "text_end",
            "done",
        ]
        assert events[1]["contentIndex"] == 0
        assert events[2]["delta"] == "你好"
        assert events[-1]["reason"] == "stop"
        assert "partial" not in events[0]
    await runtime.close()


async def test_upstream_error_keeps_metadata():
    message = partial(model(), stop_reason=StopReason.ERROR)
    message.error_message = "slow down: Bearer upstream-secret-token"
    message.error_kind = NovaErrorKind.RATE_LIMITED
    message.retry_after_ms = 2500
    app, runtime, _ = _app([ErrorEvent(reason=StopReason.ERROR, error=message)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        events = await _request_events(client, _body())
        assert events[0]["type"] == "error"
        assert events[0]["errorKind"] == "rateLimited"
        assert events[0]["retryAfterMs"] == 2500
        assert events[0]["errorMessage"] == "slow down: [REDACTED_SECRET]"
        assert "upstream-secret-token" not in json.dumps(events)
    await runtime.close()


@pytest.mark.enable_socket
async def test_client_disconnect_cancels_upstream():
    app, runtime, streams = _app(pending=True)
    async with serve(app) as base_url:
        async with httpx.AsyncClient(base_url=base_url) as client:
            async with client.stream(
                "POST",
                "/api/stream",
                json=_body(),
                headers={"Authorization": "Bearer secret"},
            ) as response:
                assert response.status_code == 200
                await streams.started.wait()
                await response.aclose()
                await asyncio.wait_for(streams.closed.wait(), timeout=1)
    await runtime.close()


@pytest.mark.enable_socket
async def test_runtime_close_cancels_pending_stream():
    app, runtime, streams = _app(pending=True)
    async with serve(app) as base_url:
        async with httpx.AsyncClient(base_url=base_url) as client:
            async with client.stream(
                "POST",
                "/api/stream",
                json=_body(),
                headers={"Authorization": "Bearer secret"},
            ) as response:
                assert response.status_code == 200
                await streams.started.wait()
                await runtime.close()
                await response.aclose()
                assert streams.closed.is_set()


async def test_protocol_major_mismatch_is_typed_400():
    app, runtime, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        valid = await client.get(
            "/api/models",
            headers={
                "Authorization": "Bearer secret",
                "x-nova-gateway-protocol": "1.1",
            },
        )
        assert valid.status_code == 200
        assert valid.headers["x-nova-gateway-protocol"] == "1.2"
        mismatch = await client.get(
            "/api/models",
            headers={
                "Authorization": "Bearer secret",
                "x-nova-gateway-protocol": "2.0",
            },
        )
        assert mismatch.status_code == 400
        assert mismatch.json()["error"]["kind"] == "badRequest"
        assert mismatch.headers["x-nova-gateway-protocol"] == "1.2"
    await runtime.close()


async def test_native_validation_error_uses_native_error_shape():
    app, runtime, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/stream",
            json={"model": {"provider": "fake", "id": "model-a"}},
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["kind"] == "badRequest"
        assert "message" in response.json()["error"]
        assert "type" not in response.json()["error"]
    await runtime.close()


async def test_server_echoes_or_generates_request_id():
    app, runtime, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/models",
            headers={"Authorization": "Bearer secret", "x-request-id": "req-1"},
        )
        assert response.headers["x-request-id"] == "req-1"
        generated = await client.get(
            "/api/models", headers={"Authorization": "Bearer secret"}
        )
        assert generated.headers["x-request-id"]
    await runtime.close()
