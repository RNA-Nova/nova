"""Moonshot AI provider 工厂。"""

from typing import cast

from nova_protocol import ProviderAuth

from ...auth.helpers import env_api_key_auth
from ...gateway import Provider, ProviderStreams, create_provider
from .models import MOONSHOTAI_MODELS


def moonshotai_provider() -> "Provider":
    """构造 Moonshot AI provider 实例。"""
    from ...api_impls import openai_completions

    return create_provider(
        id="moonshotai",
        name="Moonshot AI",
        base_url="https://api.moonshot.ai/v1",
        models=list(MOONSHOTAI_MODELS.values()),
        # 模块结构上满足 ProviderStreams，cast 收口（见 kimi_coding）
        api=cast(ProviderStreams, openai_completions),
        auth=ProviderAuth(
            api_key=env_api_key_auth("Moonshot AI API key", ["MOONSHOT_API_KEY"])
        ),
    )
