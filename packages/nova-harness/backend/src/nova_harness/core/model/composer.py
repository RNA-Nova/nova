# model_runtime/composer.py
"""Provider 组合：内置 provider + models.json 覆盖 + 扩展注册三层合成。

对齐 TS ``core/provider-composer.ts``：

- **credential-blind 模型层**：api_key 与 ``Authorization`` 头不烘焙进
  ``Model`` 对象，全部在请求时经 ``ProviderAuth`` 解析；
- **请求时鉴权**：stored credential → models.json/extension 配置的 key →
  内置 provider 自身的环境变量链；
- **model_overrides 是最顶层用户配置**，对所有模型（含扩展注册的模型）生效。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Literal, Optional

from nova_ai import (
    Model,
    ModelCost,
    Provider,
    create_provider,
)
from nova_ai.api_impls import openai_completions
from nova_ai.gateway.provider import ProviderStreams
from nova_ai.types.auth import (
    ApiKeyAuth,
    ApiKeyCredential,
    AuthCheck,
    AuthResult,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
)
from nova_ai.types.enums import KnownApi
from nova_ai.types.messages import Context
from nova_harness.core.config.resolve import (
    get_config_value_env_var_names,
    is_command_config_value,
    is_config_value_configured,
    resolve_config_value_or_throw,
    resolve_headers_or_throw,
)
from nova_harness.core.model.helpers import (
    apply_model_override,
    merge_compat,
)
from nova_harness.core.types.model import (
    ExtensionOAuthConfig,
    ModelDefinition,
    ProviderConfig,
    ProviderConfigInput,
)

# 当前唯一完整的协议实现；新增协议时在此登记
_API_IMPLS: Dict[str, Any] = {
    "openai-completions": openai_completions,
}


def get_api_impl(api: Optional[str]) -> Optional[Any]:
    """按 api 名称查找协议实现模块。"""
    if api is None:
        return None
    return _API_IMPLS.get(api)


def _api_name(api: Any) -> str:
    # Api 是 Union[KnownApi, str]，isinstance 判 Union 对任意 str 都为真，
    # 必须以 KnownApi 枚举成员为准
    return api.value if isinstance(api, KnownApi) else str(api)


# ---------------------------------------------------------------------------
# 模型层合成
# ---------------------------------------------------------------------------


def model_from_json(
    provider_id: str,
    definition: ModelDefinition,
    provider_config: Optional[ProviderConfig],
    defaults: Optional[Model],
) -> Model:
    """从 models.json 的模型定义构造 Model（credential-blind）。

    headers 只包含显式配置的静态头（组合时解析一次）；
    ``Authorization`` 由请求时的 auth 解析按 ``auth_header`` 注入。
    """
    api = definition.api or (provider_config.api if provider_config else None)
    if api is None and defaults is not None:
        api = _api_name(defaults.api)
    if not api:
        raise ValueError(
            f'Provider {provider_id}, model {definition.id}: no "api" specified. '
            "Set at provider or model level."
        )

    base_url = (
        definition.base_url
        or (provider_config.base_url if provider_config else None)
        or (defaults.base_url if defaults is not None else None)
    )
    if not base_url:
        raise ValueError(
            f'Provider {provider_id}: "base_url" is required when defining custom models.'
        )

    if definition.context_window is not None and definition.context_window <= 0:
        raise ValueError(
            f"Provider {provider_id}, model {definition.id}: invalid context_window"
        )
    if definition.max_tokens is not None and definition.max_tokens <= 0:
        raise ValueError(
            f"Provider {provider_id}, model {definition.id}: invalid max_tokens"
        )

    # per-model headers 不烘焙进 Model：原始模板留在配置里，
    # 请求时由 ModelRuntime 的 model_headers_resolver 解析注入
    # （对齐 TS modelFromJson 的 headers: undefined）
    default_cost = ModelCost(input=0.0, output=0.0, cache_read=0.0, cache_write=0.0)
    input_tuple: tuple[Literal["text", "image"], ...] = (
        tuple(definition.input) if definition.input else ("text",)
    )

    return Model(
        id=definition.id,
        name=definition.name or definition.id,
        api=api,
        provider=provider_id,
        base_url=base_url,
        reasoning=definition.reasoning or False,
        input_types=input_tuple,
        cost=definition.cost or default_cost,
        context_window=definition.context_window or 128000,
        max_tokens=definition.max_tokens or 16384,
        headers=None,
        compat=merge_compat(
            provider_config.compat if provider_config else None, definition.compat
        ),
        thinking_level_map=definition.thinking_level_map
        or (provider_config.thinking_level_map if provider_config else None),
    )


def apply_models_json(
    provider_id: str,
    base_models: List[Model],
    config: Optional[ProviderConfig],
) -> List[Model]:
    """把 models.json 的 provider 配置应用到基础模型列表（对齐 TS applyModelsJson）。"""
    if config is None:
        return list(base_models)

    has_overrides = bool(config.model_overrides)
    if (
        not config.models
        and not config.base_url
        and not config.headers
        and config.compat is None
        and not has_overrides
        and not config.api_key
        and config.auth_header is None
        and config.thinking_level_map is None
    ):
        raise ValueError(
            f'Provider {provider_id}: must specify "base_url", "headers", "compat", '
            '"model_overrides", or "models".'
        )

    models: List[Model] = []
    for model in base_models:
        updates: Dict[str, object] = {}
        if config.base_url:
            updates["base_url"] = config.base_url
        if config.compat is not None:
            updates["compat"] = merge_compat(model.compat, config.compat)
        if config.thinking_level_map is not None:
            updates["thinking_level_map"] = config.thinking_level_map
        models.append(model.model_copy(update=updates) if updates else model)

    for definition in config.models or []:
        existing_idx = next(
            (i for i, m in enumerate(models) if m.id == definition.id), -1
        )
        defaults = (
            models[existing_idx]
            if existing_idx >= 0
            else (models[0] if models else None)
        )
        model = model_from_json(provider_id, definition, config, defaults)
        if existing_idx >= 0:
            models[existing_idx] = model
        else:
            models.append(model)
    return models


def apply_extension(
    provider_id: str,
    models: List[Model],
    extension: Optional[ProviderConfigInput],
) -> List[Model]:
    """把扩展注册的配置应用到模型列表（对齐 TS applyExtension）。

    TS 的 ``ProviderConfigInput`` 没有 compat/thinking_level_map 字段；
    Python 保留这两个字段（与 models.json 的 ProviderConfig 对齐），
    在无 models 替换时作为整表覆盖合并。
    """
    if extension is None:
        return list(models)
    if not extension.models:
        updates_per_model: List[Model] = []
        for m in models:
            updates: Dict[str, object] = {}
            if extension.base_url:
                updates["base_url"] = extension.base_url
            if extension.compat is not None:
                updates["compat"] = merge_compat(m.compat, extension.compat)
            if extension.thinking_level_map is not None:
                updates["thinking_level_map"] = extension.thinking_level_map
            updates_per_model.append(m.model_copy(update=updates) if updates else m)
        return updates_per_model

    result: List[Model] = []
    for definition in extension.models:
        defaults = next((m for m in models if m.id == definition.id), None)
        if defaults is None and models:
            defaults = models[0]
        api = definition.api or extension.api
        if api is None and defaults is not None:
            api = _api_name(defaults.api)
        if not api:
            raise ValueError(
                f'Provider {provider_id}, model {definition.id}: no "api" specified.'
            )
        base_url = (
            definition.base_url
            or extension.base_url
            or (defaults.base_url if defaults is not None else None)
        )
        if not base_url:
            raise ValueError(
                f'Provider {provider_id}: "base_url" is required when defining models.'
            )
        # 扩展模型定义全量替换：以定义为准，缺省字段回落 defaults；
        # per-model headers 同 models.json 路径，不在此烘焙（请求时解析）
        result.append(
            Model(
                id=definition.id,
                name=definition.name or definition.id,
                api=api,
                provider=provider_id,
                base_url=base_url,
                reasoning=definition.reasoning or False,
                input_types=(
                    tuple(definition.input)
                    if definition.input
                    else (defaults.input_types if defaults is not None else ("text",))
                ),
                cost=definition.cost
                or (defaults.cost if defaults is not None else ModelCost()),
                context_window=definition.context_window
                or (defaults.context_window if defaults is not None else 128000),
                max_tokens=definition.max_tokens
                or (defaults.max_tokens if defaults is not None else 16384),
                headers=None,
                compat=merge_compat(extension.compat, definition.compat)
                or (defaults.compat if defaults is not None else None),
                thinking_level_map=definition.thinking_level_map
                or extension.thinking_level_map
                or (defaults.thinking_level_map if defaults is not None else None),
            )
        )
    return result


def compose_models(
    provider_id: str,
    base: Optional[Provider],
    config: Optional[ProviderConfig],
    extension: Optional[ProviderConfigInput],
) -> List[Model]:
    """三层合成模型列表，并在最顶层应用 model_overrides。"""
    base_models = base.get_models() if base is not None else []
    models = apply_extension(
        provider_id, apply_models_json(provider_id, base_models, config), extension
    )
    overrides = (config.model_overrides or {}) if config else {}
    if overrides:
        models = [
            apply_model_override(m, overrides[m.id]) if m.id in overrides else m
            for m in models
        ]
    return models


# ---------------------------------------------------------------------------
# Auth 层合成
# ---------------------------------------------------------------------------


def _configured_api_key(
    config: Optional[ProviderConfig], extension: Optional[ProviderConfigInput]
) -> Optional[str]:
    if extension is not None and extension.api_key is not None:
        return extension.api_key
    return config.api_key if config is not None else None


def _configured_headers(
    config: Optional[ProviderConfig], extension: Optional[ProviderConfigInput]
) -> Optional[Dict[str, str]]:
    headers: Dict[str, str] = {}
    if config is not None and config.headers:
        headers.update(config.headers)
    if extension is not None and extension.headers:
        headers.update(extension.headers)
    return headers or None


def _configured_auth_header(
    config: Optional[ProviderConfig], extension: Optional[ProviderConfigInput]
) -> bool:
    if extension is not None and extension.auth_header is not None:
        return extension.auth_header
    if config is not None and config.auth_header is not None:
        return config.auth_header
    return False


async def _config_context_env(
    values: Any,
    ctx: Any,
    explicit: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, str]]:
    """收集配置值中引用的 env 变量值（对齐 TS configContextEnv）。

    通过异步 ``ctx.env`` 读取，使自定义 AuthContext 下的 ``$VAR`` 引用
    也能正确解析；``explicit``（credential.env / result.env）优先。
    """
    env: Dict[str, str] = dict(explicit or {})
    for value in values or []:
        if not isinstance(value, str):
            continue
        for name in get_config_value_env_var_names(value):
            if name in env:
                continue
            resolved = await ctx.env(name)
            if resolved:
                env[name] = resolved
    return env or None


def _with_configured_auth(
    result: AuthResult,
    provider_id: str,
    raw_headers: Optional[Dict[str, str]],
    auth_header: bool,
    env: Optional[Dict[str, str]] = None,
) -> AuthResult:
    """给解析结果合并配置头，并按需注入 Authorization（对齐 TS withConfiguredAuth）。"""
    headers = resolve_headers_or_throw(raw_headers, f'provider "{provider_id}"', env)
    merged: Dict[str, str] = {
        **(result.auth.get("headers") or {}),
        **(headers or {}),
    }
    if auth_header:
        api_key = result.auth.get("apiKey")
        if not api_key:
            raise ValueError("authHeader requires a resolved API key")
        merged["Authorization"] = f"Bearer {api_key}"
    auth = dict(result.auth)
    if merged:
        auth["headers"] = merged
    elif "headers" in auth:
        del auth["headers"]
    return AuthResult(auth=auth, env=result.env, source=result.source)


def compose_api_key_auth(
    provider_id: str,
    base_auth: Optional[ProviderAuth],
    config: Optional[ProviderConfig],
    extension: Optional[ProviderConfigInput],
    extension_oauth: Optional[ExtensionOAuthConfig] = None,
) -> Optional[ApiKeyAuth]:
    """合成 API key 鉴权（对齐 TS composeApiKeyAuth）。"""
    inherited = base_auth.apiKey if base_auth is not None else None
    raw_key = _configured_api_key(config, extension)
    has_oauth = extension_oauth is not None or (
        base_auth is not None and base_auth.oauth is not None
    )
    # OAuth-only 的 provider 不伪造 api key 登录方式
    if inherited is None and raw_key is None and has_oauth:
        return None

    raw_headers = _configured_headers(config, extension)
    auth_header = _configured_auth_header(config, extension)

    async def resolve(input: Dict[str, Any]) -> Optional[AuthResult]:
        ctx = input["ctx"]
        credential: Optional[ApiKeyCredential] = input.get("credential")

        result: Optional[AuthResult]
        if credential is not None:
            if inherited is not None:
                result = await inherited.resolve({"ctx": ctx, "credential": credential})
            elif credential.key:
                result = AuthResult(
                    auth={"apiKey": credential.key},
                    env=credential.env,
                    source="stored credential",
                )
            else:
                result = None
        elif raw_key is not None:
            # 解析失败（如 env 引用缺失）直接抛错，
            # 错误经 ModelsError 进入流式错误事件，而非静默 401
            key_env = await _config_context_env([raw_key], ctx)
            key = resolve_config_value_or_throw(
                raw_key, f'API key for provider "{provider_id}"', key_env
            )
            if inherited is not None:
                result = await inherited.resolve(
                    {"ctx": ctx, "credential": ApiKeyCredential(key=key)}
                )
            else:
                result = AuthResult(auth={"apiKey": key}, source="configured API key")
        elif inherited is not None:
            result = await inherited.resolve(input)
        else:
            result = None

        if result is None:
            return None
        # 对齐 TS：header 解析时收集 credential.env / result.env 及
        # header 值中引用的 env 变量（经 AuthContext 读取）
        explicit_env: Dict[str, str] = {
            **((credential.env if credential is not None else None) or {}),
            **(result.env or {}),
        }
        header_env = await _config_context_env(
            list((raw_headers or {}).values()), ctx, explicit_env
        )
        return _with_configured_auth(
            result, provider_id, raw_headers, auth_header, header_env
        )

    async def check(input: Dict[str, Any]) -> Optional[AuthCheck]:
        credential: Optional[ApiKeyCredential] = input.get("credential")
        if credential is not None:
            if inherited is not None and inherited.check is not None:
                return await inherited.check(input)
            if credential.key:
                return AuthCheck(type="api_key", source="stored credential")
            resolved = await inherited.resolve(input) if inherited is not None else None
            return (
                AuthCheck(type="api_key", source=resolved.source) if resolved else None
            )
        if raw_key is not None:
            # 命令型配置推迟到请求时执行，检查阶段视为已配置
            if is_command_config_value(raw_key):
                return AuthCheck(type="api_key", source="configured API key")
            if is_config_value_configured(raw_key):
                return AuthCheck(type="api_key", source="configured API key")
            return None
        if inherited is not None and inherited.check is not None:
            return await inherited.check(input)
        resolved = await inherited.resolve(input) if inherited is not None else None
        return AuthCheck(type="api_key", source=resolved.source) if resolved else None

    async def login(interaction: Any) -> ApiKeyCredential:
        if inherited is not None and inherited.login is not None:
            return await inherited.login(interaction)
        from nova_ai.types.auth import AuthPrompt

        key = await interaction.prompt(
            AuthPrompt(type="secret", message="Enter API key")
        )
        return ApiKeyCredential(key=key)

    name = inherited.name if inherited is not None else "API key"
    return ApiKeyAuth(name=name, resolve=resolve, login=login, check=check)


def adapt_oauth(config: ExtensionOAuthConfig) -> OAuthAuth:
    """把扩展的 OAuth 配置适配为 nova_ai 的 ``OAuthAuth``（对齐 TS adaptOAuth）。"""

    async def login(interaction: Any) -> OAuthCredential:
        credential = await config.login(interaction)
        if isinstance(credential, OAuthCredential):
            return credential
        return OAuthCredential.model_validate({**dict(credential), "type": "oauth"})

    async def refresh(
        credential: OAuthCredential, signal: Optional[Any] = None
    ) -> OAuthCredential:
        updated = await config.refresh_token(credential)
        if isinstance(updated, OAuthCredential):
            return updated
        return OAuthCredential.model_validate({**dict(updated), "type": "oauth"})

    async def to_auth(credential: OAuthCredential) -> Dict[str, Any]:
        return {"apiKey": config.get_api_key(credential)}

    return OAuthAuth(
        name=config.name,
        login=login,
        refresh=refresh,
        toAuth=to_auth,
    )


def compose_oauth_auth(
    provider_id: str,
    base_auth: Optional[ProviderAuth],
    config: Optional[ProviderConfig],
    extension: Optional[ProviderConfigInput],
    extension_oauth: Optional[ExtensionOAuthConfig] = None,
) -> Optional[OAuthAuth]:
    """合成 OAuth 鉴权：扩展 OAuth 优先，否则保留内置实现；

    toAuth 统一包装配置头（对齐 TS composeOAuthAuth）。
    """
    if extension_oauth is not None:
        oauth = adapt_oauth(extension_oauth)
    elif base_auth is not None:
        oauth = base_auth.oauth
    else:
        oauth = None
    if oauth is None:
        return None

    raw_headers = _configured_headers(config, extension)
    auth_header = _configured_auth_header(config, extension)

    async def to_auth(credential: Any) -> Dict[str, Any]:
        auth = await oauth.toAuth(credential)
        result = _with_configured_auth(
            AuthResult(auth=auth),
            provider_id,
            raw_headers,
            auth_header,
            getattr(credential, "env", None) or None,
        )
        return result.auth

    return OAuthAuth(
        name=oauth.name,
        login=oauth.login,
        refresh=oauth.refresh,
        toAuth=to_auth,
        loginLabel=oauth.loginLabel,
    )


# ---------------------------------------------------------------------------
# 流式调度合成
# ---------------------------------------------------------------------------


class _ComposedStreams:
    """组合 provider 的流式调度（对齐 TS composeModelProvider 的 streamWith）。

    优先级：扩展 stream_fn（api 匹配时）→ 内置 provider（支持该 api 时）→
    按 model.api 登记的协议实现。
    """

    def __init__(
        self,
        provider_id: str,
        base: Optional[Provider],
        extension_api: Optional[str],
        stream_fn: Optional[Callable[..., Any]],
    ) -> None:
        self._provider_id = provider_id
        self._base = base
        self._extension_api = extension_api
        self._stream_fn = stream_fn

    def _base_supports(self, model: Model) -> bool:
        if self._base is None:
            return False
        model_api = _api_name(model.api)
        return any(_api_name(m.api) == model_api for m in self._base.get_models())

    def _dispatch(self, model: Model, context: Context, options: Any, simple: bool):
        if (
            self._stream_fn is not None
            and self._extension_api is not None
            and _api_name(model.api) == self._extension_api
        ):
            return self._stream_fn(model, context, options)
        if self._base_supports(model):
            if simple:
                return self._base.stream_simple(model, context, options)
            return self._base.stream(model, context, options)
        impl = get_api_impl(_api_name(model.api))
        if impl is None:
            # 无协议实现：委托给空 provider 产生标准错误流
            fallback = create_provider(
                id=self._provider_id, name=self._provider_id, models=[model]
            )
            if simple:
                return fallback.stream_simple(model, context, options)
            return fallback.stream(model, context, options)
        if simple:
            return impl.stream_simple(model, context, options)
        return impl.stream(model, context, options)

    def stream(self, model: Model, context: Context, options: Any = None):
        return self._dispatch(model, context, options, simple=False)

    def stream_simple(self, model: Model, context: Context, options: Any = None):
        return self._dispatch(model, context, options, simple=True)


# ---------------------------------------------------------------------------
# 顶层组合
# ---------------------------------------------------------------------------


def validate_extension_provider(
    provider_id: str,
    base: Optional[Provider],
    config: Optional[ProviderConfig],
    extension: ProviderConfigInput,
    stream_fn: Optional[Callable[..., Any]],
) -> None:
    """注册前校验：结构错误必须在不触碰现有注册的情况下抛出。"""
    if stream_fn is not None and not extension.api:
        raise ValueError(
            f'Provider {provider_id}: "api" is required when registering stream_fn.'
        )
    compose_models(provider_id, base, config, extension)


class _ComposedProvider(Provider):
    """组合 provider（对齐 TS ``composeModelProvider`` 的返回）。

    与静态 ``Provider`` 的区别：

    - ``get_models()`` 每次调用时基于 base 的**当前**模型列表重新合成，
      动态刷新（base.refresh_models / 扩展 refresh_models_fn）到达的新模型
      也会自动经过 models.json 覆盖、扩展替换与 model_overrides；
    - ``refresh_models`` 仅在有刷新能力时存在（实例属性遮蔽为 None），
      供 ``Models.refresh`` 的 duck-type 过滤识别；
    - 扩展 OAuth 的 ``modify_models`` 在拿到 OAuth credential 后生效。
    """

    def __init__(
        self,
        provider_id: str,
        name: str,
        base_url: Optional[str],
        base: Optional[Provider],
        config: Optional[ProviderConfig],
        extension: Optional[ProviderConfigInput],
        auth: Optional[ProviderAuth],
        stream_fn: Optional[Callable[..., Any]],
        refresh_models_fn: Optional[Callable[..., Any]],
        extension_oauth: Optional[ExtensionOAuthConfig],
    ) -> None:
        super().__init__(
            id=provider_id,
            name=name,
            base_url=base_url,
            headers=base.headers if base is not None else None,
            # get_models 为 live 计算，静态列表不参与
            models=[],
            api_impl=_ComposedStreams(
                provider_id,
                base,
                extension.api if extension is not None else None,
                stream_fn,
            ),
            auth=auth,
            filter_models=base.filter_models if base is not None else None,
        )
        self._base = base
        self._config = config
        self._extension = extension
        self._refresh_models_fn = refresh_models_fn
        self._modify_models = (
            extension_oauth.modify_models if extension_oauth is not None else None
        )
        self._refreshed_extension_models: Optional[List[ModelDefinition]] = None
        self._extension_oauth_credential: Optional[OAuthCredential] = None

        base_refresh = getattr(base, "refresh_models", None) if base else None
        if (
            base_refresh is None
            and refresh_models_fn is None
            and self._modify_models is None
        ):
            # 无刷新能力：实例级遮蔽，让 Models.refresh 的 getattr 过滤生效
            self.refresh_models = None  # type: ignore[assignment]

    def _current_extension(self) -> Optional[ProviderConfigInput]:
        if self._extension is not None and self._refreshed_extension_models:
            return self._extension.model_copy(
                update={"models": self._refreshed_extension_models}
            )
        return self._extension

    def get_models(self) -> List[Model]:
        models = compose_models(
            self.id, self._base, self._config, self._current_extension()
        )
        if self._extension_oauth_credential is not None and self._modify_models:
            models = self._modify_models(models, self._extension_oauth_credential)
        return models

    async def refresh_models(self, context: Any) -> None:
        if self._base is not None:
            base_refresh = getattr(self._base, "refresh_models", None)
            if base_refresh is not None:
                await base_refresh(context)
        if self._refresh_models_fn is not None:
            refreshed = await self._refresh_models_fn(context)
            signal = getattr(context, "signal", None)
            if not (signal is not None and signal.aborted):
                # 发布前先校验，非法结构不污染线上列表
                apply_extension(
                    self.id,
                    apply_models_json(
                        self.id,
                        self._base.get_models() if self._base else [],
                        self._config,
                    ),
                    (
                        self._extension.model_copy(update={"models": refreshed})
                        if self._extension is not None
                        else ProviderConfigInput(models=refreshed)
                    ),
                )
                self._refreshed_extension_models = list(refreshed)
        credential = getattr(context, "credential", None)
        self._extension_oauth_credential = (
            credential
            if credential is not None and getattr(credential, "type", None) == "oauth"
            else None
        )


def compose_provider(
    provider_id: str,
    base: Optional[Provider],
    config: Optional[ProviderConfig],
    extension: Optional[ProviderConfigInput],
    stream_fn: Optional[Callable[..., Any]] = None,
    refresh_models_fn: Optional[Callable[..., Any]] = None,
    oauth: Optional[ExtensionOAuthConfig] = None,
) -> Provider:
    """把内置 provider、models.json 配置与扩展注册合成为一个 Provider。"""
    base_auth = base.auth if base is not None else None
    api_key_auth = compose_api_key_auth(
        provider_id, base_auth, config, extension, oauth
    )
    oauth_auth = compose_oauth_auth(provider_id, base_auth, config, extension, oauth)
    auth: Optional[ProviderAuth] = None
    if api_key_auth is not None or oauth_auth is not None:
        auth = ProviderAuth(apiKey=api_key_auth, oauth=oauth_auth)

    name = provider_id
    if extension is not None and extension.name:
        name = extension.name
    elif config is not None and config.name:
        name = config.name
    elif base is not None:
        name = base.name
    elif oauth is not None:
        name = oauth.name

    base_url = (
        (extension.base_url if extension is not None else None)
        or (config.base_url if config is not None else None)
        or (base.base_url if base is not None else None)
    )

    provider = _ComposedProvider(
        provider_id,
        name,
        base_url,
        base,
        config,
        extension,
        auth,
        stream_fn,
        refresh_models_fn,
        oauth,
    )
    # 组合期即校验一次（对齐 TS：结构错误立即暴露，而不是等到首次读取）
    provider.get_models()
    return provider


__all__ = [
    "adapt_oauth",
    "apply_extension",
    "apply_models_json",
    "compose_models",
    "compose_provider",
    "get_api_impl",
    "model_from_json",
    "validate_extension_provider",
]
