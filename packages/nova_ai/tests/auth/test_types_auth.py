"""types/auth 中 Credential 持久化 schema 的测试。"""

import pytest
from pydantic import ValidationError

from nova_ai.types.auth import (
    ApiKeyAuth,
    ApiKeyCredential,
    AuthCheck,
    AuthResult,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
)


class TestCredentials:
    def test_api_key_credential_roundtrip(self):
        cred = ApiKeyCredential(key="sk-x", env={"A": "1"})
        data = cred.model_dump()
        assert data == {"type": "api_key", "key": "sk-x", "env": {"A": "1"}}
        parsed = ApiKeyCredential.model_validate(data)
        assert parsed == cred
        assert parsed.type == "api_key"

    def test_oauth_credential_roundtrip(self):
        cred = OAuthCredential(access="a", refresh="r", expires=123)
        data = cred.model_dump()
        assert data["type"] == "oauth"
        parsed = OAuthCredential.model_validate(data)
        assert parsed == cred

    def test_oauth_credential_preserves_extra_fields(self):
        """对齐 TS OAuthCredentials 的 [key: string]: unknown 扩展位"""
        data = {
            "type": "oauth",
            "access": "a",
            "refresh": "r",
            "expires": 1,
            "accountId": "acc-1",
            "provider_specific": {"nested": True},
        }
        cred = OAuthCredential.model_validate(data)
        assert cred.accountId == "acc-1"
        assert cred.provider_specific == {"nested": True}
        # 往返序列化不丢扩展字段
        assert cred.model_dump()["provider_specific"] == {"nested": True}

    def test_oauth_credential_rejects_wrong_type_literal(self):
        with pytest.raises(ValidationError):
            OAuthCredential.model_validate({"type": "api_key", "access": "a"})

    def test_api_key_credential_rejects_bad_shape(self):
        with pytest.raises(ValidationError):
            ApiKeyCredential.model_validate({"type": "api_key", "key": 123})


class TestAuthContainers:
    def test_dataclass_containers(self):
        result = AuthResult(auth={"apiKey": "k"}, source="test")
        assert result.env is None
        check = AuthCheck(type="api_key", source="s")
        assert check.type == "api_key"

    def test_provider_auth_defaults(self):
        auth = ProviderAuth()
        assert auth.apiKey is None
        assert auth.oauth is None

    def test_auth_defs_are_dataclasses(self):
        import dataclasses

        assert dataclasses.is_dataclass(ApiKeyAuth)
        assert dataclasses.is_dataclass(OAuthAuth)
        assert dataclasses.is_dataclass(ProviderAuth)
