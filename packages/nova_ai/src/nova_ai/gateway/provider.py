"""Provider 运行时单元（对齐 TS ``src/models.ts`` 的 ``createProvider`` 部分）。

Provider 是独立的运行时单元：持有 auth 配置、模型目录与 stream 调度能力
（``model.api`` → 协议实现的路由），不做 auth 解析（那是 ``ModelsManager``
的职责）。

能力即字段（对齐 TS ``refreshModels`` 的函数口袋形态）：``refresh_models``
为 ``None`` 是静态 provider（目录仅基线）；注入策略闭包即为动态 provider。
刷新治理只有一份（``gateway/strategies.py`` 的策略工厂，两形态——overlay
纯站 / personalized 站+账户钩子），``create_provider`` 按形态装配：
``personalize`` 传入 → personalized 策略 + replace 读取位；
仅 ``catalog_source`` → overlay 策略出生自带；皆无 → 静态（要别的形态
自己实现 ``refresh_models`` 裸口袋，pi 同款）。``ModelsManager.refresh``
只收集非 None 者。

``catalog_source`` 是目录站选配 key（对位 ``Model.api``）：manager 持有站
容器（``CatalogServices``，对位 ``ApiImpl`` 的单站/dict 双形态）时按它路由，
经 ``partial(service.fetch, provider.id)`` 绑定后发放进
``RefreshModelsContext.catalog_fetch``——refresh 实现的可选能力。

纪律（TS 闭包隐藏纪律在 Python 的约定实现）：

- 模型列表的读取只经 ``get_models()``（merge/replace 两形态）——直读
  ``_baseline_models`` 会漏掉动态 overlay；
- 动态集合的写入只经刷新策略内的 ``context.publish()``（世代校验闸）；
- ``_baseline_models`` / ``_dynamic_models`` / ``_api_impl`` /
  ``_dynamic_replaces_baseline`` 均为实现细节。
"""

from __future__ import annotations

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
    base_url: Optional[str] = None
    """当前注册 provider 的生效 API 基址；私有目录源必须使用同一端点身份。"""
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
    ``append``（如无站降级、订阅源不可达）：``items`` 无条件收集进
    refresh 结果的 ``diagnostics`` 键；槽位 ``on_diagnostic`` 由 manager
    装配（组合根注入）时 append 即实时外发。nova_ai 是库不发射事件——
    呈现（WarningEvent 转换）归上层组合根。"""


RefreshModelsFn = Callable[[RefreshModelsContext], Awaitable[None]]
"""刷新策略签名（``Provider.refresh_models`` 能力字段的类型）。"""

PersonalizeFn = Callable[[List[Model], RefreshModelsContext], Awaitable[List[Model]]]
"""账户层钩子签名（pi ``PersonalizeCatalog`` 对位）：已修正基底 + 上下文
→ 个性化目录。``create_provider(personalize=...)`` 装配进 personalized
刷新策略，在"基底备好、发布之前"被调用——kimi 订阅覆盖 / 火山 ark 筛选。"""


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
    _dynamic_published: bool = False
    _api_impl: Optional[ApiImpl] = None
    _dynamic_replaces_baseline: bool = False
    """目录读取模式（装配置位——personalized 形态专属，pi
    ``withPersonalizedCatalog`` 对位）：True 且动态目录非空时
    ``get_models()`` 整取代基线（账户个性化目录是"全部真相"）；
    False = merge（基线 ∪ 动态——公共事实的增量覆盖）。"""
    # 能力字段（非构造面）：None = 静态 provider；策略闭包由 create_provider
    # 装配（personalize → personalized 策略；catalog_source → overlay 策略）。
    # init=False：子类以同名方法覆写兼容（TS 的"方法在不在"探测在 Python
    # 落到"字段是不是 None"）。
    refresh_models: Optional[RefreshModelsFn] = field(default=None, init=False)

    def get_models(self) -> List[Model]:
        """返回该 provider 的模型列表（对齐 TS Provider.getModels）。

        模型列表的唯一读取口。replace 形态以“是否发布过动态目录”为状态，
        不以列表长度代替状态：合法空目录也必须整取代基线。
        """
        if self._dynamic_replaces_baseline and self._dynamic_published:
            return list(self._dynamic_models)
        return merge_models(self._baseline_models, self._dynamic_models)

    def _set_dynamic_models(self, models: List[Model]) -> None:
        """发布动态目录（含合法空集）；恢复无缓存时不得调用本方法。"""
        self._dynamic_models = list(models)
        self._dynamic_published = True

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
        signal: Optional[AbortSignal] = None,
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

            return lazy_stream(model, _missing, signal=signal)
        return run(impl)

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """使用本 provider 绑定的 API 实现发起流式调用。"""
        return self._dispatch(
            model,
            lambda api: api.stream(model, context, options),
            signal=options.signal if options else None,
        )

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """使用本 provider 绑定的 API 实现发起简化流式调用。"""
        return self._dispatch(
            model,
            lambda api: api.stream_simple(model, context, options),
            signal=options.signal if options else None,
        )


def create_provider(
    id: str,
    name: str,
    base_url: Optional[str] = None,
    headers: Optional[ProviderHeaders] = None,
    models: Optional[List[Model]] = None,
    api: Optional[ApiImpl] = None,
    auth: Optional[ProviderAuth] = None,
    filter_models: Optional[
        Callable[[List[Model], Optional[Credential]], List[Model]]
    ] = None,
    catalog_source: Optional[str] = None,
    catalog_knowledge: Optional[ProviderDefinition] = None,
    personalize: Optional[PersonalizeFn] = None,
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
        filter_models: 凭据级模型过滤策略
        catalog_source: 目录站选配 key（manager 站容器为 dict 形态时的
            路由键）；None 表示不选配公开站
        catalog_knowledge: 目录修正知识（nova_catalog 的 DEF，
            ``ProviderDefinition``）；None = 无修正
        personalize: 账户层钩子（pi ``PersonalizeCatalog`` 对位）——传入即
            装配 personalized 刷新策略（站 + 钩子、replace 读取形态）；
            None 且配了 ``catalog_source`` 时装配 overlay 策略（纯站）

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
    if personalize is not None:
        # 账户个性化形态（kimi 订阅覆盖 / 火山 ark 筛选）：replace 读取
        from .strategies import personalized_refresh_policy

        provider.refresh_models = personalized_refresh_policy(provider, personalize)
        provider._dynamic_replaces_baseline = True
    elif catalog_source is not None:
        # 纯站形态（公共事实目录）：六件套治理出生自带
        from .strategies import overlay_refresh_policy

        provider.refresh_models = overlay_refresh_policy(provider)
    return provider


__all__ = [
    "ApiImpl",
    "ModelsPublication",
    "PersonalizeFn",
    "Provider",
    "ProviderStreams",
    "RefreshModelsContext",
    "RefreshModelsFn",
    "UNSET",
    "create_provider",
    "merge_models",
]
