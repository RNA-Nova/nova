"""公共目录 HTTP 端点（匿名、无账户态）。"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from nova_gateway_protocol import ErrorPayload, ErrorResponse
from nova_protocol import NovaErrorKind

from ..catalog import http_date

router = APIRouter()
_ALLOWED_TYPES = frozenset({"chat", "image", "classifier"})


@router.get("/api/models/providers")
async def providers(request: Request) -> list[str]:
    return request.app.state.catalog.providers()


@router.get("/api/models/providers/{provider}")
async def provider_catalog(provider: str, request: Request) -> Response:
    raw_types = request.query_params.get("types", "chat,image,classifier")
    types = tuple(part.strip() for part in raw_types.split(",") if part.strip())
    if not types or any(part not in _ALLOWED_TYPES for part in types):
        payload = ErrorResponse(
            error=ErrorPayload(
                message=f"Unsupported model types: {raw_types}",
                kind=NovaErrorKind.BAD_REQUEST,
            )
        )
        return JSONResponse(payload.dump_wire(), status_code=400)
    result = request.app.state.catalog.get(
        provider,
        types=types,
        validator=request.headers.get("if-none-match"),
    )
    if result.status == 404:
        payload = ErrorResponse(
            error=ErrorPayload(
                message=f"Catalog provider not found: {provider}",
                kind=NovaErrorKind.MODEL_NOT_FOUND,
            )
        )
        return JSONResponse(payload.dump_wire(), status_code=404)
    if result.status == 304:
        return Response(status_code=304, headers={"ETag": result.etag or ""})
    headers = {"ETag": result.etag or ""}
    if result.last_modified is not None:
        headers["Last-Modified"] = http_date(result.last_modified)
    return JSONResponse(result.body, headers=headers)


__all__ = ["router"]
