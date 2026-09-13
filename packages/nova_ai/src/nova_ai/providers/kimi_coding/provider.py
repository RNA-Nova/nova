"""Kimi Coding provider 工厂。"""

from typing import cast

from nova_protocol import ProviderAuth

from ...auth.helpers import env_api_key_auth
from ...auth.oauth.kimi import kimi_oauth
from ...gateway import Provider, ProviderStreams, create_provider
from .models import KIMI_CODING_MODELS


def kimi_coding_provider() -> "Provider":
    """构造 Kimi Coding provider 实例。"""
    from ...api_impls import openai_completions

    return create_provider(
        id="kimi-coding",
        name="Kimi Coding",
        base_url="https://api.kimi.com/coding/v1",
        models=list(KIMI_CODING_MODELS.values()),
        # 模块结构上满足 ProviderStreams（导出 stream/stream_simple），
        # 静态检查不认模块对象，cast 收口
        api=cast(ProviderStreams, openai_completions),
        auth=ProviderAuth(
            api_key=env_api_key_auth("Kimi API key", ["KIMI_API_KEY"]),
            oauth=kimi_oauth,
        ),
    )
