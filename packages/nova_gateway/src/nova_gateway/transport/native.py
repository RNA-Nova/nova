"""原生 /healthz、/api/models 与 /api/stream 端点。"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from nova_ai.utils.sanitize import redact_secrets
from nova_gateway_protocol import ErrorPayload, ErrorResponse, StreamRequest
from nova_protocol import NovaErrorKind

from ..native import log_gateway_terminal, to_gateway_event, to_simple_options
from ..runtime import GatewayRequest, UnknownModelError, UnsupportedOptionError

router = APIRouter()


def _error(status: int, message: str, kind: NovaErrorKind) -> JSONResponse:
    return JSONResponse(
        ErrorResponse(
            error=ErrorPayload(message=redact_secrets(message), kind=kind)
        ).dump_wire(),
        status_code=status,
    )


@router.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"status": True}


@router.get("/api/models")
async def list_models(request: Request) -> dict:
    return request.app.state.runtime.list_models().dump_wire(exclude_none=True)


async def _events(
    gateway_request: GatewayRequest, request: Request
) -> AsyncIterator[str]:
    """每 100ms 检查一次断连；终态、断连与关停统一经 request.close 收尾。"""
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
                payload = json.dumps(
                    gateway_event.dump_wire(exclude_none=True), ensure_ascii=False
                )
                yield f"data: {payload}\n\n"
    finally:
        await gateway_request.close()


@router.post("/api/stream")
async def stream(request: Request, body: StreamRequest) -> Response:
    runtime = request.app.state.runtime
    try:
        gateway_request = runtime.stream(
            body,
            to_simple_options(body.options, None),
            request_id=request.state.request_id,
        )
    except UnknownModelError as error:
        return _error(404, str(error), NovaErrorKind.MODEL_NOT_FOUND)
    except UnsupportedOptionError as error:
        return _error(400, str(error), NovaErrorKind.BAD_REQUEST)
    except Exception as error:
        return _error(500, str(error), NovaErrorKind.UPSTREAM_ERROR)
    return StreamingResponse(
        _events(gateway_request, request), media_type="text/event-stream"
    )


__all__ = ["router"]
