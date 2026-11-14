"""Volcengine provider 工厂。"""

from typing import cast

from nova_ai.auth import ProviderAuth
from nova_ai.catalog.providers.volcengine import (
    PINNED_MODEL_IDS,
    materialize_volcengine_fields,
)

from ...auth.helpers import env_api_key_auth
from ...gateway import Provider, ProviderStreams, create_provider
from .catalog import BASE_URL, ark_personalize
from .models import VOLCENGINE_MODELS


def volcengine_provider() -> "Provider":
    """构造 Volcengine provider 实例。

    personalized 刷新形态（``create_provider`` 装配）：models.dev v2 公开站
    基底 + ``ark_personalize`` 账户钩子（方舟在售筛选）；replace 读取——
    退役剔除不被基线补回。
    """
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
        catalog_source="models.dev",
        catalog_materialize=materialize_volcengine_fields,
        personalize=lambda base, context: ark_personalize(
            base, context, exempt_ids=set(PINNED_MODEL_IDS)
        ),
    )
