"""Gateway 请求/上游/终态日志的 requestId、隐私与零 delta 契约。"""

from __future__ import annotations

import io
import json
import logging

import httpx
import pytest
from nova_ai.utils.sanitize import redact_secrets
from nova_gateway.app import create_app
from nova_gateway.config import GatewayConfig
from nova_gateway.runtime import GatewayRuntime
from nova_logging import LoggingSettings, SinkSettings, configure_logging
from nova_protocol import (
    DoneEvent,
    ErrorEvent,
    NovaErrorKind,
    StartEvent,
    StopReason,
    TextDeltaEvent,
)

from ._helpers import model, partial, provider

pytestmark = pytest.mark.asyncio


def _body() -> dict:
    return {
        "model": {"provider": "fake", "id": "model-a"},
        "context": {
            "messages": [
                {
                    "role": "user",
                    "content": "你好，Bearer downstream-secret-token",
                    "timestamp": 0,
                }
            ]
        },
        "options": {},
    }


def _records(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def _logging_runtime(stream: io.StringIO):
    return configure_logging(
        LoggingSettings(sinks=(SinkSettings(stream=stream),), redactor=redact_secrets)
    )


async def test_native_stream_logs_lifecycle_without_payload_or_delta():
    message = partial(model())
    app_runtime = GatewayRuntime(GatewayConfig(api_token="secret"))
    provider_instance, _ = provider(
        [
            StartEvent(partial=message),
            TextDeltaEvent(content_index=0, delta="秘密增量", partial=message),
            DoneEvent(reason=StopReason.STOP, message=message),
        ]
    )
    app_runtime.manager.set_provider(provider_instance)
    app = create_app(GatewayConfig(api_token="secret"), runtime=app_runtime)
    stream = io.StringIO()
    logging_runtime = _logging_runtime(stream)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/stream",
                json=_body(),
                headers={
                    "Authorization": "Bearer secret",
                    "x-request-id": "req-log-1",
                },
            )
        assert response.status_code == 200
    finally:
        await app_runtime.close()
        logging_runtime.close()

    records = [
        record for record in _records(stream) if record["event"].startswith("gateway.")
    ]
    events = {record["event"] for record in records}
    assert events == {
        "gateway.request.started",
        "gateway.upstream.connected",
        "gateway.stream.connected",
        "gateway.stream.completed",
        "gateway.stream.closed",
    }
    assert {record["requestId"] for record in records} == {"req-log-1"}
    connected = next(
        record for record in records if record["event"] == "gateway.upstream.connected"
    )
    assert connected["provider"] == "fake"
    assert connected["model"] == "model-a"
    completed = next(
        record for record in records if record["event"] == "gateway.stream.completed"
    )
    assert completed["status"] == "stop"
    assert completed["durationMs"] >= 0
    output = stream.getvalue()
    assert "downstream-secret-token" not in output
    assert "秘密增量" not in output
    assert "Bearer secret" not in output


async def test_upstream_error_logs_kind_and_redacts_provider_message():
    message = partial(model(), stop_reason=StopReason.ERROR)
    message.error_message = "slow down: Bearer upstream-secret-token"
    message.error_kind = NovaErrorKind.RATE_LIMITED
    app_runtime = GatewayRuntime(GatewayConfig(api_token="secret"))
    provider_instance, _ = provider(
        [ErrorEvent(reason=StopReason.ERROR, error=message)]
    )
    app_runtime.manager.set_provider(provider_instance)
    app = create_app(GatewayConfig(api_token="secret"), runtime=app_runtime)
    stream = io.StringIO()
    logging_runtime = _logging_runtime(stream)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/stream",
                json=_body(),
                headers={"Authorization": "Bearer secret", "x-request-id": "req-log-2"},
            )
        assert response.status_code == 200
    finally:
        await app_runtime.close()
        logging_runtime.close()

    failed = next(
        record
        for record in _records(stream)
        if record["event"] == "gateway.stream.failed"
    )
    assert failed["requestId"] == "req-log-2"
    assert failed["status"] == "error"
    assert failed["errorKind"] == "rateLimited"
    assert "upstream-secret-token" not in stream.getvalue()


async def test_openai_non_stream_emits_terminal_log():
    message = partial(model())
    app_runtime = GatewayRuntime(GatewayConfig(api_token="secret"))
    provider_instance, _ = provider(
        [DoneEvent(reason=StopReason.STOP, message=message)]
    )
    app_runtime.manager.set_provider(provider_instance)
    app = create_app(GatewayConfig(api_token="secret"), runtime=app_runtime)
    stream = io.StringIO()
    logging_runtime = _logging_runtime(stream)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "fake/model-a",
                    "messages": [{"role": "user", "content": "你好"}],
                },
                headers={"Authorization": "Bearer secret", "x-request-id": "req-log-3"},
            )
        assert response.status_code == 200
    finally:
        await app_runtime.close()
        logging_runtime.close()

    records = _records(stream)
    completed = next(
        record for record in records if record["event"] == "gateway.stream.completed"
    )
    assert completed["requestId"] == "req-log-3"
    assert completed["status"] == "stop"
    assert any(record["event"] == "gateway.request.completed" for record in records)
