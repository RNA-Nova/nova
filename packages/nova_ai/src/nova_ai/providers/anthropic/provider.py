"""Anthropic provider 工厂。"""

from typing import cast

from nova_protocol import ProviderAuth

from ...auth.helpers import env_api_key_auth
from ...gateway import Provider, ProviderStreams, create_provider
from .models import ANTHROPIC_MODELS


def anthropic_provider() -> "Provider":
    """构造 Anthropic provider 实例（nova_ai 静态出厂；``catalog_source="pi.dev"``
    供 harness 组合时六件套包装刷新）。"""
    from ...api_impls import anthropic_messages

    return create_provider(
        id="anthropic",
        name="Anthropic",
        base_url="https://api.anthropic.com",
        models=list(ANTHROPIC_MODELS.values()),
        # 模块结构上满足 ProviderStreams，cast 收口（见 kimi_coding）
        api=cast(ProviderStreams, anthropic_messages),
        auth=ProviderAuth(
            # Anthropic：ANTHROPIC_OAUTH_TOKEN 优先于 ANTHROPIC_API_KEY
            # （env 映射归 utils/env.py）
            api_key=env_api_key_auth("Anthropic API key", ["ANTHROPIC_API_KEY"])
        ),
        # 运行时 pi.dev 全供（api/级别表/compat）——无运行时 corrections；
        # 种子知识归 scripts 的 BUILD_EXTRAS
        catalog_source="pi.dev",
    )
