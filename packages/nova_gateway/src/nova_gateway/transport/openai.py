"""OpenAI 兼容端点（仅 Chat Completions 常用子集）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from nova_protocol import DoneEvent, ErrorEvent, NovaErrorKind, StopReason

from ..native import log_gateway_terminal, to_gateway_event, to_simple_options
from ..openai import (
    OpenAIChatRequest,
    OpenAIError,
    new_stream_context,
    openai_error_payload,
    to_native_request,
    to_openai_chunk,
    to_openai_response,
)
from ..runtime import GatewayRequest, UnknownModelError, UnsupportedOptionError

router = APIRouter()


def _status(kind: NovaErrorKind) -> int:
    if kind == NovaErrorKind.UNAUTHORIZED:
        return 401
    if kind == NovaErrorKind.MODEL_NOT_FOUND:
        return 404
    if kind == NovaErrorKind.BAD_REQUEST:
        return 400
    if kind == NovaErrorKind.RATE_LIMITED:
        return 429
    if kind == NovaErrorKind.REQUEST_TIMEOUT:
        return 408
    return 500


def _error(kind: NovaErrorKind, error: Exception) -> JSONResponse:
    return JSONResponse(openai_error_payload(error), status_code=_status(kind))


@router.get("/v1/models")
async def list_models(request: Request) -> dict:
    response = request.app.state.runtime.list_models()
    return {
        "object": "list",
        "data": [
            {
                "id": f"{model.provider}/{model.id}",
                "object": "model",
                "created": 0,
                "owned_by": model.provider,
            }
            for model in response.models
        ],
    }


async def _openai_events(
    gateway_request: GatewayRequest, request: Request, context
) -> AsyncIterator[str]:
    try:
        while True:
            next_event = asyncio.create_task(gateway_request.stream.__anext__())
            finished, _ = await asyncio.wait({next_event}, timeout=0.1)
            if not finished:
                next_event.cancel()
                try:
                    await next_event
                except asyncio.CancelledError:
                    pass
                if await request.is_disconnected():
                    break
                continue
            try:
                event = next_event.result()
            except StopAsyncIteration:
                break
            gateway_event = to_gateway_event(event)
            if gateway_event is not None:
                log_gateway_terminal(gateway_request, gateway_event)
                chunk = to_openai_chunk(gateway_event, context)
                if chunk is not None:
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        await gateway_request.close()


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, body: OpenAIChatRequest):
    try:
        native_request = to_native_request(body)
        gateway_request = request.app.state.runtime.stream(
            native_request,
            to_simple_options(native_request.options, None),
            request_id=request.state.request_id,
        )
    except OpenAIError as error:
        return _error(NovaErrorKind.BAD_REQUEST, error)
    except UnknownModelError as error:
        return _error(NovaErrorKind.MODEL_NOT_FOUND, error)
    except UnsupportedOptionError as error:
        return _error(NovaErrorKind.BAD_REQUEST, error)
    except Exception as error:
        return _error(NovaErrorKind.UPSTREAM_ERROR, error)
    if body.stream:
        return StreamingResponse(
            _openai_events(gateway_request, request, new_stream_context(body)),
            media_type="text/event-stream",
        )
    try:
        result = await gateway_request.stream.result()
        # 旧 Provider 只更新 DoneEvent.reason，result 可能仍是 pending；
        # 与非流式响应的既有 finish_reason 映射一致，日志按 stop 处理。
        terminal_reason = (
            StopReason.STOP
            if result.stop_reason == StopReason.PENDING
            else result.stop_reason
        )
        internal_terminal = (
            ErrorEvent(reason=cast(Any, terminal_reason), error=result)
            if terminal_reason in (StopReason.ERROR, StopReason.ABORTED)
            else DoneEvent(reason=cast(Any, terminal_reason), message=result)
        )
        terminal = to_gateway_event(internal_terminal)
        if terminal is not None:
            log_gateway_terminal(gateway_request, terminal)
    finally:
        await gateway_request.close()
    if result.stop_reason in (StopReason.ERROR, StopReason.ABORTED):
        kind = result.error_kind or NovaErrorKind.UPSTREAM_ERROR
        return JSONResponse(
            openai_error_payload(
                RuntimeError(result.error_message or "Upstream error")
            ),
            status_code=_status(kind),
        )
    return to_openai_response(result, body)


__all__ = ["router"]
