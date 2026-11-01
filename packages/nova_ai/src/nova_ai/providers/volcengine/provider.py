"""Volcengine provider 工厂。"""

from typing import cast

from nova_catalog.providers.volcengine import DEF as _knowledge
from nova_protocol import ProviderAuth

from ...auth.helpers import env_api_key_auth
from ...gateway import Provider, ProviderStreams, create_provider
from .catalog import BASE_URL, volcengine_fetch_models
from .models import VOLCENGINE_MODELS


def volcengine_provider() -> "Provider":
    """构造 Volcengine provider 实例（三件套刷新出生自带：
    ``catalog_source="models.dev"`` 公开站基底 + 凭据带 key 时方舟在售筛选）。"""
    from ...api_impls import openai_completions

    return create_provider(
        id="volcengine",
        name="Volcengine",
        base_url=BASE_URL,
        models=list(VOLCENGINE_MODELS.values()),
        # 模块结构上满足 ProviderStreams，cast 收口（见 kimi_coding）
        api=cast(ProviderStreams, openai_completions),
        auth=ProviderAuth(
            api_key=env_api_key_auth(
                "Volcengine API Key",
                ["VOLCENGINE_API_KEY"],
            ),
        ),
        fetch_models=volcengine_fetch_models,
        catalog_source="models.dev",
        catalog_knowledge=_knowledge,
    )
