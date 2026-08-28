"""
Volcengine provider 工厂

对应 TS ``src/providers/volcengine.ts``：provider 直接绑定 openai-completions
API 实现模块的 ``stream`` / ``stream_simple`` 函数。
"""

from ...auth.helpers import env_api_key_auth
from ...gateway import create_provider
from ...types.auth import ProviderAuth
from .models import VOLCENGINE_MODELS


def volcengine_provider():
    """构造 Volcengine provider 实例。

    延迟导入 ``api_impls.openai_completions`` 以避免 ``api_impls`` 与
    ``providers`` 之间的循环初始化。
    """
    from ...api_impls import openai_completions

    return create_provider(
        id="volcengine",
        name="Volcengine",
        base_url="https://ark.cn-beijing.volces.com/api/v3/",
        models=list(VOLCENGINE_MODELS.values()),
        api=openai_completions,
        auth=ProviderAuth(
            apiKey=env_api_key_auth(
                "Volcengine API Key",
                ["VOLCENGINE_API_KEY"],
            ),
        ),
    )
