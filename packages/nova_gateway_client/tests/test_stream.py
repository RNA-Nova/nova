"""独立 SDK 的公开 DTO 流、HTTP 错误、断连与资源收尾测试。"""

import asyncio
import json

import httpx
import pytest
import respx
from nova_gateway_protocol import (
    GatewayDone,
    GatewayError,
    GatewayTextDelta,
    ModelRef,
    StreamOptions,
    StreamRequest,
)
from nova_protocol import Context, NovaErrorKind, UserMessage

from nova_gateway_client import (
    GatewayDisconnectedError,
    GatewayHttpError,
    GatewayProtocolError,
    NovaGatewayClient,
)

pytestmark = pytest.mark.asyncio


def _request(**option_values) -> StreamRequest:
    return StreamRequest(
        model=ModelRef(provider="test-provider", id="model-a"),
        context=Context(messages=[UserMessage(content="hi")]),
        options=StreamOptions(**option_values),
    )


def _client(token: str = "synthetic") -> NovaGatewayClient:
    return NovaGatewayClient("https://gateway.test", token)


def _sse(events) -> bytes:
    return "".join(
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events
    ).encode()


async def _collect(client: NovaGatewayClient, request: StreamRequest):
    return [event async for event in client.stream(request)]


@respx.mock
async def test_stream_yields_public_events_and_serializes_request():
    route = respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse(
                [
                    {"type": "start"},
                    {"type": "text_start", "contentIndex": 0},
                    {"type": "text_delta", "contentIndex": 0, "delta": "你好"},
                    {"type": "done", "reason": "stop", "usage": {}},
                ]
            ),
        )
    )
    async with _client() as client:
        events = await _collect(client, _request(metadata={"tenant": "test"}))
    assert isinstance(events[2], GatewayTextDelta)
    assert events[2].delta == "你好"
    assert isinstance(events[-1], GatewayDone)
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer synthetic"
    body = json.loads(request.content)
    assert body["model"] == {"provider": "test-provider", "id": "model-a"}
    assert "baseUrl" not in body["model"]
    assert body["options"] == {"metadata": {"tenant": "test"}}
    assert "synthetic" not in request.content.decode()


@respx.mock
async def test_processes_terminal_event_without_trailing_newline():
    payload = json.dumps({"type": "done", "reason": "stop", "usage": {}})
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, content=f"data: {payload}".encode())
    )
    async with _client() as client:
        events = await _collect(client, _request())
    assert len(events) == 1
    assert isinstance(events[0], GatewayDone)
    assert events[0].reason == "stop"


@respx.mock
async def test_incremental_utf8_decoder_handles_split_multibyte():
    payload = _sse(
        [
            {"type": "text_start", "contentIndex": 0},
            {"type": "text_delta", "contentIndex": 0, "delta": "你好"},
            {"type": "done", "reason": "stop", "usage": {}},
        ]
    )
    split = payload.index("你".encode()) + 1

    class SplitStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload[:split]
            yield payload[split:]

    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, stream=SplitStream())
    )
    async with _client() as client:
        events = await _collect(client, _request())
    assert isinstance(events[1], GatewayTextDelta)
    assert events[1].delta == "你好"


@respx.mock
async def test_clean_eof_without_terminal_raises_disconnected():
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, content=_sse([{"type": "start"}]))
    )
    async with _client() as client:
        with pytest.raises(GatewayDisconnectedError) as caught:
            await _collect(client, _request())
    assert caught.value.nova_error_kind is NovaErrorKind.STREAM_DISCONNECTED
    assert "before the response completed" in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [b"data: {not-json}\n\n", _sse([{"type": "future_event"}])],
    ids=["malformed-json", "unknown-event"],
)
@respx.mock
async def test_rejects_invalid_wire_events(payload):
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, content=payload)
    )
    async with _client() as client:
        with pytest.raises(GatewayProtocolError):
            await _collect(client, _request())


@respx.mock
async def test_http_error_preserves_response_and_redacts_client_token():
    token = "proxy-secret-123"
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": {"message": f"bad credential {token}", "kind": "unauthorized"}
            },
        )
    )
    async with _client(token) as client:
        with pytest.raises(GatewayHttpError) as caught:
            await _collect(client, _request())
    assert caught.value.status_code == 401
    assert caught.value.response.status_code == 401
    assert token not in str(caught.value)


@respx.mock
async def test_stops_after_terminal_event():
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse(
                [
                    {"type": "done", "reason": "stop", "usage": {}},
                    {
                        "type": "error",
                        "reason": "error",
                        "usage": {},
                        "errorMessage": "late",
                    },
                ]
            ),
        )
    )
    async with _client() as client:
        events = await _collect(client, _request())
    assert len(events) == 1
    assert isinstance(events[0], GatewayDone)
    assert not any(isinstance(event, GatewayError) for event in events)


@respx.mock
async def test_close_closes_active_response_and_rejects_new_streams():
    closed = asyncio.Event()

    class PendingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                yield b'data: {"type":"start"}\n\n'
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def aclose(self):
            closed.set()

    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, stream=PendingStream())
    )
    client = _client()
    events = client.stream(_request())
    first = await events.__anext__()
    assert first.type == "start"

    async def next_event():
        return await events.__anext__()

    pending = asyncio.create_task(next_event())
    await client.close()
    with pytest.raises(GatewayDisconnectedError):
        await pending
    assert closed.is_set()
    with pytest.raises(RuntimeError, match="closed"):
        await client.stream(_request()).__anext__()


async def test_client_repr_uses_safe_whitelist():
    client = NovaGatewayClient(
        "https://user:pass@gateway.test/path?token=url-secret#fragment",
        "token-secret",
    )
    rendered = repr(client)
    assert "gateway.test" in rendered
    for secret in ("user", "pass", "url-secret", "token-secret"):
        assert secret not in rendered
    await client.close()


def _http_chunk(event: str) -> bytes:
    payload = f"data: {event}\n\n".encode()
    return f"{len(payload):X}\r\n".encode() + payload + b"\r\n"


async def _serve_delayed_terminal(delay: float):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            await reader.read(4096)
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"Content-Type: text/event-stream\r\n\r\n"
            )
            writer.write(_http_chunk('{"type":"start"}'))
            await writer.drain()
            await asyncio.sleep(delay)
            writer.write(
                _http_chunk('{"type":"done","reason":"stop","usage":{}}') + b"0\r\n\r\n"
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


@pytest.mark.enable_socket
@pytest.mark.timeout(15)
async def test_disabled_timeout_allows_long_idle():
    server = await _serve_delayed_terminal(5.2)
    port = server.sockets[0].getsockname()[1]
    try:
        async with NovaGatewayClient(f"http://127.0.0.1:{port}", "synthetic") as client:
            events = await _collect(client, _request(timeout=0))
        assert isinstance(events[-1], GatewayDone)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.enable_socket
async def test_custom_idle_times_out():
    server = await _serve_delayed_terminal(0.1)
    port = server.sockets[0].getsockname()[1]
    try:
        async with NovaGatewayClient(f"http://127.0.0.1:{port}", "synthetic") as client:
            with pytest.raises(httpx.TimeoutException):
                await _collect(client, _request(timeout=0.01))
    finally:
        server.close()
        await server.wait_closed()


@respx.mock
async def test_incompatible_response_protocol_major_is_rejected():
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            headers={"x-nova-gateway-protocol": "2.0"},
            content=_sse([{"type": "done", "reason": "stop", "usage": {}}]),
        )
    )
    async with _client() as client:
        with pytest.raises(GatewayProtocolError, match="Unsupported"):
            await _collect(client, _request())


@respx.mock
async def test_request_carries_protocol_version_header():
    route = respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse([{"type": "done", "reason": "stop", "usage": {}}]),
        )
    )
    async with _client() as client:
        await _collect(client, _request())
    assert route.calls[0].request.headers["x-nova-gateway-protocol"] == "1.2"
