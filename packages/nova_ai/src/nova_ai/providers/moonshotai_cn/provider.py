"""Moonshot AI CN provider 工厂。"""

from ...auth.helpers import env_api_key_auth
from ...gateway import create_provider
from ...types.auth import ProviderAuth
from .models import MOONSHOTAI_CN_MODELS


def moonshotai_cn_provider():
    """构造 Moonshot AI CN provider 实例。"""
    from ...api_impls import openai_completions

    return create_provider(
        id="moonshotai-cn",
        name="Moonshot AI CN",
        base_url="https://api.moonshot.cn/v1",
        models=list(MOONSHOTAI_CN_MODELS.values()),
        api=openai_completions,
        auth=ProviderAuth(
            apiKey=env_api_key_auth("Moonshot AI API key", ["MOONSHOT_API_KEY"])
        ),
    )
