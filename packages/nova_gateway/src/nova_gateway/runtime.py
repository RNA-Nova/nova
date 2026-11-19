"""长期模型运行时、模型白名单与在途请求生命周期。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from nova_ai import AssistantMessageEventStream, ModelsManager, SimpleStreamOptions
from nova_async import AbortController
from nova_gateway_protocol import (
    ModelListItem,
    ModelListResponse,
    ModelRef,
    StreamRequest,
)
from nova_protocol import Model

from .config import GatewayConfig
from .upstreams import build_models_manager


class UnknownModelError(ValueError):
    """客户端选择了未登记或不允许调用的模型。"""


class UnsupportedOptionError(ValueError):
    """客户端请求了服务端不支持的选项。"""


logger = logging.getLogger(__name__)


@dataclass(eq=False)
class GatewayRequest:
    stream: AssistantMessageEventStream
    controller: AbortController
    provider: str
    model: str
    transport: str
    request_id: Optional[str] = None
    started_at: float = field(default_factory=time.perf_counter, repr=False)

    @property
    def task(self) -> Optional[asyncio.Task]:
        return self.stream.task

    @property
    def log_fields(self) -> dict[str, str]:
        fields = {
            "provider": self.provider,
            "model": self.model,
            "transport": self.transport,
        }
        if self.request_id is not None:
            fields["requestId"] = self.request_id
        return fields

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started_at) * 1000, 3)

    async def close(self) -> None:
        was_done = self.stream.done
        try:
            if not self.stream.done:
                self.controller.abort()
            task = self.stream.task
            if task is not None:
                # 关停路径不得让 driver 的取消/清理异常覆盖用户可见终态。
                await asyncio.gather(task, return_exceptions=True)
        finally:
            logger.info(
                "gateway.stream.closed",
                extra={
                    **self.log_fields,
                    "status": "done" if was_done else "aborted",
                    "durationMs": self.elapsed_ms(),
                },
            )


class GatewayRuntime:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.manager: ModelsManager = build_models_manager(config.upstreams)
        self._requests: set[GatewayRequest] = set()
        self._closed = False

    def _allowed(self, provider: str, model_id: str) -> bool:
        return (
            not self.config.allow_models
            or f"{provider}/{model_id}" in self.config.allow_models
        )

    def list_models(self) -> ModelListResponse:
        models = [
            model
            for model in self.manager.get_models()
            if self._allowed(model.provider, model.id)
        ]
        return ModelListResponse(
            models=[ModelListItem.from_model(model) for model in models]
        )

    def resolve_model(self, ref: ModelRef) -> Model:
        if not self._allowed(ref.provider, ref.id):
            raise UnknownModelError(f"Model not found: {ref.provider}/{ref.id}")
        model = self.manager.get_model_of_type("chat", ref.provider, ref.id)
        if not isinstance(model, Model):
            raise UnknownModelError(f"Model not found: {ref.provider}/{ref.id}")
        return model

    def stream(
        self,
        request: StreamRequest,
        options: SimpleStreamOptions,
        *,
        request_id: Optional[str] = None,
    ) -> GatewayRequest:
        if self._closed:
            raise RuntimeError("Gateway runtime is closed")
        model = self.resolve_model(request.model)
        controller = AbortController()
        options.signal = controller.signal
        stream = self.manager.stream_simple(model, request.context, options)
        gateway_request = GatewayRequest(
            stream=stream,
            controller=controller,
            provider=model.provider,
            model=model.id,
            transport=model.api,
            request_id=request_id,
        )
        self._requests.add(gateway_request)
        if stream.task is not None:
            stream.task.add_done_callback(
                lambda _task: self._requests.discard(gateway_request)
            )
        logger.info("gateway.upstream.connected", extra=gateway_request.log_fields)
        return gateway_request

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        requests = list(self._requests)
        if requests:
            await asyncio.gather(
                *(request.close() for request in requests), return_exceptions=True
            )


__all__ = [
    "GatewayRequest",
    "GatewayRuntime",
    "UnknownModelError",
    "UnsupportedOptionError",
]
