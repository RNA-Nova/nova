"""Nova Gateway AI 适配层的事件重建、错误与取消测试。"""

import asyncio
import json

import httpx
import pytest
import respx
from nova_async import AbortController
from nova_protocol import (
    Context,
    Model,
    ModelCost,
    NovaErrorKind,
    StopReason,
    ThinkingLevel,
    ToolCall,
    UserMessage,
)

from nova_ai import SimpleStreamOptions
from nova_ai.api_impls import nova_gateway

pytestmark = pytest.mark.asyncio


def _model() -> Model:
    return Model(
        id="model-a",
        name="Model A",
        api="openai-completions",
        provider="test-provider",
        base_url="https://gateway.test",
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=1, output=2, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=8192,
    )


class _AiStreamClient:
    """测试便捷包装；生产公开面是 nova_gateway 协议模块本身。"""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self.token = token

    def stream(self, model: Model, context: Context, options=None):
        model = model.model_copy(update={"base_url": self.base_url})
        options = options or SimpleStreamOptions()
        if options.api_key is None:
            options.api_key = self.token
        return nova_gateway.stream(model, context, options)


def _client(token: str = "synthetic") -> _AiStreamClient:
    return _AiStreamClient("https://gateway.test", token)


def _context() -> Context:
    return Context(messages=[UserMessage(content="hi")])


def _sse(events) -> bytes:
    return "".join(
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events
    ).encode()


def _text_of(result) -> str:
    return "".join(
        getattr(block, "text", "") for block in result.content if hasattr(block, "text")
    )


@respx.mock
async def test_rebuilds_tool_arguments_and_terminal_metadata():
    events = [
        {"type": "start"},
        {
            "type": "toolcall_start",
            "contentIndex": 0,
            "id": "call-1",
            "toolName": "read",
        },
        {"type": "toolcall_delta", "contentIndex": 0, "delta": '{"path":'},
        {"type": "toolcall_delta", "contentIndex": 0, "delta": '"x"}'},
        {
            "type": "toolcall_end",
            "contentIndex": 0,
            "toolCall": {
                "type": "toolCall",
                "id": "call-1",
                "name": "read",
                "thoughtSignature": "signature",
                "namespace": "dynamic_tools",
            },
        },
        {"type": "done", "reason": "stop", "usage": {}},
    ]
    route = respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, content=_sse(events))
    )
    result = await _client().stream(_model(), _context()).result()
    assert result.stop_reason is StopReason.STOP
    call = result.content[0]
    assert isinstance(call, ToolCall)
    assert call.arguments == {"path": "x"}
    assert call.thought_signature == "signature"
    assert call.namespace == "dynamic_tools"
    assert call.partial_args is None
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer synthetic"
    assert "synthetic" not in request.content.decode()


@respx.mock
async def test_processes_terminal_event_without_trailing_newline():
    payload = json.dumps({"type": "done", "reason": "stop", "usage": {}})
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, content=f"data: {payload}".encode())
    )
    result = await _client().stream(_model(), _context()).result()
    assert result.stop_reason is StopReason.STOP


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
    result = await _client().stream(_model(), _context()).result()
    assert _text_of(result) == "你好"


@respx.mock
async def test_clean_eof_without_terminal_is_error_not_hang():
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, content=_sse([{"type": "start"}]))
    )
    result = await _client().stream(_model(), _context()).result()
    assert result.stop_reason is StopReason.ERROR
    assert "before the response completed" in (result.error_message or "")


@respx.mock(assert_all_called=False)
async def test_pre_abort_returns_aborted_without_request():
    route = respx.post("https://gateway.test/api/stream")
    controller = AbortController()
    controller.abort()
    from nova_ai import SimpleStreamOptions

    options = SimpleStreamOptions(signal=controller.signal)
    result = await _client().stream(_model(), _context(), options).result()
    assert result.stop_reason is StopReason.ABORTED
    assert route.call_count == 0


@respx.mock
async def test_midstream_abort_returns_aborted_and_closes_driver():
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
    controller = AbortController()
    from nova_ai import SimpleStreamOptions

    options = SimpleStreamOptions(signal=controller.signal)
    stream = _client().stream(_model(), _context(), options)
    controller.abort()
    result = await stream.result()
    assert result.stop_reason is StopReason.ABORTED
    assert result.error_kind is None
    if stream.task is not None:
        await stream.task
    assert closed.is_set()


@respx.mock
async def test_terminal_stops_waiting_for_eof():
    closed = asyncio.Event()

    class PendingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                yield b'data: {"type":"done","reason":"stop","usage":{}}\n\n'
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def aclose(self):
            closed.set()

    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, stream=PendingStream())
    )
    stream = _client().stream(_model(), _context())
    result = await stream.result()
    assert result.stop_reason is StopReason.STOP
    if stream.task is not None:
        await stream.task
    assert closed.is_set()


@respx.mock
async def test_ignores_events_after_terminal():
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
    result = await _client().stream(_model(), _context()).result()
    assert result.stop_reason is StopReason.STOP
    assert result.error_message is None


@respx.mock
async def test_error_redacts_token_and_preserves_kind():
    token = "proxy-secret-123"
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(401, json={"error": f"bad credential {token}"})
    )
    result = await _client(token).stream(_model(), _context()).result()
    assert result.stop_reason is StopReason.ERROR
    assert result.error_kind is NovaErrorKind.UNAUTHORIZED
    assert token not in (result.error_message or "")


@respx.mock
async def test_upstream_error_is_redacted_and_normalized():
    token = "upstream-secret-456"
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse(
                [
                    {
                        "type": "error",
                        "reason": "error",
                        "usage": {},
                        "errorMessage": f"failed with {token}",
                        "errorKind": "streamDisconnected",
                    }
                ]
            ),
        )
    )
    result = await _client(token).stream(_model(), _context()).result()
    assert result.error_kind is NovaErrorKind.STREAM_DISCONNECTED
    assert token not in (result.error_message or "")


@respx.mock
async def test_toolcall_end_with_wrong_block_is_loud_error():
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse(
                [
                    {"type": "text_start", "contentIndex": 0},
                    {
                        "type": "toolcall_end",
                        "contentIndex": 0,
                        "toolCall": {"type": "toolCall", "id": "c", "name": "t"},
                    },
                ]
            ),
        )
    )
    result = await _client().stream(_model(), _context()).result()
    assert result.stop_reason is StopReason.ERROR
    assert "non-toolCall" in (result.error_message or "")


@respx.mock
async def test_serializes_model_ref_context_and_supported_options():
    from nova_ai import SimpleStreamOptions

    payload = _sse([{"type": "done", "reason": "stop", "usage": {}}])

    class FullStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload

    route = respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, stream=FullStream())
    )
    options = SimpleStreamOptions(
        temperature=0.2,
        max_tokens=128,
        reasoning=ThinkingLevel.HIGH,
        session_id="session-1",
        headers={"X-Trace": "trace-1"},
        metadata={"tenant": "test"},
        max_retries=3,
        max_retry_delay_ms=500,
    )
    result = await _client().stream(_model(), _context(), options).result()
    assert result.stop_reason is StopReason.STOP, result.error_message
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == {"provider": "test-provider", "id": "model-a"}
    assert "baseUrl" not in body["model"]
    assert body["context"] == _context().dump_wire(exclude_none=True)
    assert body["options"] == {
        "temperature": 0.2,
        "maxTokens": 128,
        "reasoning": "high",
        "sessionId": "session-1",
        "metadata": {"tenant": "test"},
        "maxRetries": 3,
        "maxRetryDelayMs": 500,
    }


@respx.mock
async def test_rebuilds_thinking_and_signature():
    from nova_protocol import ThinkingContent

    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse(
                [
                    {"type": "start"},
                    {"type": "thinking_start", "contentIndex": 0},
                    {"type": "thinking_delta", "contentIndex": 0, "delta": "思"},
                    {"type": "thinking_delta", "contentIndex": 0, "delta": "考"},
                    {
                        "type": "thinking_end",
                        "contentIndex": 0,
                        "contentSignature": "signature",
                    },
                    {
                        "type": "done",
                        "reason": "stop",
                        "usage": {},
                        "providerThinkingLevel": "high",
                    },
                ]
            ),
        )
    )
    result = await _client().stream(_model(), _context()).result()
    assert result.content == [
        ThinkingContent(thinking="思考", thinking_signature="signature")
    ]
    assert result.provider_thinking_level == "high"


@pytest.mark.parametrize(
    "payload",
    [b"data: {not-json}\n\n", _sse([{"type": "future_event"}])],
    ids=["malformed-json", "unknown-event"],
)
@respx.mock
async def test_rejects_invalid_wire_events_as_error(payload):
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, content=payload)
    )
    result = await _client().stream(_model(), _context()).result()
    assert result.stop_reason is StopReason.ERROR
    assert result.error_kind is NovaErrorKind.INTERNAL


@respx.mock
async def test_http_rate_limit_preserves_retry_after():
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            429, headers={"Retry-After": "2"}, json={"error": "slow down"}
        )
    )
    result = await _client().stream(_model(), _context()).result()
    assert result.stop_reason is StopReason.ERROR
    assert result.error_kind is NovaErrorKind.RATE_LIMITED
    assert result.retry_after_ms == 2000


@respx.mock
async def test_terminal_error_preserves_metadata():
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse(
                [
                    {
                        "type": "error",
                        "reason": "error",
                        "usage": {},
                        "errorMessage": "slow down",
                        "errorKind": "rateLimited",
                        "retryAfterMs": 2500,
                        "providerThinkingLevel": "high",
                    }
                ]
            ),
        )
    )
    result = await _client().stream(_model(), _context()).result()
    assert result.error_kind is NovaErrorKind.RATE_LIMITED
    assert result.retry_after_ms == 2500
    assert result.provider_thinking_level == "high"


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
        options = SimpleStreamOptions(timeout=0)
        result = (
            await _AiStreamClient(f"http://127.0.0.1:{port}", "synthetic")
            .stream(_model(), _context(), options)
            .result()
        )
        assert result.stop_reason is StopReason.STOP
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.enable_socket
async def test_custom_idle_times_out():
    server = await _serve_delayed_terminal(0.1)
    port = server.sockets[0].getsockname()[1]
    try:
        options = SimpleStreamOptions(timeout=0.01)
        result = (
            await _AiStreamClient(f"http://127.0.0.1:{port}", "synthetic")
            .stream(_model(), _context(), options)
            .result()
        )
        assert result.stop_reason is StopReason.ERROR
        assert result.error_kind is NovaErrorKind.REQUEST_TIMEOUT
    finally:
        server.close()
        await server.wait_closed()


@respx.mock
async def test_module_is_provider_streams_implementation():
    from nova_ai import StreamOptions
    from nova_ai.gateway import create_provider

    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse([{"type": "done", "reason": "stop", "usage": {}}]),
        )
    )
    provider = create_provider(
        id="test-provider",
        name="Test Provider",
        base_url="https://gateway.test",
        models=[_model()],
        api=nova_gateway,
    )
    result = await provider.stream(
        _model(), _context(), StreamOptions(api_key="synthetic")
    ).result()
    assert result.stop_reason is StopReason.STOP


@respx.mock
async def test_observation_hooks_and_payload_rewrite():
    from nova_ai import SimpleStreamOptions

    route = respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse([{"type": "done", "reason": "stop", "usage": {}}]),
        )
    )
    observed = {"events": []}

    def on_payload(payload, identity):
        assert identity.provider == "test-provider"
        assert identity.id == "model-a"
        payload["options"]["maxTokens"] = 64
        return payload

    async def on_response(response, identity):
        observed["status"] = response.status

    def on_event(event, identity):
        observed["events"].append(event.type)

    options = SimpleStreamOptions(
        on_payload=on_payload,
        on_response=on_response,
        on_provider_stream_event=on_event,
    )
    result = await _client().stream(_model(), _context(), options).result()
    assert result.stop_reason is StopReason.STOP, result.error_message
    assert observed == {"events": ["done"], "status": 200}
    body = json.loads(route.calls[0].request.content)
    assert body["options"]["maxTokens"] == 64
