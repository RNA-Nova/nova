"""Moonshot AI CN provider 工厂。"""

from typing import cast

from nova_protocol import ProviderAuth

from ...auth.helpers import env_api_key_auth
from ...gateway import Provider, ProviderStreams, create_provider
from .models import MOONSHOTAI_CN_MODELS


def moonshotai_cn_provider() -> "Provider":
    """构造 Moonshot AI CN provider 实例。"""
    from ...api_impls import openai_completions

    return create_provider(
        id="moonshotai-cn",
        name="Moonshot AI CN",
        base_url="https://api.moonshot.cn/v1",
        models=list(MOONSHOTAI_CN_MODELS.values()),
        # 模块结构上满足 ProviderStreams，cast 收口（见 kimi_coding）
        api=cast(ProviderStreams, openai_completions),
        auth=ProviderAuth(
            api_key=env_api_key_auth("Moonshot AI API key", ["MOONSHOT_API_KEY"])
        ),
    )
