"""ModelsManager 网关（对齐 TS ``src/models.ts`` 的 ``ModelsImpl``）。

Provider 运行时集合 + auth 应用 + stream/generate_images 便捷入口。
本模块的并发与结果纪律：

- **世代守卫**：每个 provider 的刷新带递增世代；``set/delete/clear_provider``
  会 supersede 在飞刷新（bump 世代 + abort controller），旧刷新的发布在
  ``publish`` 处被世代校验拦下，永不覆盖新目录；
- **两阶段刷新**：先离线恢复缓存（``allow_network=False``），再解析凭据
  走网络阶段——离线启动也能拿到上次的目录；
- **signal 贯穿**：所有公开异步入口接受可选 ``AbortSignal``，
  经 :func:`nova_async.await_with_abort` 真杀；
- **stream 契约**：一切可失败步骤（provider 查找、auth 解析）都在
  :func:`nova_ai.gateway.streams.lazy_stream` 的 setup 闭包内进行，
  失败编码为 error 事件，绝不同步抛出。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import replace as _dataclass_replace
from functools import partial
from typing import (
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypedDict,
    TypeVar,
    Union,
    cast,
)

from nova_async import (
    Deadline,
    KeyedLockFactory,
    any_signal,
    await_with_abort,
    default_keyed_locks,
    operation_signal,
)
from nova_protocol import (
    AbortController,
    AbortedError,
    AbortSignal,
    AnyModel,
    ApiKeyCredential,
    AssistantImages,
    AssistantMessage,
    AuthCheck,
    AuthContext,
    AuthInteraction,
    AuthResult,
    AuthType,
    ClassifierContext,
    ClassifierModel,
    ClassifierResult,
    Context,
    Credential,
    CredentialStore,
    ImageModel,
    ImagesContext,
    Model,
    ModelBase,
    ModelsStoreEntry,
    ModelType,
    NovaDiagnostic,
    NovaErrorKind,
    ProviderEnv,
    ProviderHeaders,
    RedactedString,
    is_aborted,
)

from ..auth.context import default_provider_auth_context
from ..auth.credential_store import InMemoryCredentialStore
from ..auth.resolve import (
    AuthResolutionOverrides,
    ModelsError,
    refresh_stored_oauth_credential,
    resolve_provider_auth,
)
from ..stream_options import (
    ClassifierOptions,
    ImagesOptions,
    SimpleStreamOptions,
    StreamOptions,
)
from ..streaming import AssistantMessageEventStream
from ..utils.errors import RequestTimeoutError
from ..utils.model_operations import (
    assert_classifier_input_supported,
    assert_classifier_model,
    classifier_error_result,
    image_error_result,
)
from .catalog import CatalogService, CatalogServices
from .diagnostics import DiagnosticsCollector
from .provider import (
    ModelsPublication,
    Provider,
    RefreshModelsContext,
    assert_chat_model,
)
from .store import InMemoryModelsStore, ModelsStore
from .streams import lazy_stream

#: refresh 整轮预算缺省（毫秒）——防多阶段（缓存恢复/凭据解析/网络拉取）
#: 各自为政、累积成无穷等待（策略件留消费方；机制件 Deadline 住 nova_async）
DEFAULT_REFRESH_DEADLINE_MS = 90_000

_ModelT = TypeVar("_ModelT", bound=ModelBase)


class ModelsRefreshResult(TypedDict):
    """``ModelsManager.refresh`` 的汇总结果（规则 10 声明形状）。

    - ``aborted``：整个刷新是否因 signal 中断而提前收场；
    - ``errors``：按 provider id 归集的刷新错误（中断不计入）；
    - ``diagnostics``：按 provider id 归集的非致命诊断（warnings as
      data——无此项即该 provider 无诊断；呈现归上层，见
      ``docs/crosscutting/diagnostics-modeling.md``）。
    """

    aborted: bool
    errors: Dict[str, Exception]
    diagnostics: Dict[str, List[NovaDiagnostic]]


class _ApplyAuthPatch(TypedDict, total=False):
    """``_apply_auth`` 注入请求选项的补丁键集（规则 10 声明——``dataclasses.
    replace`` 的 ``**kwargs`` 不做键名校验，形状在这里立住）。

    - ``transform_headers``：恒置 None——ModelsManager 层的 transform 已在
      ``_apply_auth`` 内运行完，不派发给 provider；
    - ``api_key`` / ``headers`` / ``env``：auth 解析结果，非 None 才进补丁。
    """

    transform_headers: None
    api_key: str
    headers: ProviderHeaders
    env: ProviderEnv


# per-model headers 请求时解析钩子的签名：(model, env) → headers（可异步返回）
ModelHeadersResolver = Callable[
    [ModelBase, Optional[ProviderEnv]],
    Union[Optional[ProviderHeaders], Awaitable[Optional[ProviderHeaders]]],
]


def _merge_headers(
    base: Optional[Mapping[str, Optional[str]]],
    override: Optional[Mapping[str, Optional[str]]],
) -> Optional[ProviderHeaders]:
    """大小写不敏感地合并 headers（对齐 TS mergeHeaders）。"""
    if not base and not override:
        return None
    merged: ProviderHeaders = dict(base or {})
    for name, value in (override or {}).items():
        lower_name = name.lower()
        for existing_name in list(merged.keys()):
            if existing_name.lower() == lower_name:
                del merged[existing_name]
        merged[name] = value
    return merged


class ModelsManager:
    """Provider 运行时集合（并发语义见模块 docstring）。"""

    def __init__(
        self,
        credential_store: Optional[CredentialStore] = None,
        models_store: Optional[ModelsStore] = None,
        auth_context: Optional[AuthContext] = None,
        model_headers_resolver: Optional[ModelHeadersResolver] = None,
        catalog_services: Optional[CatalogServices] = None,
        refresh_locks: KeyedLockFactory = default_keyed_locks,
        on_diagnostic: Optional[Callable[[NovaDiagnostic], None]] = None,
    ) -> None:
        self._providers: Dict[str, Provider] = {}
        self._credential_store = credential_store or InMemoryCredentialStore()
        self._models_store = models_store or InMemoryModelsStore()
        self._auth_context = auth_context or default_provider_auth_context()
        # per-provider 刷新锁工厂（编排去重——refresh/logout/login 串行）；
        # 缺省进程内锁，多进程部署由 harness 注入文件锁
        self._refresh_locks = refresh_locks
        # 可选的 per-model headers 请求时解析钩子：
        # (model, env) -> Optional[ProviderHeaders]，由上层（如 harness 的
        # models.json 配置）注入；nova_ai 自身不关心模板语法。
        self._model_headers_resolver = model_headers_resolver
        # 目录站容器（可选）：单站全员共用 / dict 按 provider.catalog_source
        # 路由（对位 Provider._api_impl 的双形态）。内容物由组合根注入；
        # None = 无站，ctx.catalog_fetch 一律为 None。
        self._catalog_services = catalog_services
        # 诊断实时缝（可选）：组合根注入，refresh 建 context 时转发进
        # 收集器的 on_diagnostic 槽——append 即外发（双通道的另一半）
        self._on_diagnostic = on_diagnostic
        # 刷新世代机制：世代号 / 在飞 controller / 每 provider 发布链
        self._refresh_generations: Dict[str, int] = {}
        self._refresh_controllers: Dict[str, AbortController] = {}
        self._publication_chains: Dict[str, asyncio.Future] = {}

    # -----------------------------------------------------------------------
    # Provider 注册（supersede 在飞刷新）
    # -----------------------------------------------------------------------

    def set_provider(self, provider: Provider) -> None:
        """按 ``provider.id`` 注册或替换 provider（supersede 其在飞刷新）。"""
        self._supersede_provider_refresh(provider.id)
        self._providers[provider.id] = provider

    def delete_provider(self, provider_id: str) -> None:
        """删除指定 provider（supersede 其在飞刷新）。"""
        self._supersede_provider_refresh(provider_id)
        self._providers.pop(provider_id, None)

    def clear_providers(self) -> None:
        """清空所有 provider（逐个 supersede 在飞刷新）。"""
        for provider_id in set(self._providers) | set(self._refresh_controllers):
            self._supersede_provider_refresh(provider_id)
        self._providers.clear()

    def get_providers(self) -> List[Provider]:
        """返回所有已注册 provider。"""
        return list(self._providers.values())

    def get_provider(self, provider_id: str) -> Optional[Provider]:
        """按 id 查找 provider。"""
        return self._providers.get(provider_id)

    def get_models(self, provider_id: Optional[str] = None) -> List[Model]:
        """返回指定 provider 或全部 provider 的模型列表（best-effort）。"""
        if provider_id is not None:
            provider = self._providers.get(provider_id)
            if provider is None:
                return []
            try:
                return provider.get_models()
            except Exception:
                return []

        models: List[Model] = []
        for provider in self._providers.values():
            try:
                models.extend(provider.get_models())
            except Exception:
                pass
        return models

    def get_all_models(self, provider_id: Optional[str] = None) -> List[AnyModel]:
        """返回全类型目录；既有 get_models() 始终保持 chat-only。"""
        if provider_id is None:
            providers = list(self._providers.values())
        else:
            provider = self._providers.get(provider_id)
            providers = [provider] if provider is not None else []
        models: List[AnyModel] = []
        for provider in providers:
            try:
                models.extend(provider.get_all_models())
            except Exception:
                pass
        return models

    def get_models_of_type(
        self, model_type: ModelType, provider_id: Optional[str] = None
    ) -> List[AnyModel]:
        """按 chat/image/classifier 操作类型读取目录。"""
        return [
            model
            for model in self.get_all_models(provider_id)
            if model.type == model_type
        ]

    def get_model_of_type(
        self, model_type: ModelType, provider_id: str, model_id: str
    ) -> Optional[AnyModel]:
        """按类型、provider 与 id 查找一个目录条目。"""
        return next(
            (
                model
                for model in self.get_models_of_type(model_type, provider_id)
                if model.id == model_id
            ),
            None,
        )

    def get_model(self, provider_id: str, model_id: str) -> Optional[Model]:
        """按 provider id + model id 查找模型。"""
        return next(
            (m for m in self.get_models(provider_id) if m.id == model_id),
            None,
        )

    def _catalog_service_for(self, provider: Provider) -> Optional[CatalogService]:
        """按 provider 选配解析目录站（对位 ``Provider._api_for`` 的双形态）。

        单站形态全员共用；dict 形态按 ``provider.catalog_source`` 查 key
        （未选配或查不到 = None，ctx.catalog_fetch 不发放）。
        """
        services = self._catalog_services
        if services is None:
            return None
        if isinstance(services, dict):
            key = provider.catalog_source
            return services.get(key) if key is not None else None
        return services

    # -----------------------------------------------------------------------
    # 刷新世代机制
    # -----------------------------------------------------------------------

    def _supersede_provider_refresh(self, provider_id: str) -> int:
        """bump 世代并 abort 该 provider 在飞的刷新 controller。"""
        generation = self._refresh_generations.get(provider_id, 0) + 1
        self._refresh_generations[provider_id] = generation
        previous = self._refresh_controllers.pop(provider_id, None)
        if previous is not None:
            previous.abort()
        return generation

    def _begin_provider_refresh(self, provider_id: str) -> Tuple[int, AbortController]:
        """开启一轮新刷新：先 supersede 旧的，再登记新 controller。"""
        generation = self._supersede_provider_refresh(provider_id)
        controller = AbortController(name=f"refresh:{provider_id}")
        self._refresh_controllers[provider_id] = controller
        return generation, controller

    async def _publish_provider_models(
        self,
        provider_id: str,
        generation: int,
        signal: AbortSignal,
        publication: ModelsPublication,
    ) -> bool:
        """世代校验的串行发布（对齐 TS publishProviderModels）。

        同 provider 的发布按链串行；持久化前后各做一次世代/中断校验。
        在 ModelsStore 的取消原子性契约内，被 supersede 的旧刷新不会污染
        存储或内存目录；非原子后台提交不属于本接口支持范围。
        """
        previous = self._publication_chains.get(provider_id)

        async def _queued() -> bool:
            if previous is not None:
                try:
                    await previous
                except asyncio.CancelledError:
                    # 只吸收“前序自身已取消”的链毒化；若当前等待者被取消，
                    # previous 并未取消，必须继续抛出本次取消。
                    if not previous.cancelled():
                        raise
                except Exception:
                    pass
            if (
                signal.aborted
                or self._refresh_generations.get(provider_id) != generation
            ):
                return False
            persist = publication.persist
            if isinstance(persist, ModelsStoreEntry):
                await self._models_store.write(
                    provider_id, persist.model_copy(deep=True)
                )
            elif persist is None:
                await self._models_store.delete(provider_id)
            # UNSET（未提及）：不动存储
            if (
                signal.aborted
                or self._refresh_generations.get(provider_id) != generation
            ):
                return False
            if publication.update is not None:
                publication.update()
            return True

        queued = asyncio.ensure_future(_queued())

        def _cleanup(_task: "asyncio.Future") -> None:
            if self._publication_chains.get(provider_id) is queued:
                self._publication_chains.pop(provider_id, None)

        queued.add_done_callback(_cleanup)
        self._publication_chains[provider_id] = queued
        return await await_with_abort(queued, signal)

    async def _run_provider_refresh_phase(
        self,
        provider: Provider,
        credential: Optional[Credential],
        *,
        allow_network: bool,
        force: Optional[bool],
        generation: int,
        signal: AbortSignal,
        collector: DiagnosticsCollector,
    ) -> List[NovaDiagnostic]:
        """跑一个刷新阶段；诊断写入本轮共享收集器，异常时也不丢失。"""
        # 能力字段探测（refresh() 的 refreshable 过滤保证只会走到动态
        # provider，这里再守一道是双保险）
        refresh_models = provider.refresh_models
        if refresh_models is None:
            return []
        stored = await self._models_store.read(provider.id)
        # 目录站发放：按选配解析站并绑定 provider id（partial 技巧——
        # ctx.catalog_fetch 对 refresh 实现只剩 (signal, validator) 两参）
        service = self._catalog_service_for(provider)
        context = RefreshModelsContext(
            credential=credential,
            base_url=provider.base_url,
            stored=stored.model_copy(deep=True) if stored is not None else None,
            publish=lambda publication: self._publish_provider_models(
                provider.id, generation, signal, publication
            ),
            allow_network=allow_network,
            force=bool(force) if allow_network else False,
            signal=signal,
            catalog_fetch=(
                partial(service.fetch, provider.id) if service is not None else None
            ),
            diagnostics=collector,
        )
        await refresh_models(context)
        return collector.items

    async def refresh(
        self,
        allow_network: bool = True,
        force: bool = False,
        signal: Optional[AbortSignal] = None,
        providers: Optional[Sequence[str]] = None,
        deadline_ms: Optional[float] = None,
    ) -> ModelsRefreshResult:
        """刷新动态 provider 的模型列表（对齐 TS Models.refresh，两阶段）。

        - ``providers``：限定只刷这些 provider id（未知/静态 provider 忽略）；
        - ``deadline_ms``：整轮预算（codex deadline 对位）——各阶段只花剩余
          时间，耗尽记 ``RequestTimeoutError``（不冒名 aborted）；缺省
          ``DEFAULT_REFRESH_DEADLINE_MS``；
        - 错误与中断按 provider 记入 ``errors``，不向外抛；
        - 每个 provider 一轮独立世代，全程可被 ``signal`` 或
          ``set/delete_provider`` 的 supersede 中断。
        """
        caller_signal = operation_signal(signal)
        deadline = Deadline(
            deadline_ms if deadline_ms is not None else DEFAULT_REFRESH_DEADLINE_MS
        )
        errors: Dict[str, Exception] = {}
        diagnostics: Dict[str, List[NovaDiagnostic]] = {}
        if caller_signal.aborted:
            return {"aborted": True, "errors": errors, "diagnostics": diagnostics}

        selected = set(providers) if providers is not None else None
        refreshable = [
            p
            for p in self._providers.values()
            if p.refresh_models is not None and (selected is None or p.id in selected)
        ]

        async def _refresh_one_uncached(provider: Provider) -> None:
            generation, controller = self._begin_provider_refresh(provider.id)
            phase_signal = any_signal([caller_signal, controller.signal])
            collector = DiagnosticsCollector(on_diagnostic=self._on_diagnostic)

            async def _operation() -> None:
                stored_credential: Optional[Credential] = None
                credential_error: Optional[Exception] = None
                try:
                    stored_credential = self._read_credential(provider.id)
                except Exception as exc:
                    credential_error = exc

                # 先恢复缓存（离线阶段），auth 解析与网络访问之前。离线恢复
                # 也在同一 deadline 内，慢存储不能绕过整轮预算。
                await self._run_provider_refresh_phase(
                    provider,
                    stored_credential,
                    allow_network=False,
                    force=None,
                    generation=generation,
                    signal=phase_signal,
                    collector=collector,
                )
                if credential_error is not None:
                    raise credential_error
                if not allow_network or phase_signal.aborted:
                    return

                # 无凭据不再一刀切跳过（有意分歧 pi）：匿名源（models.dev）
                # 无凭据也应刷新——凭据是"筛选输入"不是"刷新门禁"。
                credential = await self._resolve_refresh_credential(
                    provider, stored_credential, phase_signal
                )
                await self._run_provider_refresh_phase(
                    provider,
                    credential,
                    allow_network=True,
                    force=force,
                    generation=generation,
                    signal=phase_signal,
                    collector=collector,
                )

            try:
                # 同一预算覆盖恢复、凭据解析和网络，不逐阶段重新计时。
                async with asyncio.timeout(deadline.remaining_s):
                    await await_with_abort(_operation(), phase_signal)
            except RequestTimeoutError as exc:
                if not phase_signal.aborted:
                    errors[provider.id] = exc
            except TimeoutError as error:
                if not phase_signal.aborted:
                    errors[provider.id] = RequestTimeoutError(
                        f"Models refresh deadline exceeded for {provider.id}"
                    )
                    errors[provider.id].__cause__ = error
            except Exception as exc:
                if not phase_signal.aborted:
                    errors[provider.id] = exc
            finally:
                # 共享收集器让网络阶段 append 后抛错也不丢诊断。
                if collector.items:
                    diagnostics[provider.id] = list(collector.items)
                if self._refresh_controllers.get(provider.id) is controller:
                    self._refresh_controllers.pop(provider.id, None)

        # 每个调用独立运行；共享只发生在上层有完整结果传播与独立取消的
        # 编排器。manager 不做半共享（pi Models 同款）。
        async with asyncio.TaskGroup() as tg:
            for provider in refreshable:
                tg.create_task(_refresh_one_uncached(provider))

        return {
            "aborted": bool(caller_signal.aborted),
            "errors": errors,
            "diagnostics": diagnostics,
        }

    async def _resolve_refresh_credential(
        self,
        provider: Provider,
        stored: Optional[Credential],
        signal: AbortSignal,
    ) -> Optional[Credential]:
        """为模型刷新解析 credential（对齐 TS resolveRefreshCredential）。"""
        if stored is not None and stored.type == "oauth":
            oauth = provider.auth.oauth if provider.auth else None
            if oauth is None:
                return None
            if stored.expires > int(time.time() * 1000):
                return stored
            if signal.aborted:
                return None

            return await refresh_stored_oauth_credential(
                self._credential_store,
                provider.id,
                oauth,
                lambda current: current.expires <= int(time.time() * 1000),
                signal,
                self._refresh_locks,
            )

        api_key = provider.auth.api_key if provider.auth else None
        if api_key is None:
            return None
        credential = stored if stored is not None and stored.type == "api_key" else None
        result = await api_key.resolve(
            {"ctx": self._auth_context, "credential": credential, "signal": signal}
        )
        if result is None:
            return None
        key = result.auth.get("api_key")
        return ApiKeyCredential(
            key=RedactedString(key) if key is not None else None,
            env=result.env,
        )

    def _read_credential(self, provider_id: str) -> Optional[Credential]:
        try:
            return self._credential_store.read(provider_id)
        except Exception as exc:
            raise ModelsError(
                "auth", f"Credential store read failed for {provider_id}", exc
            ) from exc

    # -----------------------------------------------------------------------
    # Auth
    # -----------------------------------------------------------------------

    async def get_auth(
        self,
        provider_or_model: Union[str, ModelBase],
        overrides: Optional[AuthResolutionOverrides] = None,
    ) -> Optional[AuthResult]:
        """解析 provider 或 model 的鉴权（对齐 TS Models.getAuth）。"""
        overrides = overrides or AuthResolutionOverrides()
        if overrides.refresh_locks is None:
            overrides = _dataclass_replace(overrides, refresh_locks=self._refresh_locks)
        signal = operation_signal(overrides.signal)
        provider_id = (
            provider_or_model
            if isinstance(provider_or_model, str)
            else provider_or_model.provider
        )
        provider = self._providers.get(provider_id)
        if provider is None or provider.auth is None:
            return None
        result = await await_with_abort(
            resolve_provider_auth(
                provider_id,
                provider.auth,
                self._credential_store,
                self._auth_context,
                overrides,
            ),
            signal,
        )
        if result is None or isinstance(provider_or_model, str):
            return result

        # 按优先级合并 headers：auth 解析结果 < model 静态 headers <
        # 请求时解析的 per-model headers（resolver 注入）
        headers = result.auth.get("headers")
        if provider_or_model.headers:
            headers = _merge_headers(headers, provider_or_model.headers)
        if self._model_headers_resolver is not None:
            resolved = self._model_headers_resolver(provider_or_model, result.env)
            if inspect.isawaitable(resolved):
                resolved = await await_with_abort(resolved, signal)
            if resolved:
                headers = _merge_headers(headers, resolved)
        if not headers:
            return result
        return AuthResult(
            auth={**result.auth, "headers": headers},
            env=result.env,
            source=result.source,
        )

    async def get_auth_for_model(
        self,
        model: Model,
        overrides: Optional[AuthResolutionOverrides] = None,
    ) -> Optional[AuthResult]:
        """按 model.provider 解析鉴权。"""
        return await self.get_auth(model, overrides)

    async def _check_provider_auth(
        self,
        provider: Provider,
        credential: Optional[Credential],
        signal: AbortSignal,
    ) -> Optional[AuthCheck]:
        """检查 provider auth 是否配置（对齐 TS checkProviderAuth）。"""
        provider_auth = provider.auth
        if credential is not None and credential.type == "oauth":
            return (
                AuthCheck(type="oauth", source="OAuth")
                if provider_auth and provider_auth.oauth
                else None
            )
        if provider_auth is None:
            return None
        api_key = provider_auth.api_key
        if api_key is None:
            return None
        if api_key.check:
            try:
                return await await_with_abort(
                    api_key.check(
                        {
                            "ctx": self._auth_context,
                            "credential": (
                                credential
                                if credential is not None
                                and credential.type == "api_key"
                                else None
                            ),
                            "signal": signal,
                        }
                    ),
                    signal,
                )
            except Exception as exc:
                if isinstance(exc, AbortedError):
                    raise
                raise ModelsError(
                    "auth",
                    f"API key auth check failed for provider {provider.id}",
                    exc,
                ) from exc
        resolution = await await_with_abort(
            resolve_provider_auth(
                provider.id,
                provider_auth,
                self._credential_store,
                self._auth_context,
                AuthResolutionOverrides(refresh_locks=self._refresh_locks),
            ),
            signal,
        )
        return (
            AuthCheck(type="api_key", source=resolution.source) if resolution else None
        )

    async def check_auth(
        self,
        provider_id: str,
        signal: Optional[AbortSignal] = None,
    ) -> Optional[AuthCheck]:
        """检查 provider 是否已配置鉴权（不触发网络刷新）。"""
        op_signal = operation_signal(signal)
        op_signal.throw_if_aborted()
        provider = self._providers.get(provider_id)
        if provider is None:
            return None
        credential = self._read_credential(provider_id)
        return await await_with_abort(
            self._check_provider_auth(provider, credential, op_signal), op_signal
        )

    async def get_available(
        self,
        provider_id: Optional[str] = None,
        signal: Optional[AbortSignal] = None,
    ) -> List[Model]:
        """返回已配置鉴权的 provider 的模型（对齐 TS Models.getAvailable）。"""
        op_signal = operation_signal(signal)
        op_signal.throw_if_aborted()
        providers = (
            [self._providers[provider_id]]
            if provider_id is not None and provider_id in self._providers
            else ([] if provider_id is not None else list(self._providers.values()))
        )

        async def _check_one(
            provider: Provider,
        ) -> tuple[Provider, Optional[Credential], Optional[AuthCheck]]:
            credential = self._read_credential(provider.id)
            auth = await self._check_provider_auth(provider, credential, op_signal)
            return provider, credential, auth

        checks_or_errors = await asyncio.gather(
            *[_check_one(p) for p in providers], return_exceptions=True
        )
        # 首个异常按序上浮（gather 默认语义）但先收齐——裸 gather 先抛后
        # 其余任务的异常无人取回（"exception was never retrieved" 噪音）
        checks = []
        for item in checks_or_errors:
            if isinstance(item, BaseException):
                raise item
            checks.append(item)
        result: List[Model] = []
        for provider, credential, auth in checks:
            if auth is None:
                continue
            models = provider.get_models()
            if provider.filter_models is not None:
                models = provider.filter_models(models, credential)
            result.extend(models)
        return result

    async def login(
        self,
        provider_id: str,
        type: AuthType,
        interaction: AuthInteraction,
        signal: Optional[AbortSignal] = None,
    ) -> Credential:
        """执行 provider 的登录流程并持久化 credential。

        abort 语义：login 与刷新/登出同锁串行；整段在 AbortScope 内——
        取消即真杀（凭据不写入）；写是同步瞬时，无"半写入"窗口。
        """
        op_signal = operation_signal(
            signal
            if signal is not None
            else (interaction.signal if interaction is not None else None)
        )
        op_signal.throw_if_aborted()
        provider = self._providers.get(provider_id)
        if provider is None:
            raise ModelsError("provider", f"Unknown provider: {provider_id}")
        if provider.auth is None:
            raise ModelsError("auth", f"{provider.name} has no auth configured")

        method = provider.auth.oauth if type == "oauth" else provider.auth.api_key
        if method is None or method.login is None:
            raise ModelsError("auth", f"{provider.name} does not support {type} login")
        # 绑定局部变量再进闭包（pyright 属性收窄不随闭包传递）
        login_method = method.login

        # login 与刷新/登出同锁串行（pi 契约）；整段包 scope——锁等待、
        # login 流程、写入任一阶段取消都真杀；写是同步瞬时，无"半写入"窗口
        async def _do_login() -> Credential:
            async with self._refresh_locks(provider_id):
                credential = await login_method(interaction)
                try:
                    self._credential_store.write(provider_id, credential)
                except Exception as exc:
                    raise ModelsError(
                        "auth", f"Credential store write failed for {provider_id}", exc
                    ) from exc
                return credential

        return await await_with_abort(_do_login(), op_signal)

    async def logout(
        self,
        provider_id: str,
        signal: Optional[AbortSignal] = None,
    ) -> None:
        """删除 provider 的已存储 credential。"""
        op_signal = operation_signal(signal)
        op_signal.throw_if_aborted()

        # logout 与刷新/登录同锁串行（防"刷新在飞期间登出又复活"）
        async def _do_logout() -> None:
            async with self._refresh_locks(provider_id):
                self._credential_store.delete(provider_id)

        try:
            await await_with_abort(_do_logout(), op_signal)
        except Exception as exc:
            if isinstance(exc, AbortedError):
                raise
            raise ModelsError(
                "auth", f"Credential store delete failed for {provider_id}", exc
            ) from exc

    async def set_credential(
        self,
        provider_id: str,
        credential: Credential,
        signal: Optional[AbortSignal] = None,
    ) -> None:
        """写入 credential（**凭据写路径的统一漏斗**——与刷新/登出/登录同锁
        串行）。

        纪律入结构：RPC/命令层一律经此方法写凭据，不直接摸 store——否则
        可被在飞刷新整条目覆盖（新写的 api key 被刷新完成的 OAuth 写回
        吞掉）。provider 无需已注册（凭据可先于 provider 存在）。
        """
        op_signal = operation_signal(signal)
        op_signal.throw_if_aborted()

        async def _do_write() -> None:
            async with self._refresh_locks(provider_id):
                try:
                    self._credential_store.write(provider_id, credential)
                except Exception as exc:
                    raise ModelsError(
                        "auth", f"Credential store write failed for {provider_id}", exc
                    ) from exc

        await await_with_abort(_do_write(), op_signal)

    # -----------------------------------------------------------------------
    # Stream dispatch
    # -----------------------------------------------------------------------

    def _require_provider(self, model: ModelBase) -> Provider:
        """根据 ``model.provider`` 查找 provider，找不到则抛错。"""
        provider = self._providers.get(model.provider)
        if provider is None:
            raise ModelsError("provider", f"Unknown provider: {model.provider}")
        return provider

    async def _resolve_request_auth(
        self,
        model: _ModelT,
        options: Optional[Union[StreamOptions, ImagesOptions, ClassifierOptions]],
    ) -> tuple[
        _ModelT,
        Optional[str],
        Optional[ProviderHeaders],
        Optional[ProviderEnv],
    ]:
        """聊天/图像/分类共享的 auth、base_url、headers 与环境装配。"""
        provider = self._require_provider(model)
        resolution = await self.get_auth(
            model,
            AuthResolutionOverrides(
                api_key=options.api_key if options else None,
                env=options.env if options else None,
                signal=options.signal if options else None,
            ),
        )
        if resolution is None:
            raise ModelsError(
                "auth",
                f"Provider is not configured: {model.provider}. "
                "Set the provider's API key env var, or run /login to authenticate.",
                kind=NovaErrorKind.LOGIN_REQUIRED,
            )

        auth = resolution.auth
        api_key = (
            options.api_key
            if options is not None and options.api_key is not None
            else auth.get("api_key")
        )
        options_headers = options.headers if options else None
        headers = _merge_headers(
            provider.headers, _merge_headers(auth.get("headers"), options_headers)
        )
        transform = options.transform_headers if options else None
        if transform is not None:
            transformed = transform(headers or {})
            if inspect.isawaitable(transformed):
                transformed = await await_with_abort(
                    transformed, options.signal if options else None
                )
            headers = transformed
        options_env = options.env if options else None
        env = (
            {**(resolution.env or {}), **(options_env or {})}
            if resolution.env or options_env
            else None
        )
        request_model = model
        base_url_override = auth.get("base_url")
        if base_url_override:
            request_model = model.model_copy(update={"base_url": base_url_override})
        if headers is not None:
            # Manager 已完成 provider/auth/model/options/transform 全量合并；
            # 清空模型副本上的静态头，防协议客户端再次叠加、让删键复活。
            request_model = request_model.model_copy(update={"headers": None})
        return request_model, api_key, headers, env

    async def _apply_auth(
        self,
        model: Model,
        options: Optional[StreamOptions],
    ) -> tuple[Model, Optional[StreamOptions]]:
        """把共享鉴权结果写回聊天模型与 StreamOptions。"""
        request_model, api_key, headers, env = await self._resolve_request_auth(
            model, options
        )
        if options is None:
            request_options = SimpleStreamOptions(
                api_key=api_key,
                headers=headers,
                env=env,
            )
        else:
            update: _ApplyAuthPatch = {"transform_headers": None}
            if api_key is not None:
                update["api_key"] = api_key
            if headers is not None:
                update["headers"] = headers
            if env is not None:
                update["env"] = env
            request_options = _dataclass_replace(options, **update)

        return request_model, request_options

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """流式调用模型（同步返回；provider 查找与 auth 解析在 setup 闭包内）。"""

        async def _setup() -> AssistantMessageEventStream:
            # 类型错误先于凭据读取、OAuth 刷新和 header 钩子副作用。
            assert_chat_model(model)
            provider = self._require_provider(model)
            request_model, request_options = await self._apply_auth(model, options)
            return provider.stream(request_model, context, request_options)

        return lazy_stream(model, _setup, signal=options.signal if options else None)

    async def complete(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessage:
        """非流式完成调用。"""
        return await self.stream(model, context, options).result()

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """简化的流式调用（同步返回；provider 查找与 auth 解析在 setup 闭包内）。"""

        async def _setup() -> AssistantMessageEventStream:
            # 类型错误先于凭据读取、OAuth 刷新和 header 钩子副作用。
            assert_chat_model(model)
            provider = self._require_provider(model)
            request_model, request_options = await self._apply_auth(model, options)
            # _apply_auth 的不变量：None → 新造 SimpleStreamOptions；
            # 否则 dataclasses.replace 保留原类（输入恒为 SimpleStreamOptions）
            return provider.stream_simple(
                request_model,
                context,
                cast(Optional[SimpleStreamOptions], request_options),
            )

        return lazy_stream(model, _setup, signal=options.signal if options else None)

    async def complete_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> AssistantMessage:
        """简化的非流式完成调用。"""
        return await self.stream_simple(model, context, options).result()

    async def generate_images(
        self,
        model: AnyModel,
        context: ImagesContext,
        options: Optional[ImagesOptions] = None,
    ) -> AssistantImages:
        """经所属 provider 生成图像；鉴权/分派失败以结果返回，不抛出。"""
        try:
            if not isinstance(model, ImageModel):
                raise ModelsError(
                    "provider",
                    f"Model {model.provider}/{model.id} is not an image model",
                )
            provider = self._require_provider(model)
            generate = provider.generate_images
            if generate is None:
                raise ModelsError(
                    "provider",
                    f"Provider {model.provider} does not support image generation",
                )
            options = options or ImagesOptions()
            request_model, api_key, headers, env = await self._resolve_request_auth(
                model, options
            )
            update: _ApplyAuthPatch = {"transform_headers": None}
            if api_key is not None:
                update["api_key"] = api_key
            if headers is not None:
                update["headers"] = headers
            if env is not None:
                update["env"] = env
            request_options = _dataclass_replace(options, **update)
            return await generate(request_model, context, request_options)
        except Exception as error:
            aborted = isinstance(error, AbortedError) or is_aborted(
                options.signal if options else None
            )
            return image_error_result(model, error, aborted=aborted)

    async def classify(
        self,
        model: AnyModel,
        context: ClassifierContext,
        options: Optional[ClassifierOptions] = None,
    ) -> ClassifierResult:
        """经所属 provider 分类；类型、鉴权与分派失败以结果返回。"""
        try:
            classifier = assert_classifier_model(model)
            assert_classifier_input_supported(classifier, context)
            provider = self._require_provider(classifier)
            classify = provider.classify
            if classify is None:
                raise ModelsError(
                    "provider",
                    f"Provider {classifier.provider} does not support classification",
                )
            options = options or ClassifierOptions()
            request_model, api_key, headers, env = await self._resolve_request_auth(
                classifier, options
            )
            update: _ApplyAuthPatch = {"transform_headers": None}
            if api_key is not None:
                update["api_key"] = api_key
            if headers is not None:
                update["headers"] = headers
            if env is not None:
                update["env"] = env
            request_options = _dataclass_replace(options, **update)
            return await classify(request_model, context, request_options)
        except Exception as error:
            aborted = isinstance(error, AbortedError) or is_aborted(
                options.signal if options else None
            )
            return classifier_error_result(model, error, aborted=aborted)


def create_models_manager(
    credential_store: Optional[CredentialStore] = None,
    models_store: Optional[ModelsStore] = None,
    auth_context: Optional[AuthContext] = None,
    model_headers_resolver: Optional[ModelHeadersResolver] = None,
    catalog_services: Optional[CatalogServices] = None,
    refresh_locks: KeyedLockFactory = default_keyed_locks,
    on_diagnostic: Optional[Callable[[NovaDiagnostic], None]] = None,
) -> ModelsManager:
    """构造空的 ModelsManager 集合。

    ``catalog_services``：目录站容器（单站全员共用 / dict 按
    ``provider.catalog_source`` 路由），缺省 None = 无站；
    ``refresh_locks``：per-provider 刷新锁工厂，缺省进程内锁
    （多进程部署由组合根注入文件锁）；
    ``on_diagnostic``：诊断实时缝（可选）——refresh 产出的每条诊断在
    收集的同时经此外发（组合根桥接 WarningEvent / 呈现；缺省 None =
    纯批模式，仅随 refresh 结果返回）。
    """
    return ModelsManager(
        credential_store=credential_store,
        models_store=models_store,
        auth_context=auth_context,
        model_headers_resolver=model_headers_resolver,
        catalog_services=catalog_services,
        refresh_locks=refresh_locks,
        on_diagnostic=on_diagnostic,
    )
