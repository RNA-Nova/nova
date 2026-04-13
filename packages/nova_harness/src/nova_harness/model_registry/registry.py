# model_registry/registry.py
"""
核心层 - ModelRegistry 主类
"""
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Optional, List, Dict, Literal

from nova_ai import (
    get_models_by_provider,
    list_providers,
    Model,
    ModelCost,
    register_api_provider,
    reset_api_registry,
    Context,
    SimpleStreamOptions,
)

# 本地导入（resolve.py 已移至同级目录）
from ..config import get_agent_dir
from .storage import AuthStorage
from .resolve import resolve_config_value, resolve_headers
from .types import (
    ModelsConfig,
    ProviderOverride,
    ModelOverride,
    CustomModelsResult,
    ProviderConfigInput,
)
from .helpers import empty_custom_models_result, apply_model_override


class ModelRegistry:
    """
    Model registry - manages built-in and custom models, provides API key resolution.
    """

    def __init__(
        self,
        auth_storage: AuthStorage,
        models_json_path: Optional[str] = None
    ):
        self.auth_storage: AuthStorage = auth_storage
        self.models_json_path: str = models_json_path or os.path.join(
            get_agent_dir(), "models.json"
        )
        self.models: List[Model] = []
        self.custom_provider_api_keys: Dict[str, str] = {}
        self.registered_providers: Dict[str, ProviderConfigInput] = {}
        self.load_error: Optional[str] = None

        # 设置 fallback resolver 用于自定义 provider API keys
        def fallback_resolver(provider: str) -> Optional[str]:
            key_config = self.custom_provider_api_keys.get(provider)
            if key_config:
                return resolve_config_value(key_config)
            return None

        self.auth_storage.set_fallback_resolver(fallback_resolver)
        self._load_models()

    def refresh(self) -> None:
        """
        Reload models from disk (built-in + custom from models.json).
        """
        self.custom_provider_api_keys.clear()
        self.load_error = None

        # 重置动态 API 注册（已移除 OAuth）
        reset_api_registry()

        self._load_models()

        for provider_name, config in self.registered_providers.items():
            self._apply_provider_config(provider_name, config)

    def get_error(self) -> Optional[str]:
        """Get any error from loading models.json (undefined if no error)."""
        return self.load_error

    def _load_models(self) -> None:
        """Load models from disk (built-in + custom)."""
        # 加载自定义模型和覆盖
        if self.models_json_path and os.path.exists(self.models_json_path):
            result = self._load_custom_models(self.models_json_path)
        else:
            result = empty_custom_models_result()

        if result.error:
            self.load_error = result.error

        # 加载内置模型并应用覆盖
        built_in = self._load_built_in_models(result.overrides, result.model_overrides)

        # 已移除 OAuth modify_models 逻辑
        self.models = self._merge_custom_models(built_in, result.models)

    def _load_built_in_models(
        self,
        overrides: Dict[str, ProviderOverride],
        model_overrides: Dict[str, Dict[str, ModelOverride]]
    ) -> List[Model]:
        """Load built-in models and apply provider/model overrides."""
        models: List[Model] = []

        for provider in list_providers():
            provider_models = get_models_by_provider(provider)
            provider_override = overrides.get(provider)
            per_model_overrides = model_overrides.get(provider, {})

            for _, m in provider_models.items():
                model = m

                # 应用 provider 级别覆盖 (base_url, headers)
                if provider_override:
                    updates: Dict[str, object] = {}
                    if provider_override.base_url:
                        updates["base_url"] = provider_override.base_url
                    if provider_override.headers:
                        resolved_headers = resolve_headers(provider_override.headers)
                        if resolved_headers:
                            updates["headers"] = {**(model.headers or {}), **resolved_headers}
                    if updates:
                        model = replace(model, **updates)

                # 应用 per-model 覆盖
                mo = per_model_overrides.get(m.id)
                if mo:
                    model = apply_model_override(model, mo)

                models.append(model)

        return models

    def _merge_custom_models(
        self,
        built_in: List[Model],
        custom: List[Model]
    ) -> List[Model]:
        """
        Merge custom models into built-in list by provider+id (custom wins on conflicts).
        """
        merged = list(built_in)
        for custom_model in custom:
            existing_idx = next(
                (
                    i for i, m in enumerate(merged)
                    if m.provider == custom_model.provider and m.id == custom_model.id
                ),
                -1
            )
            if existing_idx >= 0:
                merged[existing_idx] = custom_model
            else:
                merged.append(custom_model)
        return merged

    def _load_custom_models(self, models_json_path: str) -> CustomModelsResult:
        """Load custom models and overrides from models.json."""
        try:
            path = Path(models_json_path)
            content = path.read_text(encoding="utf-8")
            config_data = json.loads(content)

            # 使用 mashumaro 验证和解析（替代原 Ajv）
            config = ModelsConfig.from_dict(config_data)

            # 额外业务逻辑验证
            self._validate_config(config)

            overrides: Dict[str, ProviderOverride] = {}
            model_overrides: Dict[str, Dict[str, ModelOverride]] = {}

            for provider_name, provider_config in config.providers.items():
                # Provider 覆盖配置 (base_url, headers, api_key)
                if provider_config.base_url or provider_config.headers or provider_config.api_key:
                    overrides[provider_name] = ProviderOverride(
                        base_url=provider_config.base_url,
                        headers=provider_config.headers,
                        api_key=provider_config.api_key
                    )

                # 存储 API key 用于 fallback resolver
                if provider_config.api_key:
                    self.custom_provider_api_keys[provider_name] = provider_config.api_key

                # 收集 per-model 覆盖
                if provider_config.model_overrides:
                    model_overrides[provider_name] = provider_config.model_overrides

            return CustomModelsResult(
                models=self._parse_models(config),
                overrides=overrides,
                model_overrides=model_overrides,
                error=None
            )

        except json.JSONDecodeError as e:
            return empty_custom_models_result(
                f"Failed to parse models.json: {e}\n\nFile: {models_json_path}"
            )
        except Exception as e:
            return empty_custom_models_result(
                f"Failed to load models.json: {e}\n\nFile: {models_json_path}"
            )

    def _validate_config(self, config: ModelsConfig) -> None:
        """Additional validation beyond schema (context_window > 0 等)."""
        for provider_name, provider_config in config.providers.items():
            has_provider_api = bool(provider_config.api)
            models = provider_config.models or []
            has_model_overrides = bool(provider_config.model_overrides)

            if not models:
                # Override-only 配置：需要 base_url 或 model_overrides
                if not provider_config.base_url and not has_model_overrides:
                    raise ValueError(
                        f'Provider {provider_name}: must specify "base_url", "model_overrides", or "models".'
                    )
            else:
                # 自定义模型：需要 base_url 和 api_key
                if not provider_config.base_url:
                    raise ValueError(
                        f'Provider {provider_name}: "base_url" is required when defining custom models.'
                    )
                if not provider_config.api_key:
                    raise ValueError(
                        f'Provider {provider_name}: "api_key" is required when defining custom models.'
                    )

            for model_def in models:
                has_model_api = bool(model_def.api)

                if not has_provider_api and not has_model_api:
                    raise ValueError(
                        f'Provider {provider_name}, model {model_def.id}: no "api" specified. '
                        'Set at provider or model level.'
                    )

                if not model_def.id:
                    raise ValueError(f'Provider {provider_name}: model missing "id"')

                if model_def.context_window is not None and model_def.context_window <= 0:
                    raise ValueError(
                        f'Provider {provider_name}, model {model_def.id}: invalid context_window'
                    )
                if model_def.max_tokens is not None and model_def.max_tokens <= 0:
                    raise ValueError(
                        f'Provider {provider_name}, model {model_def.id}: invalid max_tokens'
                    )

    def _parse_models(self, config: ModelsConfig) -> List[Model]:
        """Parse custom model definitions from config."""
        models: List[Model] = []

        for provider_name, provider_config in config.providers.items():
            model_defs = provider_config.models or []
            if not model_defs:
                continue

            # 存储 API key 配置
            if provider_config.api_key:
                self.custom_provider_api_keys[provider_name] = provider_config.api_key

            for model_def in model_defs:
                api = model_def.api or provider_config.api
                if not api:
                    continue

                # 合并 headers：provider headers 为基础，model headers 覆盖
                provider_headers = resolve_headers(provider_config.headers) or {}
                model_headers = resolve_headers(model_def.headers) or {}
                headers: Optional[Dict[str, str]] = None
                if provider_headers or model_headers:
                    headers = {**provider_headers, **model_headers}

                # 如果启用 auth_header，添加 Authorization
                if provider_config.auth_header and provider_config.api_key:
                    resolved_key = resolve_config_value(provider_config.api_key)
                    if resolved_key:
                        headers = {**(headers or {}), "Authorization": f"Bearer {resolved_key}"}

                # 应用默认值（针对本地模型如 Ollama, LM Studio）
                default_cost = ModelCost(input=0.0, output=0.0, cache_read=0.0, cache_write=0.0)

                # 确保 input 是 tuple（假设 nova_ai 的 Model.input 是 tuple）
                input_tuple: tuple[Literal["text", "image"], ...] = tuple(model_def.input) if model_def.input else ("text",)

                models.append(Model(
                    id=model_def.id,
                    name=model_def.name or model_def.id,
                    api=api,
                    provider=provider_name,
                    base_url=provider_config.base_url,  # 已验证存在
                    reasoning=model_def.reasoning or False,
                    input_types=input_tuple,
                    cost=model_def.cost or default_cost,
                    context_window=model_def.context_window or 128000,
                    max_tokens=model_def.max_tokens or 16384,
                    headers=headers,
                    compat=model_def.compat
                ))

        return models

    def get_all(self) -> List[Model]:
        """Get all models (built-in + custom)."""
        return self.models

    def get_available(self) -> List[Model]:
        """Get only models that have auth configured (fast check)."""
        return [m for m in self.models if self.auth_storage.has_auth(m.provider)]

    def find(self, provider: str, model_id: str) -> Optional[Model]:
        """Find a model by provider and ID."""
        return next(
            (m for m in self.models if m.provider == provider and m.id == model_id),
            None
        )

    async def get_api_key(self, model: Model) -> Optional[str]:
        """Get API key for a model."""
        return await self.auth_storage.get_api_key(model.provider)

    async def get_api_key_for_provider(self, provider: str) -> Optional[str]:
        """Get API key for a provider."""
        return await self.auth_storage.get_api_key(provider)

    def register_provider(self, provider_name: str, config: ProviderConfigInput) -> None:
        """
        Register a provider dynamically (from extensions).

        If provider has models: replaces all existing models for this provider.
        If provider has only base_url/headers: overrides existing models' URLs.
        """
        self.registered_providers[provider_name] = config
        self._apply_provider_config(provider_name, config)

    def unregister_provider(self, provider_name: str) -> None:
        """
        Unregister a previously registered provider.
        Removes the provider and reloads models from disk.
        """
        if provider_name not in self.registered_providers:
            return
        del self.registered_providers[provider_name]
        self.custom_provider_api_keys.pop(provider_name, None)
        self.refresh()

    def _apply_provider_config(
        self,
        provider_name: str,
        config: ProviderConfigInput
    ) -> None:
        """Apply provider configuration to registry."""
        # 已移除 OAuth 注册逻辑

        if config.stream_simple:
            if not config.api:
                raise ValueError(
                    f'Provider {provider_name}: "api" is required when registering stream_simple.'
                )

            # 包装函数以适配 API 签名
            def stream_wrapper(
                model: Model,
                context: Context,
                options: Optional[SimpleStreamOptions]
            ):
                return config.stream_simple(model, context, options)

            register_api_provider(
                {
                    "api": config.api,
                    "stream": stream_wrapper,
                    "stream_simple": config.stream_simple
                },
                f"provider:{provider_name}"
            )

        # 存储 API key
        if config.api_key:
            self.custom_provider_api_keys[provider_name] = config.api_key

        if config.models:
            # 全量替换：移除该 provider 的所有现有模型
            self.models = [m for m in self.models if m.provider != provider_name]

            if not config.base_url:
                raise ValueError(
                    f'Provider {provider_name}: "base_url" is required when defining models.'
                )
            if not config.api_key:
                raise ValueError(
                    f'Provider {provider_name}: "api_key" is required when defining models.'
                )

            for model_def in config.models:
                api = model_def.api or config.api
                if not api:
                    raise ValueError(
                        f'Provider {provider_name}, model {model_def.id}: no "api" specified.'
                    )

                # 合并 headers
                provider_headers = resolve_headers(config.headers) or {}
                model_headers = resolve_headers(model_def.headers) or {}
                headers: Optional[Dict[str, str]] = None
                if provider_headers or model_headers:
                    headers = {**provider_headers, **model_headers}

                if config.auth_header and config.api_key:
                    resolved_key = resolve_config_value(config.api_key)
                    if resolved_key:
                        headers = {**(headers or {}), "Authorization": f"Bearer {resolved_key}"}

                input_tuple: tuple[Literal["text", "image"], ...] = tuple(model_def.input) if model_def.input else ("text",)

                self.models.append(Model(
                    id=model_def.id,
                    name=model_def.name,
                    api=api,
                    provider=provider_name,
                    base_url=config.base_url,
                    reasoning=model_def.reasoning or False,
                    input_types=input_tuple,
                    cost=model_def.cost or ModelCost(),
                    context_window=model_def.context_window or 128000,
                    max_tokens=model_def.max_tokens or 16384,
                    headers=headers,
                    compat=model_def.compat
                ))

        elif config.base_url:
            # 仅覆盖：更新现有模型的 base_url/headers
            resolved_headers = resolve_headers(config.headers)
            new_models: List[Model] = []
            for m in self.models:
                if m.provider != provider_name:
                    new_models.append(m)
                else:
                    new_headers = m.headers
                    if resolved_headers:
                        new_headers = {**(m.headers or {}), **resolved_headers}
                    new_models.append(replace(
                        m,
                        base_url=config.base_url or m.base_url,
                        headers=new_headers
                    ))
            self.models = new_models