"""Provider 运行时单元（对齐 TS ``src/models.ts`` 的 ``createProvider`` 部分）。

Provider 是独立的运行时单元：持有 auth 配置、模型目录与 stream 调度能力
（``model.api`` → 协议实现的路由），不做 auth 解析（那是 ``Models`` 的职责）。

发布纪律（对齐 TS ``ModelsPublication``）：动态模型目录的持久化策略归
provider 所有，但落盘必须经 ``context.publish()`` ——由 ``Models`` 做
世代校验后串行发布，防止被 supersede 的旧刷新覆盖新目录。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Union

from ..auth.resolve import ModelsError
from ..signal import AbortSignal
from ..streaming import AssistantMessageEventStream
from ..types.aliases import ProviderHeaders
from ..types.auth import Credential, ProviderAuth
from ..types.enums import KnownApi
from ..types.messages import Context
from ..types.model import Model
from ..types.stream_options import SimpleStreamOptions, StreamOptions
from .store import ModelsStoreEntry
from .streams import lazy_stream


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


class _UnsetType:
    """``ModelsPublication.persist`` 的"未提及"哨兵（三态：未提及 / 删除 / 条目）。"""

    def __repr__(self) -> str:  # pragma: no cover
        return "UNSET"


UNSET = _UnsetType()


@dataclass
class ModelsPublication:
    """provider 一次发布的内容（对齐 TS ModelsPublication）。

    - ``persist``：provider 选定的持久化目录。缺省（``UNSET``）不动存储；
      显式 ``None`` 删除存储条目；``ModelsStoreEntry`` 则写入。
    - ``update``：持久化成功后同步执行的 provider 私有内存目录更新。
    """

    persist: Union[ModelsStoreEntry, None, _UnsetType] = UNSET
    update: Optional[Callable[[], None]] = None


PublishFn = Callable[[ModelsPublication], Awaitable[bool]]


@dataclass
class RefreshModelsContext:
    """``refresh_models`` 调用上下文（对齐 TS RefreshModelsContext）。

    两阶段刷新共用本上下文：离线阶段 ``allow_network=False``（仅从
    ``stored`` 恢复缓存）；网络阶段凭 ``credential`` 拉新目录。
    """

    credential: Optional[Credential] = None
    """本次刷新生效的凭据（OAuth 已提前刷新）。"""
    stored: Optional[ModelsStoreEntry] = None
    """刷新开始前捕获的持久化目录只读快照（深拷贝，可安全持有）。"""
    publish: Optional[PublishFn] = None
    """世代校验的发布口（持久化 + 内存更新经 Models 串行化）。"""
    allow_network: bool = True
    """False 表示离线/仅缓存阶段。"""
    force: bool = False
    """绕过 provider 侧新鲜度检查立即拉取（仅网络阶段有意义）。"""
    signal: Optional[AbortSignal] = None
    """阻塞工作的共享中断信号。"""


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

    def _dispatch(
        self,
        model: Model,
        run: Callable[[ProviderStreams], AssistantMessageEventStream],
    ) -> AssistantMessageEventStream:
        """按 model.api 派发；缺实现时以 error 流收尾（对齐 TS dispatch）。

        ``StreamFunction`` 契约：调用后的失败一律编码进流，而不是抛出。
        """
        impl = self._api_for(model)
        if impl is None:

            async def _missing() -> AssistantMessageEventStream:
                raise ModelsError(
                    "stream",
                    f'Provider {self.id} has no API implementation for "{model.api}"',
                )

            return lazy_stream(model, _missing)
        return run(impl)

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """使用本 provider 绑定的 API 实现发起流式调用。"""
        return self._dispatch(model, lambda api: api.stream(model, context, options))

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """使用本 provider 绑定的 API 实现发起简化流式调用。"""
        return self._dispatch(
            model, lambda api: api.stream_simple(model, context, options)
        )


class _DynamicProvider(Provider):
    """支持动态模型合并与刷新的 Provider（对齐 TS createProvider 的 refreshModels）。"""

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
        """两阶段刷新（对齐 TS createProvider 内的 refreshModels 实现）。

        离线阶段从 ``context.stored`` 恢复缓存目录并发布；网络阶段拉新目录、
        经 ``context.publish`` 一并持久化。持久化策略归本 provider，但发布
        必须经上下文（世代校验在 Models 侧）。
        """
        if self._fetch_models is None:
            return
        publish = context.publish

        async def _publish(publication: ModelsPublication) -> bool:
            if publish is None:
                return False
            return await publish(publication)

        if context.stored is not None:
            # 离线恢复尽力而为：publish 缺席/失败都不阻断后续网络阶段
            # （旧实现此处误加 early-return，会跳过网络刷新）
            restored = [m for m in context.stored.models if m.provider == self.id]
            await _publish(
                ModelsPublication(
                    update=lambda restored=restored: setattr(
                        self, "_dynamic_models", restored
                    )
                )
            )
        if not context.allow_network:
            return
        if context.signal is not None and context.signal.aborted:
            return
        refreshed = await self._fetch_models(context)
        if context.signal is not None and context.signal.aborted:
            return
        await _publish(
            ModelsPublication(
                persist=ModelsStoreEntry(
                    models=list(refreshed), checked_at=int(time.time() * 1000)
                ),
                update=lambda refreshed=refreshed: setattr(
                    self, "_dynamic_models", list(refreshed)
                ),
            )
        )


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
        fetch_models: 动态模型拉取回调；``refresh_models`` 会先从存储恢复
            缓存目录，再拉取新目录并经 ``publish`` 世代校验地持久化
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
    "ModelsPublication",
    "Provider",
    "ProviderStreams",
    "RefreshModelsContext",
    "UNSET",
    "create_provider",
]
