"""Provider 运行时单元（对齐 TS ``src/models.ts`` 的 ``createProvider`` 部分）。

Provider 是独立的运行时单元：持有 auth 配置、模型目录与 stream 调度能力
（``model.api`` → 协议实现的路由），不做 auth 解析（那是 ``ModelsManager``
的职责）。

能力即字段（对齐 TS ``refreshModels`` 的函数口袋形态）：``refresh_models``
为 ``None`` 是静态 provider（目录仅基线）；注入策略闭包即为动态 provider——
默认策略（``fetch_models`` 回调，三件套：恢复 → 拉取 → 持久化）由
``create_provider`` 装配；远程目录六件套治理皮归 harness 层组合时包装。
``ModelsManager.refresh`` 只收集非 None 者。

``catalog_source`` 是目录站选配 key（对位 ``Model.api``）：manager 持有站
容器（``CatalogServices``，对位 ``ApiImpl`` 的单站/dict 双形态）时按它路由，
经 ``partial(service.fetch, provider.id)`` 绑定后发放进
``RefreshModelsContext.catalog_fetch``——refresh 实现的可选能力。

纪律（TS 闭包隐藏纪律在 Python 的约定实现）：

- 模型列表的读取只经 ``get_models()``（基线 ∪ 动态的合并视图）——直读
  ``_baseline_models`` 会漏掉动态 overlay；
- 动态集合的写入只经刷新策略内的 ``context.publish()``（世代校验闸）；
- ``_baseline_models`` / ``_dynamic_models`` / ``_api_impl`` 均为实现细节。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Protocol, Union

from nova_catalog.define import ProviderDefinition
from nova_protocol import (
    AbortSignal,
    Context,
    Credential,
    KnownApi,
    Model,
    ModelsStoreEntry,
    ProviderAuth,
    ProviderHeaders,
    is_aborted,
)

from ..auth.resolve import ModelsError
from ..stream_options import SimpleStreamOptions, StreamOptions
from ..streaming import AssistantMessageEventStream
from .catalog import FetchCatalogFn
from .diagnostics import DiagnosticsCollector
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
    """世代校验的发布口（持久化 + 内存更新经 ModelsManager 串行化）。"""
    allow_network: bool = True
    """False 表示离线/仅缓存阶段。"""
    force: bool = False
    """绕过 provider 侧新鲜度检查立即拉取（仅网络阶段有意义）。"""
    signal: Optional[AbortSignal] = None
    """阻塞工作的共享中断信号。"""
    catalog_fetch: Optional[FetchCatalogFn] = None
    """manager 按 ``catalog_source`` 路由并绑定 provider id 后发放的公开站
    拉取函数——refresh 的可选能力（用不用归 refresh 实现）；None 表示
    无站或未选配（恢复照常，网络阶段由实现方自行收场）。"""
    diagnostics: DiagnosticsCollector = field(default_factory=DiagnosticsCollector)
    """本次刷新的诊断收集器（**warnings as data** 双通道——refresh 实现方
    ``append``（如无站降级、订阅源单独供数）：``items`` 无条件收集进
    refresh 结果的 ``diagnostics`` 键；槽位 ``on_diagnostic`` 由 manager
    装配（组合根注入）时 append 即实时外发。nova_ai 是库不发射事件——
    呈现（WarningEvent 转换）归上层组合根。"""


RefreshModelsFn = Callable[[RefreshModelsContext], Awaitable[None]]
"""刷新策略签名（``Provider.refresh_models`` 能力字段的类型）。"""


def merge_models(baseline: List[Model], dynamic: List[Model]) -> List[Model]:
    """基线 ∪ overlay：同 id overlay 胜，新 id 追加（对齐 TS mergeModels）。

    全包唯一的目录合并点（``Provider.get_models`` 与远程目录恢复共用）。
    """
    merged = list(baseline)
    for model in dynamic:
        index = next((i for i, m in enumerate(merged) if m.id == model.id), -1)
        if index >= 0:
            merged[index] = model
        else:
            merged.append(model)
    return merged


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
    auth: Optional[ProviderAuth] = None
    filter_models: Optional[
        Callable[[List[Model], Optional[Credential]], List[Model]]
    ] = None
    catalog_source: Optional[str] = None
    """目录站选配 key（对位 ``Model.api``）：manager 持有站容器为 dict 形态时
    按本 key 路由；为 None 表示本 provider 不选配公开站。"""
    catalog_knowledge: Optional[ProviderDefinition] = None
    """目录修正知识（**知识唯一定义点在 nova_catalog**——工厂装配时挂
    nova_catalog 的对应 DEF（``ProviderDefinition``，引擎直读字段）——
    无注册表、无查找）。None = 无修正（pi.dev 已覆盖或 models.json
    声明式 provider 的常态）。"""
    _baseline_models: List[Model] = field(default_factory=list)
    _dynamic_models: List[Model] = field(default_factory=list)
    _api_impl: Optional[ApiImpl] = None
    # 能力字段（非构造面）：None = 静态 provider；策略闭包由 create_provider
    # （fetch_models 默认策略）/ with_remote_catalog（远程目录治理）注入。
    # init=False：子类以同名方法覆写兼容（TS 的"方法在不在"探测在 Python
    # 落到"字段是不是 None"）。
    refresh_models: Optional[RefreshModelsFn] = field(default=None, init=False)

    def get_models(self) -> List[Model]:
        """返回该 provider 的模型列表（基线 ∪ 动态，对齐 TS Provider.getModels）。

        模型列表的唯一读取口——merge 逻辑只活在这里，直读
        ``_baseline_models`` 会漏掉动态 overlay。
        """
        return merge_models(self._baseline_models, self._dynamic_models)

    def get_model(self, model_id: str) -> Optional[Model]:
        """按 model id 查找模型。"""
        for model in self.get_models():
            if model.id == model_id:
                return model
        return None

    def _api_for(self, model: Model) -> Optional[ProviderStreams]:
        """根据 model.api 找到对应的 API 实现。"""
        if self._api_impl is None:
            return None
        if isinstance(self._api_impl, dict):
            api_key = (
                model.api.value if isinstance(model.api, KnownApi) else str(model.api)
            )
            return self._api_impl.get(api_key)
        return self._api_impl

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


def _default_refresh_models(
    provider: Provider,
    fetch_models: Callable[[RefreshModelsContext], Awaitable[Optional[List[Model]]]],
) -> RefreshModelsFn:
    """默认刷新策略：``fetch_models`` 回调的两阶段实现（对齐 TS createProvider
    内的 refreshModels——恢复缓存 → 拉取 → 经 publish 世代校验持久化）。

    回调返回 ``None`` = **本次无发布**（无站降级等）——内存与落盘保持现状
    （离线恢复阶段读出的缓存继续服役）；与"拉到空列表"严格区分：空列表
    照常发布（清空 overlay 是真实语义）。

    闭包捕获 provider 实例与拉取回调（provider → 策略 → provider 引用环，
    Python GC 收环无压力；策略与属主同生共死）。
    """

    async def _refresh_models(context: RefreshModelsContext) -> None:
        """两阶段刷新（离线恢复 + 网络拉取），持久化策略归本 provider，
        但发布必须经上下文（世代校验在 ModelsManager 侧）。"""
        publish = context.publish

        async def _publish(publication: ModelsPublication) -> bool:
            if publish is None:
                return False
            return await publish(publication)

        if context.stored is not None:
            # 离线恢复尽力而为：publish 缺席/失败都不阻断后续网络阶段
            restored = [m for m in context.stored.models if m.provider == provider.id]
            await _publish(
                ModelsPublication(
                    update=lambda restored=restored: setattr(
                        provider, "_dynamic_models", restored
                    )
                )
            )
        if not context.allow_network:
            return
        if is_aborted(context.signal):
            return
        refreshed = await fetch_models(context)
        if refreshed is None:
            # 本次无发布（无站降级等）：内存与落盘缓存都保持现状
            return
        if is_aborted(context.signal):
            return
        await _publish(
            ModelsPublication(
                persist=ModelsStoreEntry(
                    models=list(refreshed), checked_at=int(time.time() * 1000)
                ),
                update=lambda refreshed=refreshed: setattr(
                    provider, "_dynamic_models", list(refreshed)
                ),
            )
        )

    return _refresh_models


def create_provider(
    id: str,
    name: str,
    base_url: Optional[str] = None,
    headers: Optional[ProviderHeaders] = None,
    models: Optional[List[Model]] = None,
    api: Optional[ApiImpl] = None,
    auth: Optional[ProviderAuth] = None,
    fetch_models: Optional[
        Callable[[RefreshModelsContext], Awaitable[Optional[List[Model]]]]
    ] = None,
    filter_models: Optional[
        Callable[[List[Model], Optional[Credential]], List[Model]]
    ] = None,
    catalog_source: Optional[str] = None,
    catalog_knowledge: Optional[ProviderDefinition] = None,
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
        fetch_models: 动态模型拉取回调；传入即为动态 provider——默认刷新
            策略会先从存储恢复缓存目录，再拉取新目录并经 ``publish``
            世代校验地持久化。回调返回 ``None`` = 本次无发布（无站降级
            等），内存与落盘缓存保持现状
        filter_models: 凭据级模型过滤策略
        catalog_source: 目录站选配 key（manager 站容器为 dict 形态时的
            路由键）；None 表示不选配公开站
        catalog_knowledge: 目录修正知识（nova_catalog 的 DEF，
            ``ProviderDefinition``）；None = 无修正

    Returns:
        可独立调度的 Provider 实例
    """
    provider = Provider(
        id=id,
        name=name,
        base_url=base_url,
        headers=headers,
        auth=auth,
        filter_models=filter_models,
        catalog_source=catalog_source,
        catalog_knowledge=catalog_knowledge,
        _baseline_models=list(models) if models is not None else [],
        _api_impl=api,
    )
    if fetch_models is not None:

        async def _fetch(context: RefreshModelsContext) -> Optional[List[Model]]:
            result = await fetch_models(context)
            return None if result is None else list(result)

        provider.refresh_models = _default_refresh_models(provider, _fetch)
    return provider


__all__ = [
    "ApiImpl",
    "ModelsPublication",
    "Provider",
    "ProviderStreams",
    "RefreshModelsContext",
    "RefreshModelsFn",
    "UNSET",
    "create_provider",
    "merge_models",
]
