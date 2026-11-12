"""Provider 运行时单元（对齐 TS ``src/models.ts`` 的 ``createProvider`` 部分）。

Provider 是独立的运行时单元：持有 auth 配置、chat/image/classifier 混合目录与
stream/generate_images/classify 调度能力（``model.api`` → 协议实现的路由），不做
auth 解析（那是 ``ModelsManager`` 的职责）。

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
from typing import Awaitable, Callable, Dict, List, Optional, Protocol, Sequence, Union

from nova_protocol import (
    AbortSignal,
    AnyModel,
    AssistantImages,
    ClassifierContext,
    ClassifierModel,
    ClassifierResult,
    Context,
    Credential,
    ImageModel,
    ImagesContext,
    Model,
    ModelsStoreEntry,
    ModelType,
    ProviderAuth,
    ProviderHeaders,
)

from ..auth.resolve import ModelsError
from ..stream_options import (
    ClassifierOptions,
    ImagesOptions,
    SimpleStreamOptions,
    StreamOptions,
)
from ..streaming import AssistantMessageEventStream
from ..utils.model_operations import api_value, get_model_type
from .catalog import FetchCatalogFn
from .diagnostics import DiagnosticsCollector
from .streams import lazy_stream


class ProviderStreams(Protocol):
    """聊天 API 实现模块的统一契约（stream / stream_simple）。"""

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


class ProviderImages(Protocol):
    """图像 API 实现模块的统一契约（非流式 generate_images）。"""

    async def generate_images(
        self,
        model: ImageModel,
        context: ImagesContext,
        options: Optional[ImagesOptions] = None,
    ) -> AssistantImages: ...


class ProviderClassifier(Protocol):
    """分类 API 实现模块的统一契约（非流式 classify）。"""

    async def classify(
        self,
        model: ClassifierModel,
        context: ClassifierContext,
        options: Optional[ClassifierOptions] = None,
    ) -> ClassifierResult: ...


# api_impl 可以是单个实现，也可以按 model.api 分发（对齐 TS Provider.api）
ApiImpl = Union[ProviderStreams, Dict[str, ProviderStreams]]
ImagesImpl = Dict[str, ProviderImages]
ClassifiersImpl = Dict[str, ProviderClassifier]


class GenerateImagesFn(Protocol):
    """Provider 图像能力回调；options 可像真实闭包一样省略。"""

    def __call__(
        self,
        model: ImageModel,
        context: ImagesContext,
        options: Optional[ImagesOptions] = None,
    ) -> Awaitable[AssistantImages]: ...


class ClassifyFn(Protocol):
    """Provider 分类能力回调；options 可像真实闭包一样省略。"""

    def __call__(
        self,
        model: ClassifierModel,
        context: ClassifierContext,
        options: Optional[ClassifierOptions] = None,
    ) -> Awaitable[ClassifierResult]: ...


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

PersonalizeFn = Callable[
    [Sequence[AnyModel], RefreshModelsContext], Awaitable[Sequence[AnyModel]]
]
"""账户层钩子签名：混合目录在基底备好、发布之前按凭据个性化。"""


def assert_chat_model(model: AnyModel) -> Model:
    """运行时收窄聊天模型；图像/其他操作不得进入 ProviderStreams。"""
    if not isinstance(model, Model):
        raise ModelsError(
            "provider", f"Model {model.provider}/{model.id} is not a chat model"
        )
    return model


def merge_models(baseline: List[AnyModel], dynamic: List[AnyModel]) -> List[AnyModel]:
    """基线 ∪ overlay：同 ``(type,id)`` overlay 胜，新条目追加。

    类型参与键是因为同一 id 可以分别支持聊天与图像生成（pi mergeModels）。
    """
    merged = list(baseline)
    for model in dynamic:
        index = next(
            (
                i
                for i, entry in enumerate(merged)
                if entry.id == model.id
                and get_model_type(entry) == get_model_type(model)
            ),
            -1,
        )
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
    _baseline_models: List[AnyModel] = field(default_factory=list)
    _dynamic_models: List[AnyModel] = field(default_factory=list)
    _dynamic_published: bool = False
    _api_impl: Optional[ApiImpl] = None
    _images_impl: Optional[ImagesImpl] = None
    _classifiers_impl: Optional[ClassifiersImpl] = None
    _dynamic_replaces_baseline: bool = False
    """目录读取模式：True 且已发布动态目录时整取代基线；False = merge。"""
    # 能力字段（非构造面）：策略与一次性操作分派闭包均由 create_provider 装配。
    refresh_models: Optional[RefreshModelsFn] = field(default=None, init=False)
    generate_images: Optional[GenerateImagesFn] = field(default=None, init=False)
    classify: Optional[ClassifyFn] = field(default=None, init=False)

    def get_all_models(self) -> List[AnyModel]:
        """返回全类型目录；replace 以“发布过”而非列表长度判定。"""
        if self._dynamic_replaces_baseline and self._dynamic_published:
            return list(self._dynamic_models)
        return merge_models(self._baseline_models, self._dynamic_models)

    def get_models(self) -> List[Model]:
        """既有读取面保持 chat-only；图像/分类条目经 get_all/类型接口读取。"""
        return [model for model in self.get_all_models() if isinstance(model, Model)]

    def _set_dynamic_models(self, models: List[AnyModel]) -> None:
        """发布动态目录（含合法空集）；恢复无缓存时不得调用本方法。"""
        self._dynamic_models = list(models)
        self._dynamic_published = True

    def get_model(self, model_id: str) -> Optional[Model]:
        """按 model id 查找聊天模型。"""
        for model in self.get_models():
            if model.id == model_id:
                return model
        return None

    def get_model_of_type(
        self, model_type: ModelType, model_id: str
    ) -> Optional[AnyModel]:
        """按操作类型 + id 查找目录条目。"""
        return next(
            (
                model
                for model in self.get_all_models()
                if model.id == model_id and get_model_type(model) == model_type
            ),
            None,
        )

    def _api_for(self, model: Model) -> Optional[ProviderStreams]:
        """根据 model.api 找到对应的 API 实现。"""
        if self._api_impl is None:
            return None
        if isinstance(self._api_impl, dict):
            return self._api_impl.get(api_value(model.api))
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
        try:
            assert_chat_model(model)
            impl = self._api_for(model)
            if impl is None:

                async def _missing() -> AssistantMessageEventStream:
                    raise ModelsError(
                        "stream",
                        f'Provider {self.id} has no API implementation for "{model.api}"',
                    )

                return lazy_stream(model, _missing, signal=signal)
            return run(impl)
        except Exception as error:
            # 类型守卫与实现装配期的同步失败都经 lazy_stream 终止。
            failure = error

            async def _failed() -> AssistantMessageEventStream:
                raise failure

            return lazy_stream(model, _failed, signal=signal)

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


__all__ = [
    "ApiImpl",
    "ClassifyFn",
    "ClassifiersImpl",
    "GenerateImagesFn",
    "ImagesImpl",
    "ModelsPublication",
    "PersonalizeFn",
    "Provider",
    "ProviderClassifier",
    "ProviderImages",
    "ProviderStreams",
    "RefreshModelsContext",
    "RefreshModelsFn",
    "UNSET",
    "merge_models",
]
