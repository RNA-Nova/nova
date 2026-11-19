"""独立流句柄的关闭、未消费放弃与错误收尾测试。"""

import asyncio

import httpx
import pytest
import respx
from nova_gateway_protocol import ModelRef, StreamRequest
from nova_protocol import Context, UserMessage

from nova_gateway_client import GatewayProtocolError, NovaGatewayClient

pytestmark = pytest.mark.asyncio


def _request() -> StreamRequest:
    return StreamRequest(
        model=ModelRef(provider="test-provider", id="model-a"),
        context=Context(messages=[UserMessage(content="hi")]),
    )


class PendingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = asyncio.Event()

    async def __aiter__(self):
        try:
            yield b'data: {"type":"start"}\n\n'
            await asyncio.Event().wait()
        finally:
            self.closed.set()

    async def aclose(self):
        self.closed.set()


@respx.mock
async def test_stream_close_is_independent_and_idempotent():
    first_pending = PendingStream()
    second_pending = PendingStream()
    respx.post("https://gateway.test/api/stream").mock(
        side_effect=[
            httpx.Response(200, stream=first_pending),
            httpx.Response(200, stream=second_pending),
        ]
    )
    async with NovaGatewayClient("https://gateway.test", "token") as client:
        first = client.stream(_request())
        second = client.stream(_request())
        assert (await first.__anext__()).type == "start"
        assert (await second.__anext__()).type == "start"
        await first.close()
        await first.close()
        with pytest.raises(StopAsyncIteration):
            await first.__anext__()
        assert first.closed
        assert not second.closed
        await second.close()
        await client.close()


@respx.mock
async def test_close_before_first_iteration_sends_no_request():
    route = respx.post("https://gateway.test/api/stream")
    async with NovaGatewayClient("https://gateway.test", "token") as client:
        stream = client.stream(_request())
        await stream.close()
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
    assert route.call_count == 0


class InvalidStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = asyncio.Event()

    async def __aiter__(self):
        try:
            yield b"data: {not-json}\n\n"
        finally:
            self.closed.set()

    async def aclose(self):
        self.closed.set()


@respx.mock
async def test_protocol_error_closes_response():
    stream = InvalidStream()
    respx.post("https://gateway.test/api/stream").mock(
        return_value=httpx.Response(200, stream=stream)
    )
    async with NovaGatewayClient("https://gateway.test", "token") as client:
        with pytest.raises(GatewayProtocolError):
            [event async for event in client.stream(_request())]
    assert stream.closed.is_set()
