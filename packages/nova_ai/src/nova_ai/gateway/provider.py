"""Provider 运行时单元（对齐 TS ``src/models.ts`` 的 ``createProvider`` 部分）。

Provider 是独立的运行时单元：持有 auth 配置、模型目录与 stream 调度能力
（``model.api`` → 协议实现的路由），不做 auth 解析（那是 ``Models`` 的职责）。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Union

from ..signal import AbortSignal
from ..streaming import AssistantMessageEventStream
from ..types.aliases import ProviderHeaders
from ..types.auth import Credential, ProviderAuth
from ..types.enums import KnownApi, StopReason
from ..types.events import ErrorEvent
from ..types.messages import AssistantMessage, Context
from ..types.model import Model, Usage
from ..types.stream_options import SimpleStreamOptions, StreamOptions
from .store import ModelsStoreEntry, ProviderModelsStore


class ProviderStreams(Protocol):
    """API 实现模块的统一契约（stream / stream_simple）。"""

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessageEventStream: ...

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> AssistantMessageEventStream: ...


# api_impl 可以是单个实现，也可以按 model.api 分发（对齐 TS Provider.api）
ApiImpl = Union[ProviderStreams, Dict[str, ProviderStreams]]


def _missing_api_error_stream(
    model: Model, provider_id: str
) -> AssistantMessageEventStream:
    """构造一个立即以 error 事件结束的流。

    对齐 TS ``createProvider`` 的 ``dispatch``：找不到 API 实现时不抛异常，
    而是返回携带 error 事件的流（``StreamFunction`` 契约：调用后的失败
    一律编码进流，而不是抛出）。
    """
    error_message = AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        stop_reason=StopReason.ERROR,
        error_message=(
            f"Provider {provider_id} has no API implementation for " f'"{model.api}"'
        ),
        timestamp=int(time.time() * 1000),
    )
    stream = AssistantMessageEventStream()
    stream.push(ErrorEvent(type="error", reason="error", error=error_message))
    return stream


@dataclass
class RefreshModelsContext:
    """``refresh_models`` 调用上下文（对齐 TS RefreshModelsContext）。"""

    credential: Optional[Credential] = None
    store: Optional[ProviderModelsStore] = None
    allow_network: bool = True
    force: bool = False
    signal: Optional[AbortSignal] = None


@dataclass
class Provider:
    """Provider 运行时单元。

    与 TS ``createProvider`` 对齐：持有 ``auth``、``api_impl`` 并暴露
    ``stream()`` / ``stream_simple()`` 调度方法。
    """

    id: str
    name: str
    base_url: Optional[str] = None
    headers: Optional[ProviderHeaders] = None
    models: List[Model] = field(default_factory=list)
    api_impl: Optional[ApiImpl] = None
    auth: Optional[ProviderAuth] = None
    filter_models: Optional[
        Callable[[List[Model], Optional[Credential]], List[Model]]
    ] = None

    def get_models(self) -> List[Model]:
        """返回该 provider 的模型列表（对齐 TS Provider.getModels）。"""
        return list(self.models)

    def get_model(self, model_id: str) -> Optional[Model]:
        """按 model id 查找模型。"""
        for model in self.get_models():
            if model.id == model_id:
                return model
        return None

    def _api_for(self, model: Model) -> Optional[ProviderStreams]:
        """根据 model.api 找到对应的 API 实现。"""
        if self.api_impl is None:
            return None
        if isinstance(self.api_impl, dict):
            api_key = (
                model.api.value if isinstance(model.api, KnownApi) else str(model.api)
            )
            return self.api_impl.get(api_key)
        return self.api_impl

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """使用本 provider 绑定的 API 实现发起流式调用。"""
        api = self._api_for(model)
        if api is None:
            return _missing_api_error_stream(model, self.id)
        return api.stream(model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """使用本 provider 绑定的 API 实现发起简化流式调用。"""
        api = self._api_for(model)
        if api is None:
            return _missing_api_error_stream(model, self.id)
        return api.stream_simple(model, context, options)


class _DynamicProvider(Provider):
    """支持动态模型合并与刷新的 Provider。"""

    def __init__(
        self,
        *args: Any,
        fetch_models: Optional[
            Callable[[RefreshModelsContext], Awaitable[List[Model]]]
        ] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._baseline_models = list(self.models)
        self._dynamic_models: List[Model] = []
        self._fetch_models = fetch_models
        self._inflight_refresh: Optional[asyncio.Task[None]] = None

    def get_models(self) -> List[Model]:
        merged = list(self._baseline_models)
        for model in self._dynamic_models:
            idx = next(
                (i for i, m in enumerate(merged) if m.id == model.id),
                -1,
            )
            if idx >= 0:
                merged[idx] = model
            else:
                merged.append(model)
        return merged

    async def refresh_models(self, context: RefreshModelsContext) -> None:
        if self._fetch_models is None:
            return

        async def _run() -> None:
            if context.store is not None:
                stored = await context.store.read()
                if stored is not None:
                    self._dynamic_models = [
                        m for m in stored.models if m.provider == self.id
                    ]
            if not context.allow_network:
                return
            if context.signal is not None and context.signal.aborted:
                return
            refreshed = await self._fetch_models(context)
            if context.signal is not None and context.signal.aborted:
                return
            self._dynamic_models = list(refreshed)
            if context.store is not None:
                await context.store.write(
                    ModelsStoreEntry(
                        models=list(refreshed),
                        checked_at=int(time.time() * 1000),
                    )
                )

        if self._inflight_refresh is None or self._inflight_refresh.done():
            self._inflight_refresh = asyncio.create_task(_run())
        await self._inflight_refresh


def create_provider(
    id: str,
    name: str,
    base_url: Optional[str] = None,
    headers: Optional[ProviderHeaders] = None,
    models: Optional[List[Model]] = None,
    api: Optional[ApiImpl] = None,
    auth: Optional[ProviderAuth] = None,
    fetch_models: Optional[
        Callable[[RefreshModelsContext], Awaitable[List[Model]]]
    ] = None,
    filter_models: Optional[
        Callable[[List[Model], Optional[Credential]], List[Model]]
    ] = None,
) -> Provider:
    """构造 Provider 实例（对齐 TS ``createProvider``）。

    Args:
        id: provider 唯一标识
        name: 展示名称
        base_url: API 基础 URL
        headers: 默认请求头
        models: 静态模型列表（baseline）
        api: API 实现（单个实现或按 ``model.api`` 分发的字典）
        auth: provider 鉴权配置
        fetch_models: 动态模型拉取回调；调用后 ``refresh_models`` 会把结果与
            ``models`` 合并并持久化到 ``RefreshModelsContext.store``
        filter_models: 凭据级模型过滤策略

    Returns:
        可独立调度的 Provider 实例
    """
    cls = _DynamicProvider if fetch_models is not None else Provider
    kwargs: Dict[str, Any] = {
        "id": id,
        "name": name,
        "base_url": base_url,
        "headers": headers,
        "models": list(models) if models is not None else [],
        "api_impl": api,
        "auth": auth,
        "filter_models": filter_models,
    }
    if fetch_models is not None:
        kwargs["fetch_models"] = fetch_models
    return cls(**kwargs)


__all__ = [
    "ApiImpl",
    "Provider",
    "ProviderStreams",
    "RefreshModelsContext",
    "create_provider",
]
