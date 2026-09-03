"""Moonshot AI provider 工厂。"""

from ...auth.helpers import env_api_key_auth
from ...gateway import Provider, create_provider
from ...types.auth import ProviderAuth
from .models import MOONSHOTAI_MODELS


def moonshotai_provider() -> "Provider":
    """构造 Moonshot AI provider 实例。"""
    from ...api_impls import openai_completions

    return create_provider(
        id="moonshotai",
        name="Moonshot AI",
        base_url="https://api.moonshot.ai/v1",
        models=list(MOONSHOTAI_MODELS.values()),
        api=openai_completions,
        auth=ProviderAuth(
            api_key=env_api_key_auth("Moonshot AI API key", ["MOONSHOT_API_KEY"])
        ),
    )
