"""stream_proxy 的线级事件重建与终态测试（无真实网络）。"""

import asyncio
import json

import httpx
import pytest
import respx
from _helpers.messages import text_of
from nova_async import AbortController
from nova_protocol import (
    Context,
    NovaErrorKind,
    RedactedString,
    StopReason,
    ToolCall,
    UserMessage,
)

from nova_agent import ProxyStreamOptions, stream_proxy

pytestmark = pytest.mark.asyncio


def _options() -> ProxyStreamOptions:
    return ProxyStreamOptions(
        proxy_url="https://proxy.test", auth_token=RedactedString("synthetic")
    )


def _sse(events) -> bytes:
    return "".join(
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events
    ).encode()


def _context() -> Context:
    return Context(messages=[UserMessage(content="hi")])


@respx.mock
async def test_proxy_rebuilds_tool_arguments_and_terminal_metadata(dummy_model):
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
            # 终态只给元数据，不应把增量重建的 arguments 清掉。
            "toolCall": {
                "type": "toolCall",
                "id": "call-1",
                "name": "read",
                "thoughtSignature": "signature",
            },
        },
        {"type": "done", "reason": "stop", "usage": {}},
    ]
    route = respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(200, content=_sse(events))
    )
    stream = stream_proxy(dummy_model, _context(), _options())
    result = await stream.result()
    assert result.stop_reason is StopReason.STOP
    call = result.content[0]
    assert isinstance(call, ToolCall)
    assert call.arguments == {"path": "x"}
    assert call.thought_signature == "signature"
    assert call.partial_args is None
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer synthetic"
    assert "synthetic" not in request.content.decode()


@respx.mock
async def test_proxy_processes_terminal_event_without_trailing_newline(dummy_model):
    payload = json.dumps({"type": "done", "reason": "stop", "usage": {}})
    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(200, content=f"data: {payload}".encode())
    )
    result = await stream_proxy(dummy_model, _context(), _options()).result()
    assert result.stop_reason is StopReason.STOP


@respx.mock
async def test_proxy_incremental_utf8_decoder_handles_split_multibyte(dummy_model):
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

    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(200, stream=SplitStream())
    )
    result = await stream_proxy(dummy_model, _context(), _options()).result()
    assert text_of(result) == "你好"


@respx.mock
async def test_proxy_clean_eof_without_terminal_is_error_not_hang(dummy_model):
    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(200, content=_sse([{"type": "start"}]))
    )
    result = await stream_proxy(dummy_model, _context(), _options()).result()
    assert result.stop_reason is StopReason.ERROR
    assert "before the response completed" in (result.error_message or "")


@respx.mock(assert_all_called=False)
async def test_proxy_pre_abort_returns_aborted_without_request(dummy_model):
    route = respx.post("https://proxy.test/api/stream")
    controller = AbortController()
    controller.abort()
    options = _options()
    options.signal = controller.signal
    result = await stream_proxy(dummy_model, _context(), options).result()
    assert result.stop_reason is StopReason.ABORTED
    assert route.call_count == 0


@respx.mock
async def test_proxy_rejects_negative_content_index_as_error_result(dummy_model):
    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse(
                [
                    {"type": "text_start", "contentIndex": -1},
                ]
            ),
        )
    )
    result = await stream_proxy(dummy_model, _context(), _options()).result()
    assert result.stop_reason is StopReason.ERROR
    assert "contentIndex" in (result.error_message or "")
    assert result.error_kind is NovaErrorKind.INTERNAL


@pytest.mark.parametrize("value", ["0", False])
@respx.mock
async def test_proxy_rejects_non_integer_content_index(dummy_model, value):
    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse([{"type": "text_start", "contentIndex": value}]),
        )
    )
    result = await stream_proxy(dummy_model, _context(), _options()).result()
    assert result.stop_reason is StopReason.ERROR
    assert result.error_kind is NovaErrorKind.INTERNAL


@respx.mock
async def test_proxy_midstream_abort_returns_aborted_and_closes_driver(dummy_model):
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

    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(200, stream=PendingStream())
    )
    controller = AbortController()
    options = _options()
    options.signal = controller.signal
    stream = stream_proxy(dummy_model, _context(), options)
    controller.abort()
    result = await stream.result()
    assert result.stop_reason is StopReason.ABORTED
    assert result.error_kind is None
    driver = stream.task
    if driver is not None:
        await driver
    assert closed.is_set()


@respx.mock
async def test_proxy_terminal_stops_waiting_for_eof(dummy_model):
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

    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(200, stream=PendingStream())
    )
    stream = stream_proxy(dummy_model, _context(), _options())
    result = await stream.result()
    assert result.stop_reason is StopReason.STOP
    driver = stream.task
    if driver is not None:
        await driver
    assert closed.is_set()


@respx.mock
async def test_proxy_ignores_events_after_terminal(dummy_model):
    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse(
                [
                    {"type": "done", "reason": "stop", "usage": {}},
                    {
                        "type": "error",
                        "reason": "error",
                        "usage": {},
                        "errorMessage": "late error",
                    },
                ]
            ),
        )
    )
    result = await stream_proxy(dummy_model, _context(), _options()).result()
    assert result.stop_reason is StopReason.STOP
    assert result.error_message is None


@respx.mock
async def test_proxy_error_redacts_arbitrary_token_and_preserves_kind(dummy_model):
    token = "proxy-secret-123"
    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(401, json={"error": f"bad credential {token}"})
    )
    options = ProxyStreamOptions(
        proxy_url="https://proxy.test", auth_token=RedactedString(token)
    )
    result = await stream_proxy(dummy_model, _context(), options).result()
    assert result.stop_reason is StopReason.ERROR
    assert result.error_kind is NovaErrorKind.UNAUTHORIZED
    assert token not in (result.error_message or "")


@respx.mock
async def test_proxy_upstream_error_is_redacted_and_normalized(dummy_model):
    token = "upstream-secret-456"
    respx.post("https://proxy.test/api/stream").mock(
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
    options = ProxyStreamOptions(
        proxy_url="https://proxy.test", auth_token=RedactedString(token)
    )
    result = await stream_proxy(dummy_model, _context(), options).result()
    assert result.error_kind is NovaErrorKind.STREAM_DISCONNECTED
    assert token not in (result.error_message or "")


@respx.mock
async def test_proxy_toolcall_end_with_wrong_block_is_loud_error(dummy_model):
    respx.post("https://proxy.test/api/stream").mock(
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
    result = await stream_proxy(dummy_model, _context(), _options()).result()
    assert result.stop_reason is StopReason.ERROR
    assert "non-toolCall" in (result.error_message or "")


@respx.mock
async def test_proxy_preserves_namespace(dummy_model):
    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse(
                [
                    {"type": "start"},
                    {
                        "type": "toolcall_start",
                        "contentIndex": 0,
                        "id": "c",
                        "toolName": "dynamic",
                    },
                    {
                        "type": "toolcall_end",
                        "contentIndex": 0,
                        "toolCall": {
                            "type": "toolCall",
                            "id": "c",
                            "name": "dynamic",
                            "namespace": "dynamic_tools",
                        },
                    },
                    {"type": "done", "reason": "stop", "usage": {}},
                ]
            ),
        )
    )
    result = await stream_proxy(dummy_model, _context(), _options()).result()
    call = result.content[0]
    assert isinstance(call, ToolCall)
    assert call.namespace == "dynamic_tools"


async def test_proxy_options_repr_uses_safe_whitelist():
    options = ProxyStreamOptions(
        proxy_url="https://user:pass@proxy.test/path?token=url-secret#fragment",
        auth_token=RedactedString("token-secret"),
        metadata={"api_key": "metadata-secret"},
    )
    rendered = repr(options)
    assert "proxy.test" in rendered
    for secret in ("user", "pass", "url-secret", "token-secret", "metadata-secret"):
        assert secret not in rendered


@pytest.mark.asyncio
@respx.mock
async def test_proxy_serializes_model_context_and_supported_options(dummy_model):
    from nova_protocol import ThinkingLevel

    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(
            200, content=_sse([{"type": "done", "reason": "stop", "usage": {}}])
        )
    )
    options = ProxyStreamOptions(
        proxy_url="https://proxy.test",
        auth_token=RedactedString("synthetic"),
        temperature=0.2,
        max_tokens=128,
        reasoning=ThinkingLevel.HIGH,
        session_id="session-1",
        headers={"X-Trace": "trace-1"},
        metadata={"tenant": "test"},
        max_retry_delay_ms=500,
    )
    route = respx.routes[-1]
    result = await stream_proxy(dummy_model, _context(), options).result()

    assert result.stop_reason is StopReason.STOP
    request = route.calls[0].request
    body = json.loads(request.content)
    assert set(body) == {"model", "context", "options"}
    assert body["model"] == dummy_model.dump_wire(exclude_none=True)
    assert body["context"] == _context().dump_wire(exclude_none=True)
    assert body["options"] == {
        "temperature": 0.2,
        "maxTokens": 128,
        "reasoning": "high",
        "sessionId": "session-1",
        "headers": {"X-Trace": "trace-1"},
        "metadata": {"tenant": "test"},
        "maxRetryDelayMs": 500,
    }
    assert request.headers["content-type"] == "application/json"
    assert "synthetic" not in request.content.decode()


@pytest.mark.asyncio
@respx.mock
async def test_proxy_rebuilds_thinking_and_signature(dummy_model):
    from nova_protocol import ThinkingContent

    respx.post("https://proxy.test/api/stream").mock(
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
    result = await stream_proxy(dummy_model, _context(), _options()).result()

    assert result.content == [
        ThinkingContent(thinking="思考", thinking_signature="signature")
    ]
    assert result.provider_thinking_level == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [b"data: {not-json}\n\n", _sse([{"type": "future_event"}])],
    ids=["malformed-json", "unknown-event"],
)
@respx.mock
async def test_proxy_rejects_invalid_wire_events_as_error(dummy_model, payload):
    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(200, content=payload)
    )
    result = await stream_proxy(dummy_model, _context(), _options()).result()

    assert result.stop_reason is StopReason.ERROR
    assert result.error_kind is NovaErrorKind.INTERNAL
    assert result.error_message


@pytest.mark.asyncio
@respx.mock
async def test_proxy_http_rate_limit_preserves_retry_after(dummy_model):
    respx.post("https://proxy.test/api/stream").mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "2"},
            json={"error": "slow down"},
        )
    )
    result = await stream_proxy(dummy_model, _context(), _options()).result()

    assert result.stop_reason is StopReason.ERROR
    assert result.error_kind is NovaErrorKind.RATE_LIMITED
    assert result.retry_after_ms == 2000
    assert "slow down" in (result.error_message or "")


@pytest.mark.asyncio
@respx.mock
async def test_proxy_terminal_error_preserves_metadata(dummy_model):
    respx.post("https://proxy.test/api/stream").mock(
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
    result = await stream_proxy(dummy_model, _context(), _options()).result()

    assert result.error_kind is NovaErrorKind.RATE_LIMITED
    assert result.retry_after_ms == 2500
    assert result.provider_thinking_level == "high"
