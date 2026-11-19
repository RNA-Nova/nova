"""配置 → 长期 ModelsManager 与 Provider 注册表。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from nova_ai import ModelsManager
from nova_ai.api_impls import openai_completions
from nova_ai.auth import ProviderAuth, env_api_key_auth
from nova_ai.gateway import create_provider
from nova_ai.providers import builtin_providers

from .config import UpstreamConfig


def build_models_manager(upstreams: list[UpstreamConfig]) -> ModelsManager:
    builtin = {provider.id: provider for provider in builtin_providers()}
    manager = ModelsManager()
    for config in upstreams:
        auth = (
            ProviderAuth(
                api_key=env_api_key_auth(config.provider, [config.api_key_env])
            )
            if config.api_key_env
            else None
        )
        if config.models:
            provider = create_provider(
                id=config.provider,
                name=config.name or config.provider,
                base_url=config.base_url,
                models=config.models,
                api=cast(Any, openai_completions),
                auth=auth,
            )
        else:
            try:
                provider = builtin[config.provider]
            except KeyError as error:
                raise ValueError(
                    f"Unknown built-in provider: {config.provider}"
                ) from error
            updates = {}
            if config.name is not None:
                updates["name"] = config.name
            if config.base_url is not None:
                updates["base_url"] = config.base_url
            if auth is not None:
                updates["auth"] = auth
            if updates:
                provider = replace(provider, **updates)
        manager.set_provider(provider)
    return manager


__all__ = ["build_models_manager"]
