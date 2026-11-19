"""ASGI 组合根：配置、版本协商、鉴权、路由与 lifespan。"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from nova_ai.utils.sanitize import redact_secrets
from nova_gateway_protocol import (
    GATEWAY_IDEMPOTENCY_HEADER,
    GATEWAY_PROTOCOL_HEADER,
    GATEWAY_PROTOCOL_MAJOR,
    GATEWAY_PROTOCOL_VERSION,
    GATEWAY_REQUEST_ID_HEADER,
    ErrorPayload,
    ErrorResponse,
    protocol_major,
)
from nova_logging import log_context
from nova_protocol import NovaErrorKind

from .auth import GatewayAuthMiddleware
from .catalog import CatalogPublisher
from .config import GatewayConfig
from .openai import openai_error_payload
from .runtime import GatewayRuntime
from .transport.catalog import router as catalog_router
from .transport.native import router as native_router
from .transport.openai import router as openai_router


def create_app(
    config: GatewayConfig, *, runtime: Optional[GatewayRuntime] = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_runtime = app.state.runtime
        try:
            yield
        finally:
            await active_runtime.close()

    app = FastAPI(lifespan=lifespan)
    app.state.runtime = runtime or GatewayRuntime(config)
    app.state.catalog = CatalogPublisher(
        config.catalog_dir or Path.home() / ".nova" / "agent" / "gateway" / "catalog"
    )
    if config.api_token:
        app.add_middleware(GatewayAuthMiddleware, token=config.api_token)
    if config.allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.allow_origins,
            allow_methods=["*"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "If-None-Match",
                GATEWAY_PROTOCOL_HEADER,
                GATEWAY_REQUEST_ID_HEADER,
                GATEWAY_IDEMPOTENCY_HEADER,
            ],
            expose_headers=[
                GATEWAY_PROTOCOL_HEADER,
                GATEWAY_REQUEST_ID_HEADER,
                "ETag",
                "Last-Modified",
            ],
        )

    logger = logging.getLogger(__name__)

    @app.middleware("http")
    async def negotiate_protocol(request: Request, call_next):
        request_id = request.headers.get(GATEWAY_REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        with log_context(requestId=request_id):
            started_at = time.perf_counter()
            logger.info("gateway.request.started")
            try:
                presented = request.headers.get(GATEWAY_PROTOCOL_HEADER)
                error: Optional[Exception] = None
                major: Optional[int] = None
                if presented is not None:
                    try:
                        major = protocol_major(presented)
                    except ValueError as caught:
                        error = caught
                if error is not None or (
                    major is not None and major != GATEWAY_PROTOCOL_MAJOR
                ):
                    message = (
                        str(error)
                        if error is not None
                        else f"Unsupported gateway protocol major: {major}"
                    )
                    payload = ErrorResponse(
                        error=ErrorPayload(
                            message=message,
                            kind=NovaErrorKind.BAD_REQUEST,
                        )
                    )
                    response = JSONResponse(
                        payload.dump_wire(),
                        status_code=400,
                        headers={
                            GATEWAY_PROTOCOL_HEADER: GATEWAY_PROTOCOL_VERSION,
                            GATEWAY_REQUEST_ID_HEADER: request_id,
                        },
                    )
                else:
                    response = await call_next(request)
                    response.headers[GATEWAY_PROTOCOL_HEADER] = GATEWAY_PROTOCOL_VERSION
                    response.headers[GATEWAY_REQUEST_ID_HEADER] = request_id
                duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
                event = (
                    "gateway.stream.connected"
                    if response.headers.get("content-type", "").startswith(
                        "text/event-stream"
                    )
                    else "gateway.request.completed"
                )
                logger.info(
                    event,
                    extra={"status": response.status_code, "durationMs": duration_ms},
                )
                return response
            except Exception:
                logger.error(
                    "gateway.request.failed",
                    extra={
                        "status": 500,
                        "errorKind": NovaErrorKind.INTERNAL.value,
                        "durationMs": round(
                            (time.perf_counter() - started_at) * 1000, 3
                        ),
                    },
                    exc_info=True,
                )
                raise

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/v1/"):
            return JSONResponse(openai_error_payload(exc), status_code=400)
        payload = ErrorResponse(
            error=ErrorPayload(
                message=redact_secrets(str(exc)),
                kind=NovaErrorKind.BAD_REQUEST,
            )
        )
        return JSONResponse(payload.dump_wire(), status_code=400)

    app.include_router(native_router)
    app.include_router(catalog_router)
    app.include_router(openai_router)
    return app


__all__ = ["create_app"]
