"""调用独立 Nova Gateway SDK，并重建 Nova 标准助手事件流。"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Optional

import httpx
from nova_async import AbortedError, AbortScope, is_aborted
from nova_gateway_client import NovaGatewayClient
from nova_gateway_protocol import ModelRef, StreamRequest
from nova_protocol import AssistantMessage, Context, ErrorEvent, Model, StopReason

from ...stream_options import (
    ModelIdentity,
    ProviderResponse,
    SimpleStreamOptions,
    StreamOptions,
)
from ...streaming import AssistantMessageEventStream
from ...utils.errors import normalize_error
from ...utils.sanitize import redact_secrets
from .events import EventReconstructor, new_partial
from .options import as_simple_options, to_wire_options


def _redact_text(value: Any, token: Optional[str]) -> str:
    text = str(value)
    if token:
        text = text.replace(token, "[REDACTED_SECRET]")
    return redact_secrets(text)


def _normalize_error(
    partial: AssistantMessage,
    error: BaseException,
    token: Optional[str],
    model: Model,
    *,
    preserve_message: bool = False,
) -> None:
    normalized = normalize_error(error, domain="proxy", provider=model.provider)
    if partial.error_kind is None:
        partial.error_kind = normalized.kind
    if partial.retry_after_ms is None and normalized.retry_after_ms is not None:
        partial.retry_after_ms = int(normalized.retry_after_ms)
    message = (
        partial.error_message
        if preserve_message and partial.error_message
        else normalized.message
    )
    partial.error_message = _redact_text(message, token)


def stream(
    model: Model, context: Context, options: Optional[StreamOptions] = None
) -> AssistantMessageEventStream:
    """完整 ProviderStreams 入口。"""
    return _start(model, context, as_simple_options(options))


def stream_simple(
    model: Model, context: Context, options: Optional[SimpleStreamOptions] = None
) -> AssistantMessageEventStream:
    """简化 ProviderStreams 入口。"""
    return _start(model, context, as_simple_options(options))


def _start(
    model: Model, context: Context, options: SimpleStreamOptions
) -> AssistantMessageEventStream:
    result = AssistantMessageEventStream()
    result.drive(_run_stream(result, model, context, options))
    return result


async def _run_stream(
    result: AssistantMessageEventStream,
    model: Model,
    context: Context,
    options: SimpleStreamOptions,
) -> None:
    partial = new_partial(model)
    token = options.api_key
    signal = options.signal
    client = NovaGatewayClient(model.base_url, token)
    reconstructor = EventReconstructor(partial)
    identity = ModelIdentity.from_model(model)
    try:
        with AbortScope(signal) as abort_scope:
            request = StreamRequest(
                model=ModelRef(provider=model.provider, id=model.id),
                context=context,
                options=to_wire_options(options),
            )
            if options.on_payload is not None:
                replacement = options.on_payload(
                    request.dump_wire(exclude_none=True), identity
                )
                if inspect.isawaitable(replacement):
                    replacement = await replacement
                if replacement is not None:
                    if not isinstance(replacement, dict):
                        raise TypeError(
                            "on_payload must return a request object or None"
                        )
                    request = StreamRequest.model_validate(replacement)

            def _observe_response(response: httpx.Response) -> Any:
                if options.on_response is None:
                    return None
                return options.on_response(
                    ProviderResponse(
                        status=response.status_code,
                        headers=dict(response.headers),
                    ),
                    identity,
                )

            async for wire_event in client.stream(
                request, on_response=_observe_response
            ):
                if options.on_provider_stream_event is not None:
                    observed = options.on_provider_stream_event(wire_event, identity)
                    if inspect.isawaitable(observed):
                        await observed
                event = reconstructor.process(wire_event)
                if event is None:
                    continue
                if isinstance(event, ErrorEvent):
                    if event.reason == StopReason.ABORTED:
                        partial.error_kind = None
                        partial.retry_after_ms = None
                        partial.error_message = _redact_text(
                            partial.error_message or "Request was aborted", token
                        )
                    else:
                        _normalize_error(
                            partial,
                            RuntimeError(
                                partial.error_message or "Gateway returned an error"
                            ),
                            token,
                            model,
                            preserve_message=True,
                        )
                    event = ErrorEvent(reason=event.reason, error=partial)
                result.push(event)
        abort_scope.raise_if_aborted()
    except asyncio.CancelledError:
        if not is_aborted(signal):
            raise
        if result.done:
            return
        partial.stop_reason = StopReason.ABORTED
        partial.error_kind = None
        partial.retry_after_ms = None
        partial.error_message = "Request was aborted"
        result.push(ErrorEvent(reason=StopReason.ABORTED, error=partial))
    except Exception as error:
        if result.done:
            return
        aborted = is_aborted(signal) or isinstance(error, AbortedError)
        partial.stop_reason = StopReason.ABORTED if aborted else StopReason.ERROR
        if aborted:
            partial.error_kind = None
            partial.retry_after_ms = None
            partial.error_message = _redact_text(
                str(error) or "Request was aborted", token
            )
        else:
            _normalize_error(partial, error, token, model)
        result.push(ErrorEvent(reason=partial.stop_reason, error=partial))
    finally:
        await client.close()


__all__ = ["stream", "stream_simple"]
