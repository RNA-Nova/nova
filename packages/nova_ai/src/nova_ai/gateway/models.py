"""Models 网关（对齐 TS ``src/models.ts`` 的 ``ModelsImpl``）。

Provider 运行时集合 + auth 应用 + stream 便捷入口。本模块的并发纪律：

- **世代守卫**：每个 provider 的刷新带递增世代；``set/delete/clear_provider``
  会 supersede 在飞刷新（bump 世代 + abort controller），旧刷新的发布在
  ``publish`` 处被世代校验拦下，永不覆盖新目录；
- **两阶段刷新**：先离线恢复缓存（``allow_network=False``），再解析凭据
  走网络阶段——离线启动也能拿到上次的目录；
- **signal 贯穿**：所有公开异步入口接受可选 ``AbortSignal``，
  经 :func:`nova_ai.utils.abort.race_with_abort` 竞速；
- **stream 契约**：一切可失败步骤（provider 查找、auth 解析）都在
  :func:`nova_ai.gateway.streams.lazy_stream` 的 setup 闭包内进行，
  失败编码为 error 事件，绝不同步抛出。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from ..auth.context import default_provider_auth_context
from ..auth.credential_store import InMemoryCredentialStore
from ..auth.resolve import (
    AuthResolutionOverrides,
    ModelsError,
    resolve_provider_auth,
)
from ..signal import AbortController, AbortedError, AbortSignal
from ..streaming import AssistantMessageEventStream
from ..types.aliases import ProviderHeaders
from ..types.auth import (
    ApiKeyCredential,
    AuthCheck,
    AuthContext,
    AuthResult,
    AuthType,
    Credential,
    CredentialStore,
    ProviderAuth,
)
from ..types.messages import AssistantMessage, Context
from ..types.model import Model
from ..types.stream_options import SimpleStreamOptions, StreamOptions
from ..utils.abort import any_signal, operation_signal, race_with_abort
from .provider import UNSET, ModelsPublication, Provider, RefreshModelsContext
from .store import InMemoryModelsStore, ModelsStore, ModelsStoreEntry
from .streams import lazy_stream


def _merge_headers(
    base: Optional[ProviderHeaders],
    override: Optional[ProviderHeaders],
) -> Optional[ProviderHeaders]:
    """大小写不敏感地合并 headers（对齐 TS mergeHeaders）。"""
    if not base and not override:
        return None
    merged: Dict[str, Any] = dict(base or {})
    for name, value in (override or {}).items():
        lower_name = name.lower()
        for existing_name in list(merged.keys()):
            if existing_name.lower() == lower_name:
                del merged[existing_name]
        merged[name] = value
    return merged


class Models:
    """Provider 运行时集合（并发语义见模块 docstring）。"""

    def __init__(
        self,
        credential_store: Optional[CredentialStore] = None,
        models_store: Optional[ModelsStore] = None,
        auth_context: Optional[AuthContext] = None,
        model_headers_resolver: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._providers: Dict[str, Provider] = {}
        self._credential_store = credential_store or InMemoryCredentialStore()
        self._models_store = models_store or InMemoryModelsStore()
        self._auth_context = auth_context or default_provider_auth_context()
        # 可选的 per-model headers 请求时解析钩子：
        # (model, env) -> Optional[ProviderHeaders]，由上层（如 harness 的
        # models.json 配置）注入；nova_ai 自身不关心模板语法。
        self._model_headers_resolver = model_headers_resolver
        # 刷新世代机制：世代号 / 在飞 controller / 每 provider 发布链
        self._refresh_generations: Dict[str, int] = {}
        self._refresh_controllers: Dict[str, AbortController] = {}
        self._inflight_refreshes: Dict[str, "asyncio.Task[None]"] = {}
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

    def get_model(self, provider_id: str, model_id: str) -> Optional[Model]:
        """按 provider id + model id 查找模型。"""
        return next(
            (m for m in self.get_models(provider_id) if m.id == model_id),
            None,
        )

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

        同 provider 的发布按链串行；持久化前后各做一次世代/中断校验——
        被 supersede 的旧刷新在此处被拦下，存储与内存目录都不会被污染。
        """
        previous = self._publication_chains.get(provider_id)

        async def _queued() -> bool:
            if previous is not None:
                try:
                    await previous
                except Exception:
                    pass
            if (
                signal.aborted
                or self._refresh_generations.get(provider_id) != generation
            ):
                return False
            if publication.persist is UNSET:
                pass
            elif publication.persist is None:
                await self._models_store.delete(provider_id)
            else:
                await self._models_store.write(
                    provider_id, publication.persist.model_copy(deep=True)
                )
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
        return await race_with_abort(queued, signal)

    async def _run_provider_refresh_phase(
        self,
        provider: Provider,
        credential: Optional[Credential],
        *,
        allow_network: bool,
        force: Optional[bool],
        generation: int,
        signal: AbortSignal,
    ) -> None:
        """跑一个刷新阶段（离线恢复或网络拉取）。"""
        stored = await self._models_store.read(provider.id)
        await provider.refresh_models(
            RefreshModelsContext(
                credential=credential,
                stored=stored.model_copy(deep=True) if stored is not None else None,
                publish=lambda publication: self._publish_provider_models(
                    provider.id, generation, signal, publication
                ),
                allow_network=allow_network,
                force=force if allow_network else False,
                signal=signal,
            )
        )

    async def refresh(
        self,
        allow_network: bool = True,
        force: bool = False,
        signal: Optional[AbortSignal] = None,
        providers: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """刷新动态 provider 的模型列表（对齐 TS Models.refresh，两阶段）。

        - ``providers``：限定只刷这些 provider id（未知/静态 provider 忽略）；
        - 错误与中断按 provider 记入 ``errors``，不向外抛；
        - 每个 provider 一轮独立世代，全程可被 ``signal`` 或
          ``set/delete_provider`` 的 supersede 中断。
        """
        caller_signal = operation_signal(signal)
        errors: Dict[str, Exception] = {}
        if caller_signal.aborted:
            return {"aborted": True, "errors": errors}

        selected = set(providers) if providers is not None else None
        refreshable = [
            p
            for p in self._providers.values()
            if getattr(p, "refresh_models", None) is not None
            and (selected is None or p.id in selected)
        ]

        async def _refresh_one(provider: Provider) -> None:
            inflight = self._inflight_refreshes.get(provider.id)
            if inflight is not None and not inflight.done():
                # 在途去重：同 provider 的并发刷新共享一次网络往返
                # （否则两边互相 bump 世代 supersede，先完成者的发布被拦下）
                try:
                    await inflight
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass  # 错误归属发起方调用的 errors 字典
                return
            task = asyncio.current_task()
            assert task is not None
            self._inflight_refreshes[provider.id] = task
            try:
                await _refresh_one_uncached(provider)
            finally:
                if self._inflight_refreshes.get(provider.id) is task:
                    self._inflight_refreshes.pop(provider.id, None)

        async def _refresh_one_uncached(provider: Provider) -> None:
            generation, controller = self._begin_provider_refresh(provider.id)
            phase_signal = any_signal([caller_signal, controller.signal])

            async def _operation() -> None:
                stored_credential: Optional[Credential] = None
                credential_error: Optional[Exception] = None
                try:
                    stored_credential = await self._read_credential(
                        provider.id, phase_signal
                    )
                except Exception as exc:
                    credential_error = exc

                # 先恢复缓存（离线阶段），auth 解析与网络访问之前
                await self._run_provider_refresh_phase(
                    provider,
                    stored_credential,
                    allow_network=False,
                    force=None,
                    generation=generation,
                    signal=phase_signal,
                )
                if credential_error is not None:
                    raise credential_error
                if not allow_network or phase_signal.aborted:
                    return

                credential = await self._resolve_refresh_credential(
                    provider, stored_credential, phase_signal
                )
                if credential is None:
                    return
                await self._run_provider_refresh_phase(
                    provider,
                    credential,
                    allow_network=True,
                    force=force,
                    generation=generation,
                    signal=phase_signal,
                )

            try:
                await race_with_abort(_operation(), phase_signal)
            except Exception as exc:
                if not phase_signal.aborted:
                    errors[provider.id] = (
                        exc
                        if isinstance(exc, Exception)
                        else ModelsError(
                            "model_source",
                            f"Model refresh failed for {provider.id}",
                            exc,
                        )
                    )
            finally:
                if self._refresh_controllers.get(provider.id) is controller:
                    self._refresh_controllers.pop(provider.id, None)

        # TaskGroup（3.11+ 结构化并发）：_refresh_one 内部已按 provider
        # 收集错误，正常不外抛；若外抛（如取消）则整组等待收尾后传播。
        async with asyncio.TaskGroup() as tg:
            for provider in refreshable:
                tg.create_task(_refresh_one(provider))

        return {
            "aborted": bool(caller_signal.aborted),
            "errors": errors,
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
            if getattr(stored, "expires", 0) > int(time.time() * 1000):
                return stored
            if signal.aborted:
                return None

            async def _do_refresh(
                current: Optional[Credential],
            ) -> Optional[Credential]:
                if current is None or current.type != "oauth":
                    return None
                if getattr(current, "expires", 0) > int(time.time() * 1000):
                    return None
                return await oauth.refresh(current, signal)

            post = await self._credential_store.modify(provider.id, _do_refresh)
            return post if post is not None and post.type == "oauth" else None

        api_key = provider.auth.api_key if provider.auth else None
        if api_key is None:
            return None
        credential = stored if stored is not None and stored.type == "api_key" else None
        result = await api_key.resolve(
            {"ctx": self._auth_context, "credential": credential}
        )
        if result is None:
            return None
        return ApiKeyCredential(
            key=result.auth.get("api_key"),
            env=result.env,
        )

    async def _read_credential(
        self, provider_id: str, signal: Optional[AbortSignal] = None
    ) -> Optional[Credential]:
        try:
            return await race_with_abort(
                self._credential_store.read(provider_id), signal
            )
        except Exception as exc:
            if not isinstance(exc, AbortedError):
                raise ModelsError(
                    "auth", f"Credential store read failed for {provider_id}", exc
                ) from exc
            raise

    # -----------------------------------------------------------------------
    # Auth
    # -----------------------------------------------------------------------

    async def get_auth(
        self,
        provider_or_model: Union[str, Model],
        overrides: Optional[AuthResolutionOverrides] = None,
    ) -> Optional[Any]:
        """解析 provider 或 model 的鉴权（对齐 TS Models.getAuth）。"""
        overrides = overrides or AuthResolutionOverrides()
        signal = operation_signal(getattr(overrides, "signal", None))
        provider_id = (
            provider_or_model
            if isinstance(provider_or_model, str)
            else provider_or_model.provider
        )
        provider = self._providers.get(provider_id)
        if provider is None or provider.auth is None:
            return None
        result = await race_with_abort(
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
                resolved = await resolved
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
    ) -> Optional[Any]:
        """按 model.provider 解析鉴权。"""
        return await self.get_auth(model, overrides)

    async def _check_provider_auth(
        self,
        provider: Provider,
        credential: Optional[Credential],
        signal: AbortSignal,
    ) -> Optional[AuthCheck]:
        """检查 provider auth 是否配置（对齐 TS checkProviderAuth）。"""
        if credential is not None and credential.type == "oauth":
            return (
                AuthCheck(type="oauth", source="OAuth")
                if provider.auth and provider.auth.oauth
                else None
            )
        api_key = provider.auth.api_key if provider.auth else None
        if api_key is None:
            return None
        if api_key.check:
            try:
                return await race_with_abort(
                    api_key.check(
                        {
                            "ctx": self._auth_context,
                            "credential": (
                                credential
                                if credential is not None
                                and credential.type == "api_key"
                                else None
                            ),
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
        resolution = await race_with_abort(
            resolve_provider_auth(
                provider.id,
                provider.auth,
                self._credential_store,
                self._auth_context,
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
        credential = await self._read_credential(provider_id, op_signal)
        return await race_with_abort(
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
            else list(self._providers.values())
        )

        async def _check_one(
            provider: Provider,
        ) -> tuple[Provider, Optional[Credential], Optional[AuthCheck]]:
            credential = await self._read_credential(provider.id, op_signal)
            auth = await self._check_provider_auth(provider, credential, op_signal)
            return provider, credential, auth

        checks = await asyncio.gather(*[_check_one(p) for p in providers])
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
        interaction: Any,
        signal: Optional[AbortSignal] = None,
    ) -> Optional[Any]:
        """执行 provider 的登录流程并持久化 credential。

        abort 语义对齐 TS：mutation 已开始则等它完成（凭据不会"半写入"），
        尚未开始则拒绝写入并按 abort 收场。
        """
        op_signal = operation_signal(
            signal if signal is not None else getattr(interaction, "signal", None)
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
        credential = await race_with_abort(method.login(interaction), op_signal)

        mutation_started = asyncio.Event()

        async def _set(_current: Optional[Credential]) -> Optional[Credential]:
            mutation_started.set()
            return credential

        # abort 竞速（对齐 TS login）：mutation 尚未开始则按 abort 收场
        # （凭据不写入）；已开始则等它完成——不会"半写入"。
        mutation = asyncio.ensure_future(
            self._credential_store.modify(provider_id, _set)
        )
        abort_waiter = asyncio.ensure_future(op_signal.wait())
        try:
            done, _pending = await asyncio.wait(
                {mutation, abort_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            if mutation in done:
                mutation.result()  # 存储错误在此抛出
            elif mutation_started.is_set():
                await mutation  # 已开始：等写入完成
            else:
                # 尚未开始：取消排队中的 mutation——否则 store 稍后仍会执行
                # _set，凭据照样落盘（对齐 TS "未开始即拒绝写入"契约）
                mutation.cancel()
                mutation.add_done_callback(
                    lambda t: t.exception() if not t.cancelled() else None
                )
                op_signal.throw_if_aborted()
        except AbortedError:
            raise
        except Exception as exc:
            raise ModelsError(
                "auth", f"Credential store modify failed for {provider_id}", exc
            ) from exc
        finally:
            abort_waiter.cancel()
        return credential

    async def logout(
        self,
        provider_id: str,
        signal: Optional[AbortSignal] = None,
    ) -> None:
        """删除 provider 的已存储 credential。"""
        op_signal = operation_signal(signal)
        op_signal.throw_if_aborted()
        try:
            await race_with_abort(self._credential_store.delete(provider_id), op_signal)
        except Exception as exc:
            if isinstance(exc, AbortedError):
                raise
            op_signal.throw_if_aborted()
            raise ModelsError(
                "auth", f"Credential store delete failed for {provider_id}", exc
            ) from exc

    # -----------------------------------------------------------------------
    # Stream dispatch
    # -----------------------------------------------------------------------

    def _require_provider(self, model: Model) -> Provider:
        """根据 ``model.provider`` 查找 provider，找不到则抛错。"""
        provider = self._providers.get(model.provider)
        if provider is None:
            raise ModelsError("provider", f"Unknown provider: {model.provider}")
        return provider

    async def _apply_auth(
        self,
        model: Model,
        options: Optional[StreamOptions],
    ) -> tuple[Model, Optional[StreamOptions]]:
        """解析 auth 并合并到 model/options（对齐 TS Models.applyAuth）。"""
        self._require_provider(model)
        resolution = await self.get_auth(
            model,
            AuthResolutionOverrides(
                api_key=options.api_key if options else None,
                env=options.env if options else None,
                signal=options.signal if options else None,
            ),
        )
        if resolution is None:
            raise ModelsError("auth", f"Provider is not configured: {model.provider}")

        auth = resolution.auth
        # 对齐 TS ??：仅当显式传入的 api_key 为 None 时才回落到 auth 解析结果
        api_key = (
            options.api_key
            if options is not None and options.api_key is not None
            else auth.get("api_key")
        )
        headers = _merge_headers(
            auth.get("headers"), options.headers if options else None
        )
        # Models 层 transform_headers 最后运行（对齐 TS applyAuth），
        # 并在派发给 provider 前从 options 中移除。
        transform = options.transform_headers if options else None
        if transform is not None:
            transformed = transform(headers or {})
            if inspect.isawaitable(transformed):
                transformed = await transformed
            headers = transformed
        env = (
            {**(resolution.env or {}), **(options.env or {})}
            if resolution.env or (options and options.env)
            else None
        )

        request_model = model
        if auth.get("base_url"):
            request_model = model.model_copy(update={"base_url": auth["base_url"]})

        if options is None:
            request_options = SimpleStreamOptions(
                api_key=api_key,
                headers=headers,
                env=env,
            )
        else:
            update: Dict[str, Any] = {"transform_headers": None}
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
            provider = self._require_provider(model)
            request_model, request_options = await self._apply_auth(model, options)
            return provider.stream(request_model, context, request_options)

        return lazy_stream(model, _setup)

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
            provider = self._require_provider(model)
            request_model, request_options = await self._apply_auth(model, options)
            return provider.stream_simple(request_model, context, request_options)

        return lazy_stream(model, _setup)

    async def complete_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> AssistantMessage:
        """简化的非流式完成调用。"""
        return await self.stream_simple(model, context, options).result()


def _dataclass_replace(options: StreamOptions, **update: Any) -> StreamOptions:
    """dataclasses.replace 的模块间接层（便于测试与将来自定义选项类）。"""
    from dataclasses import replace

    return replace(options, **update)


def create_models(
    credential_store: Optional[CredentialStore] = None,
    models_store: Optional[ModelsStore] = None,
    auth_context: Optional[AuthContext] = None,
    model_headers_resolver: Optional[Callable[..., Any]] = None,
) -> Models:
    """构造空的 Models 集合。"""
    return Models(
        credential_store=credential_store,
        models_store=models_store,
        auth_context=auth_context,
        model_headers_resolver=model_headers_resolver,
    )
