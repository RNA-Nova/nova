"""鉴权测试数据的类型化 builder 与不可达回调。"""

from typing import Any

from nova_ai.auth import AuthInteraction
from nova_async import AbortSignal
from nova_protocol import ApiKeyCredential, OAuthCredential, RedactedString


def api_key_credential(key: str, **kwargs: Any) -> ApiKeyCredential:
    return ApiKeyCredential(key=RedactedString(key), **kwargs)


def oauth_credential(
    access: str = "", refresh: str = "", **kwargs: Any
) -> OAuthCredential:
    return OAuthCredential(
        access=RedactedString(access), refresh=RedactedString(refresh), **kwargs
    )


async def never_login(_interaction: AuthInteraction) -> OAuthCredential:
    raise AssertionError("本路径不应触发 OAuth login")


async def never_refresh(
    _credential: OAuthCredential, _signal: AbortSignal | None = None
) -> OAuthCredential:
    raise AssertionError("本路径不应触发 OAuth refresh")
