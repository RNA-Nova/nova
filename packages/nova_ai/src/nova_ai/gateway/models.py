"""Models 集合（对齐 TS ``src/models.ts`` 的 ``Models``/``createModels`` 部分）。

Models 是 provider 的运行时集合：auth 网关（``applyAuth`` 解析链）+
provider 注册表 + 共享设施宿主（credential store / models store / auth context）。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Union

from ..auth.context import default_provider_auth_context
from ..auth.credential_store import InMemoryCredentialStore
from ..auth.resolve import (
    AuthResolutionOverrides,
    ModelsError,
    resolve_provider_auth,
)
from ..signal import AbortSignal
from ..streaming import AssistantMessageEventStream
from ..types.auth import (
    ApiKeyCredential,
    AuthCheck,
    AuthContext,
    AuthResult,
    AuthType,
    Credential,
    CredentialStore,
)
from ..types.enums import StopReason
from ..types.events import ErrorEvent
from ..types.messages import AssistantMessage, Context
from ..types.model import Model, Usage
from ..types.stream_options import SimpleStreamOptions, StreamOptions
from .provider import Provider, RefreshModelsContext
from .store import (
    InMemoryModelsStore,
    ModelsStore,
    ModelsStoreEntry,
    ProviderModelsStore,
)


def _create_setup_error_message(model: Model, error: BaseException) -> AssistantMessage:
    """构造 setup 失败时的 error AssistantMessage（对齐 TS lazyStream）。"""
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        stop_reason=StopReason.ERROR,
        error_message=str(error),
        timestamp=int(time.time() * 1000),
    )


def _lazy_stream(
    model: Model,
    setup: Callable[[], Any],
) -> AssistantMessageEventStream:
    """同步返回 stream，setup 在后台异步执行；失败以 error 事件结束。"""
    outer = AssistantMessageEventStream()

    async def _run() -> None:
        try:
            inner = await setup()
            async for event in inner:
                outer.push(event)
            result = await inner.result()
            outer.end(result=result)
        except BaseException as exc:
            message = _create_setup_error_message(model, exc)
            outer.push(
                ErrorEvent(
                    type="error",
                    reason="error",
                    error=message,
                )
            )
            outer.end(result=message)

    asyncio.get_running_loop().create_task(_run())
    return outer


def _merge_headers(
    base: Optional[Dict[str, Optional[str]]],
    override: Optional[Dict[str, Optional[str]]],
) -> Optional[Dict[str, Optional[str]]]:
    """大小写不敏感地合并 headers（对齐 TS mergeHeaders）。"""
    if not base and not override:
        return None
    merged: Dict[str, Optional[str]] = dict(base or {})
    for name, value in (override or {}).items():
        lower_name = name.lower()
        for existing_name in list(merged.keys()):
            if existing_name.lower() == lower_name:
                del merged[existing_name]
        merged[name] = value
    return merged


class _ProviderModelsStoreAdapter:
    """把 ModelsStore 适配为单个 provider 的 ProviderModelsStore。"""

    def __init__(self, store: ModelsStore, provider_id: str):
        self._store = store
        self._provider_id = provider_id

    async def read(self) -> Optional[ModelsStoreEntry]:
        return await self._store.read(self._provider_id)

    async def write(self, entry: ModelsStoreEntry) -> None:
        await self._store.write(self._provider_id, entry)

    async def delete(self) -> None:
        await self._store.delete(self._provider_id)


class Models:
    """Provider 运行时集合。

    与 TS ``Models`` 对齐：持有 provider 集合、CredentialStore、ModelsStore、
    AuthContext，暴露 ``stream()`` / ``stream_simple()`` / ``complete()`` /
    ``complete_simple()``、``getAuth()`` / ``checkAuth()`` /
    ``getAvailable()`` / ``login()`` / ``logout()`` / ``refresh()``。
    """

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

    def set_provider(self, provider: Provider) -> None:
        """按 ``provider.id`` 注册或替换 provider。"""
        self._providers[provider.id] = provider

    def delete_provider(self, provider_id: str) -> None:
        """删除指定 provider。"""
        self._providers.pop(provider_id, None)

    def clear_providers(self) -> None:
        """清空所有 provider。"""
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
    # Refresh
    # -----------------------------------------------------------------------

    async def refresh(
        self,
        allow_network: bool = True,
        force: bool = False,
        signal: Optional[AbortSignal] = None,
    ) -> Dict[str, Any]:
        """刷新所有动态 provider 的模型列表（对齐 TS Models.refresh）。"""
        errors: Dict[str, Exception] = {}
        refreshable = [
            p
            for p in self._providers.values()
            if getattr(p, "refresh_models", None) is not None
        ]

        async def _refresh_one(provider: Provider) -> None:
            if signal is not None and signal.aborted:
                return
            store = _ProviderModelsStoreAdapter(self._models_store, provider.id)
            stored = None
            try:
                stored = await self._read_credential(provider.id)
                credential = await self._resolve_refresh_credential(
                    provider, stored, allow_network, signal
                )
                if credential is None:
                    return
                await provider.refresh_models(
                    RefreshModelsContext(
                        credential=credential,
                        store=store,
                        allow_network=allow_network,
                        force=force,
                        signal=signal,
                    )
                )
            except Exception as exc:
                if signal is None or not signal.aborted:
                    errors[provider.id] = (
                        exc
                        if isinstance(exc, Exception)
                        else ModelsError(
                            "model_source",
                            f"Model refresh failed for {provider.id}",
                            exc,
                        )
                    )
                # 尝试离线恢复缓存
                try:
                    await provider.refresh_models(
                        RefreshModelsContext(
                            credential=stored,
                            store=store,
                            allow_network=False,
                            signal=signal,
                        )
                    )
                except Exception:
                    pass

        await asyncio.gather(*[_refresh_one(p) for p in refreshable])
        return {
            "aborted": bool(signal is not None and signal.aborted),
            "errors": errors,
        }

    async def _resolve_refresh_credential(
        self,
        provider: Provider,
        stored: Optional[Credential],
        allow_network: bool,
        signal: Optional[AbortSignal],
    ) -> Optional[Credential]:
        """为模型刷新解析 credential（对齐 TS resolveRefreshCredential）。"""
        if stored is not None and stored.type == "oauth":
            oauth = provider.auth.oauth if provider.auth else None
            if oauth is None:
                return None
            if not allow_network or getattr(stored, "expires", 0) > int(
                time.time() * 1000
            ):
                return stored
            if signal is not None and signal.aborted:
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

        api_key = provider.auth.apiKey if provider.auth else None
        if api_key is None:
            return None
        credential = stored if stored is not None and stored.type == "api_key" else None
        result = await api_key.resolve(
            {"ctx": self._auth_context, "credential": credential}
        )
        if result is None:
            return None
        return ApiKeyCredential(
            key=result.auth.get("apiKey"),
            env=result.env,
        )

    async def _read_credential(self, provider_id: str) -> Optional[Credential]:
        try:
            return await self._credential_store.read(provider_id)
        except Exception as exc:
            raise ModelsError(
                "auth", f"Credential store read failed for {provider_id}", exc
            )

    # -----------------------------------------------------------------------
    # Auth
    # -----------------------------------------------------------------------

    async def get_auth(
        self,
        provider_or_model: Union[str, Model],
        overrides: Optional[AuthResolutionOverrides] = None,
    ) -> Optional[Any]:
        """解析 provider 或 model 的鉴权（对齐 TS Models.getAuth）。"""
        provider_id = (
            provider_or_model
            if isinstance(provider_or_model, str)
            else provider_or_model.provider
        )
        provider = self._providers.get(provider_id)
        if provider is None or provider.auth is None:
            return None
        result = await resolve_provider_auth(
            provider_id,
            provider.auth,
            self._credential_store,
            self._auth_context,
            overrides,
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
    ) -> Optional[AuthCheck]:
        """检查 provider auth 是否配置（对齐 TS checkProviderAuth）。"""
        if credential is not None and credential.type == "oauth":
            return (
                AuthCheck(type="oauth", source="OAuth")
                if provider.auth and provider.auth.oauth
                else None
            )
        api_key = provider.auth.apiKey if provider.auth else None
        if api_key is None:
            return None
        if api_key.check:
            try:
                return await api_key.check(
                    {
                        "ctx": self._auth_context,
                        "credential": (
                            credential
                            if credential is not None and credential.type == "api_key"
                            else None
                        ),
                    }
                )
            except Exception as exc:
                raise ModelsError(
                    "auth",
                    f"API key auth check failed for provider {provider.id}",
                    exc,
                )
        resolution = await resolve_provider_auth(
            provider.id,
            provider.auth,
            self._credential_store,
            self._auth_context,
        )
        return (
            AuthCheck(type="api_key", source=resolution.source) if resolution else None
        )

    async def check_auth(self, provider_id: str) -> Optional[AuthCheck]:
        """检查 provider 是否已配置鉴权（不触发网络刷新）。"""
        provider = self._providers.get(provider_id)
        if provider is None:
            return None
        credential = await self._read_credential(provider_id)
        return await self._check_provider_auth(provider, credential)

    async def get_available(self, provider_id: Optional[str] = None) -> List[Model]:
        """返回已配置鉴权的 provider 的模型（对齐 TS Models.getAvailable）。"""
        providers = (
            [self._providers[provider_id]]
            if provider_id is not None and provider_id in self._providers
            else list(self._providers.values())
        )

        async def _check_one(
            provider: Provider,
        ) -> tuple[Provider, Optional[Credential], Optional[AuthCheck]]:
            credential = await self._read_credential(provider.id)
            auth = await self._check_provider_auth(provider, credential)
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
    ) -> Optional[Any]:
        """执行 provider 的登录流程并持久化 credential。"""
        provider = self._providers.get(provider_id)
        if provider is None:
            raise ModelsError("provider", f"Unknown provider: {provider_id}")
        if provider.auth is None:
            raise ModelsError("auth", f"{provider.name} has no auth configured")

        method = provider.auth.oauth if type == "oauth" else provider.auth.apiKey
        if method is None or method.login is None:
            raise ModelsError("auth", f"{provider.name} does not support {type} login")
        credential = await method.login(interaction)

        async def _set(_current: Optional[Credential]) -> Optional[Credential]:
            return credential

        try:
            await self._credential_store.modify(provider_id, _set)
        except Exception as exc:
            raise ModelsError(
                "auth", f"Credential store modify failed for {provider_id}", exc
            )
        return credential

    async def logout(self, provider_id: str) -> None:
        """删除 provider 的已存储 credential。"""
        try:
            await self._credential_store.delete(provider_id)
        except Exception as exc:
            raise ModelsError(
                "auth", f"Credential store delete failed for {provider_id}", exc
            )

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
                apiKey=options.api_key if options else None,
                env=options.env if options else None,
            ),
        )
        if resolution is None:
            raise ModelsError("auth", f"Provider is not configured: {model.provider}")

        auth = resolution.auth
        # 对齐 TS ??：仅当显式传入的 api_key 为 None 时才回落到 auth 解析结果
        api_key = (
            options.api_key
            if options is not None and options.api_key is not None
            else auth.get("apiKey")
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
        if auth.get("baseUrl"):
            request_model = model.model_copy(update={"base_url": auth["baseUrl"]})

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
            request_options = replace(options, **update)

        return request_model, request_options

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """流式调用模型（对齐 TS：同步返回，auth 异步解析）。"""
        provider = self._require_provider(model)
        if options is None:
            options = StreamOptions()

        async def _setup() -> AssistantMessageEventStream:
            request_model, request_options = await self._apply_auth(model, options)
            return provider.stream(request_model, context, request_options)

        return _lazy_stream(model, _setup)

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
        """简化的流式调用（对齐 TS：同步返回，auth 异步解析）。"""
        provider = self._require_provider(model)
        if options is None:
            options = SimpleStreamOptions()

        async def _setup() -> AssistantMessageEventStream:
            request_model, request_options = await self._apply_auth(model, options)
            return provider.stream_simple(request_model, context, request_options)

        return _lazy_stream(model, _setup)

    async def complete_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> AssistantMessage:
        """简化的非流式完成调用。"""
        return await self.stream_simple(model, context, options).result()


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
