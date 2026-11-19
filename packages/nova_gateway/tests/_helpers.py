"""服务端测试的假 Provider 与事件流。"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, Iterable, Optional, cast

import uvicorn
from nova_ai import AssistantMessageEventStream, SimpleStreamOptions
from nova_ai.auth import ProviderAuth, env_api_key_auth
from nova_ai.gateway import create_provider
from nova_async import AbortedError, await_with_abort, is_aborted
from nova_protocol import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    DoneEvent,
    ErrorEvent,
    Model,
    ModelCost,
    StartEvent,
    StopReason,
    Usage,
)


def model(provider: str = "fake", model_id: str = "model-a") -> Model:
    return Model(
        id=model_id,
        name="Model A",
        api="openai-completions",
        provider=provider,
        base_url="https://upstream.test/v1",
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=1, output=2, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=8192,
    )


def partial(
    model: Model, *, stop_reason: StopReason = StopReason.PENDING
) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        stop_reason=stop_reason,
        usage=Usage(),
    )


class FakeStreams:
    def __init__(
        self,
        events: Optional[Iterable[AssistantMessageEvent]] = None,
        *,
        pending: bool = False,
    ) -> None:
        self.events = list(events or [])
        self.pending = pending
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ):
        return self._drive(model, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ):
        return self._drive(model, options)

    def _drive(self, model: Model, options: Optional[SimpleStreamOptions]):
        stream = AssistantMessageEventStream()
        options = options or SimpleStreamOptions()

        async def _run():
            self.started.set()
            try:
                if self.pending:
                    try:
                        await await_with_abort(asyncio.Event().wait(), options.signal)
                    except AbortedError:
                        message = partial(model, stop_reason=StopReason.ABORTED)
                        message.error_message = "Request was aborted"
                        stream.push(
                            ErrorEvent(reason=StopReason.ABORTED, error=message)
                        )
                        return
                    finally:
                        self.closed.set()
                    if is_aborted(options.signal):
                        message = partial(model, stop_reason=StopReason.ABORTED)
                        message.error_message = "Request was aborted"
                        stream.push(
                            ErrorEvent(reason=StopReason.ABORTED, error=message)
                        )
                        return
                for event in self.events:
                    if is_aborted(options.signal):
                        message = partial(model, stop_reason=StopReason.ABORTED)
                        message.error_message = "Request was aborted"
                        stream.push(
                            ErrorEvent(reason=StopReason.ABORTED, error=message)
                        )
                        return
                    await asyncio.sleep(0)
                    stream.push(event)
            finally:
                self.closed.set()

        stream.drive(_run())
        return stream


def provider(
    events: Optional[Iterable[AssistantMessageEvent]] = None, *, pending: bool = False
):
    os.environ.setdefault("FAKE_API_KEY", "fake")
    streams = FakeStreams(events, pending=pending)
    return (
        create_provider(
            id="fake",
            name="Fake",
            base_url="https://upstream.test/v1",
            models=[model()],
            api=cast(Any, streams),
            auth=ProviderAuth(api_key=env_api_key_auth("fake", ["FAKE_API_KEY"])),
        ),
        streams,
    )


@asynccontextmanager
async def serve(app):
    """真实 uvicorn loopback 服务；测试结束后有界关停。"""
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    try:

        async def _wait_started():
            while not server.started:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_wait_started(), timeout=2)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


__all__ = ["FakeStreams", "model", "partial", "provider", "serve"]
