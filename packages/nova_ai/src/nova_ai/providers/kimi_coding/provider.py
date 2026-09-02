"""Kimi Coding provider 工厂。"""

from ...auth.helpers import env_api_key_auth
from ...auth.oauth.kimi import kimi_oauth
from ...gateway import create_provider
from ...types.auth import ProviderAuth
from .models import KIMI_CODING_MODELS


def kimi_coding_provider():
    """构造 Kimi Coding provider 实例。"""
    from ...api_impls import openai_completions

    return create_provider(
        id="kimi-coding",
        name="Kimi Coding",
        base_url="https://api.kimi.com/coding/v1",
        models=list(KIMI_CODING_MODELS.values()),
        api=openai_completions,
        auth=ProviderAuth(
            apiKey=env_api_key_auth("Kimi API key", ["KIMI_API_KEY"]),
            oauth=kimi_oauth,
        ),
    )
