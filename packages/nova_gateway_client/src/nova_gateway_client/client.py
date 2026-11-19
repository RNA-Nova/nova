"""独立的 Nova Gateway SDK：模型目录、公共目录与公开 SSE 事件流。"""

from __future__ import annotations

import asyncio
import json
import random
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from nova_gateway_protocol import (
    GATEWAY_PROTOCOL_HEADER,
    GATEWAY_PROTOCOL_MAJOR,
    ErrorResponse,
    ModelListResponse,
    StreamRequest,
    protocol_major,
)
from nova_protocol import AnyModel, ModelType, NovaErrorKind
from pydantic import TypeAdapter

from .catalog import CatalogResult
from .errors import GatewayProtocolError, http_error
from .response import GatewayResponse
from .streaming import GatewayEventStream
from .transport import (
    JitterFn,
    RequestExecutor,
    ResponseObserver,
    SleepFn,
    TransportResult,
    retry_after_ms,
)

_ANY_MODELS_ADAPTER = TypeAdapter(List[AnyModel])


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _status_kind(status_code: int) -> Optional[NovaErrorKind]:
    if status_code == 400:
        return NovaErrorKind.BAD_REQUEST
    if status_code in (401, 403):
        return NovaErrorKind.UNAUTHORIZED
    if status_code == 404:
        return NovaErrorKind.MODEL_NOT_FOUND
    if status_code == 408:
        return NovaErrorKind.REQUEST_TIMEOUT
    if status_code in (409, 529):
        return NovaErrorKind.OVERLOADED
    if status_code == 429:
        return NovaErrorKind.RATE_LIMITED
    if status_code >= 500:
        return NovaErrorKind.UPSTREAM_ERROR
    return None


class NovaGatewayClient:
    """只依赖公开 DTO 的 SDK；不感知 nova_ai 的运行时事件或选项。"""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout: Optional[float] = None,
        default_headers: Optional[Dict[str, str]] = None,
        max_retries: int = 2,
        max_retry_delay_ms: int = 60000,
        sleep_fn: SleepFn = asyncio.sleep,
        jitter_fn: JitterFn = random.random,
    ) -> None:
        if not base_url:
            raise ValueError("Gateway base_url is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._executor = RequestExecutor(
            api_key=api_key,
            http_client=http_client,
            default_timeout=timeout,
            default_headers=default_headers,
            max_retries=max_retries,
            max_retry_delay_ms=max_retry_delay_ms,
            sleep_fn=sleep_fn,
            jitter_fn=jitter_fn,
        )

    def __repr__(self) -> str:
        return f"NovaGatewayClient(base_url={_safe_url(self.base_url)!r})"

    async def __aenter__(self) -> "NovaGatewayClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """幂等中断全部在途流并关闭自有 HTTP 资源。"""
        await self._executor.close()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _redact_token(self, message: str) -> str:
        if self.api_key:
            return message.replace(self.api_key, "[REDACTED_SECRET]")
        return message

    def _check_protocol(self, response: httpx.Response) -> None:
        value = response.headers.get(GATEWAY_PROTOCOL_HEADER)
        if value is None:
            return
        try:
            major = protocol_major(value)
        except ValueError as error:
            raise GatewayProtocolError("Invalid gateway protocol version") from error
        if major != GATEWAY_PROTOCOL_MAJOR:
            raise GatewayProtocolError(f"Unsupported gateway protocol major: {major}")

    async def _request(
        self,
        method: str,
        url: str,
        *,
        allow_not_found: bool = False,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> TransportResult:
        result = await self._executor.execute(
            method, url, headers=headers, params=params
        )
        response = result.response
        self._check_protocol(response)
        if (
            200 <= response.status_code < 300
            or response.status_code == 304
            or (allow_not_found and response.status_code == 404)
        ):
            return result
        await self._raise_for_status(response)
        raise AssertionError("unreachable")

    async def _raise_for_status(self, response: httpx.Response) -> None:
        await response.aread()
        message = f"Gateway error: {response.status_code} {response.reason_phrase}"
        body: Any = response.text
        kind = _status_kind(response.status_code)
        retry_ms = retry_after_ms(response.headers)
        try:
            raw = response.json()
            body = raw
            payload = ErrorResponse.model_validate(raw)
            message = payload.error.message
            kind = payload.error.kind or kind
            retry_ms = payload.error.retry_after_ms or retry_ms
        except Exception:
            pass
        raise http_error(
            self._redact_token(message),
            response,
            body=body,
            kind=kind,
            retry_after_ms=retry_ms,
        )

    async def list_models_with_response(self) -> GatewayResponse[ModelListResponse]:
        """返回模型列表及 HTTP 元数据。"""
        result = await self._request("GET", self._url("/api/models"))
        try:
            data = ModelListResponse.model_validate(result.response.json())
        except ValueError as error:
            raise GatewayProtocolError("Invalid model list payload") from error
        return GatewayResponse(
            data=data,
            response=result.response,
            retries_taken=result.retries_taken,
        )

    async def list_models(self) -> ModelListResponse:
        """返回当前服务端允许调用的模型集合。"""
        return (await self.list_models_with_response()).data

    async def catalog_with_response(
        self,
        provider: str,
        *,
        types: tuple[ModelType, ...] = ("chat", "image", "classifier"),
        validator: Optional[str] = None,
    ) -> GatewayResponse[CatalogResult]:
        """返回公共目录切片及 HTTP 元数据。"""
        headers = {"If-None-Match": validator} if validator else None
        result = await self._request(
            "GET",
            self._url(f"/api/models/providers/{quote(provider, safe='')}"),
            headers=headers,
            params={"types": ",".join(types)},
            allow_not_found=True,
        )
        response = result.response
        if response.status_code == 304:
            result_data = CatalogResult(status=304, etag=validator)
        elif response.status_code == 404:
            result_data = CatalogResult(status=404)
        else:
            try:
                payload = response.json()
            except ValueError as error:
                raise GatewayProtocolError(
                    f"Invalid catalog payload for {provider}"
                ) from error
            if isinstance(payload, list):
                entries = payload
            elif isinstance(payload, dict) and isinstance(payload.get("models"), list):
                entries = payload["models"]
            elif isinstance(payload, dict):
                entries = list(payload.values())
            else:
                raise GatewayProtocolError(f"Invalid catalog payload for {provider}")
            normalized = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise GatewayProtocolError(f"Invalid catalog entry for {provider}")
                item = dict(entry)
                if "input" in item:
                    item["input_types"] = item.pop("input")
                if "output" in item:
                    item["output_types"] = item.pop("output")
                normalized.append(item)
            last_modified: Optional[int] = None
            header = response.headers.get("last-modified")
            if header:
                parsed = parsedate_to_datetime(header)
                last_modified = int(parsed.timestamp() * 1000)
            try:
                models = _ANY_MODELS_ADAPTER.validate_python(normalized)
            except ValueError as error:
                raise GatewayProtocolError(
                    f"Invalid catalog payload for {provider}"
                ) from error
            result_data = CatalogResult(
                status=200,
                models=models,
                etag=response.headers.get("etag"),
                last_modified=last_modified,
            )
        return GatewayResponse(
            data=result_data,
            response=response,
            retries_taken=result.retries_taken,
        )

    async def catalog(
        self,
        provider: str,
        *,
        types: tuple[ModelType, ...] = ("chat", "image", "classifier"),
        validator: Optional[str] = None,
    ) -> CatalogResult:
        """拉取公共目录切片；支持 ETag/304 与旧 keyed chat 形状。"""
        return (
            await self.catalog_with_response(provider, types=types, validator=validator)
        ).data

    def stream(
        self,
        request: StreamRequest,
        *,
        on_response: Optional[ResponseObserver] = None,
    ) -> GatewayEventStream:
        """创建单次公开事件流；首次消费时才建连与重试。"""
        return GatewayEventStream(
            executor=self._executor,
            url=self._url("/api/stream"),
            request=request,
            on_response=on_response,
            status_handler=self._raise_for_status,
        )


__all__ = ["NovaGatewayClient"]
