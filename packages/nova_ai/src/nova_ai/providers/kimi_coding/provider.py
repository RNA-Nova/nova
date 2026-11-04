"""Kimi Coding provider 工厂。"""

from typing import cast

from nova_catalog.providers.kimi_coding import DEF as _knowledge
from nova_protocol import OAuthAuth, ProviderAuth

from ...auth.helpers import env_api_key_auth, lazy_oauth
from ...gateway import Provider, ProviderStreams, create_provider
from .catalog import BASE_URL, kimi_personalize
from .models import KIMI_CODING_MODELS


async def _load_kimi_oauth() -> OAuthAuth:
    """延迟加载 Kimi OAuth 流程实现（对齐 pi lazyOAuth 的动态 import 语义：
    流程模块推迟到 login/refresh/to_auth 首次被调用时才 import）。"""
    from ...auth.oauth.kimi import kimi_oauth

    return kimi_oauth


def kimi_coding_provider() -> "Provider":
    """构造 Kimi Coding provider 实例。

    personalized 刷新形态（``create_provider`` 装配）：pi.dev 公开站基底
    + ``kimi_personalize`` 账户钩子（managed 订阅源覆盖层）；replace
    读取——订阅筛掉的模型不被基线补回。

    协议随站：openai-completions（pi.dev 标注——与官方 Kimi Code 客户端
    同协议）。
    """
    from ...api_impls import openai_completions

    return create_provider(
        id="kimi-coding",
        name="Kimi Coding",
        base_url=BASE_URL,
        models=list(KIMI_CODING_MODELS.values()),
        # 模块结构上满足 ProviderStreams，cast 收口
        api=cast(ProviderStreams, openai_completions),
        auth=ProviderAuth(
            api_key=env_api_key_auth("Kimi API key", ["KIMI_API_KEY"]),
            oauth=lazy_oauth("Kimi (Moonshot AI)", _load_kimi_oauth),
        ),
        catalog_source="pi.dev",
        catalog_knowledge=_knowledge,
        personalize=kimi_personalize,
    )
