"""Provider 鉴权行为定义的容器选型测试。"""

import dataclasses

from nova_ai.auth import ApiKeyAuth, OAuthAuth, ProviderAuth


def test_provider_auth_defaults():
    auth = ProviderAuth()
    assert auth.api_key is None
    assert auth.oauth is None


def test_auth_defs_are_dataclasses():
    assert dataclasses.is_dataclass(ApiKeyAuth)
    assert dataclasses.is_dataclass(OAuthAuth)
    assert dataclasses.is_dataclass(ProviderAuth)
