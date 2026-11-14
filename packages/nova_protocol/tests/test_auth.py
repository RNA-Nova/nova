"""RedactedString 与凭据模型（auth.py）测试——脱敏 + 持久化 schema + 容器选型。"""

import pytest
from nova_protocol import (
    ApiKeyCredential,
    AuthCheck,
    AuthResult,
    OAuthCredential,
    RedactedString,
)
from pydantic import ValidationError


class TestRedactedString:
    def test_repr_masked_str_plain(self):
        token = RedactedString("sk-live-123")
        assert repr(token) == "<redacted>"
        assert str(token) == "sk-live-123"
        assert f"Bearer {token}" == "Bearer sk-live-123"
        assert token == "sk-live-123"  # str 语义不受影响

    def test_container_repr_masked(self):
        """容器打印内容时对元素逐个 repr——值自带掩码，容器自动安全。"""
        token = RedactedString("sk-live-123")
        # <redacted> 是无引号哨兵（codex Debug 同款），容器原样嵌入
        assert repr([token]) == "[<redacted>]"
        assert repr({"t": token}) == "{'t': <redacted>}"

    def test_validate_wraps_plain_str(self):
        cred = OAuthCredential.model_validate(
            {"type": "oauth", "access": "sk-live-123", "refresh": "r-456"}
        )
        assert isinstance(cred.access, RedactedString)
        assert isinstance(cred.refresh, RedactedString)


class TestOAuthCredentialRedaction:
    def test_model_repr_hides_tokens(self):
        cred = OAuthCredential(
            access="sk-live-123", refresh="r-456", expires=999, accountId="me"
        )
        rendered = repr(cred)
        assert "sk-live-123" not in rendered
        assert "r-456" not in rendered
        assert "expires=999" in rendered
        assert "accountId='me'" in rendered

    def test_model_repr_hides_extra_fields(self):
        """extra="allow" 的扩展字段可能藏第二个 token——白名单只露安全字段。"""
        cred = OAuthCredential.model_validate(
            {"type": "oauth", "access": "a", "refresh": "b", "id_token": "jwt-xyz"}
        )
        rendered = repr(cred)
        assert "jwt-xyz" not in rendered
        assert "id_token" not in rendered

    def test_model_dump_preserves_plaintext(self):
        """落盘/序列化照常出明文（transparent——auth.json 不写掩码）。"""
        cred = OAuthCredential(access="sk-live-123", refresh="r-456", expires=999)
        dumped = cred.model_dump()
        assert dumped["access"] == "sk-live-123"
        assert dumped["refresh"] == "r-456"
        # roundtrip 保真
        restored = OAuthCredential.model_validate(dumped)
        assert restored.access == "sk-live-123"


class TestApiKeyCredentialRedaction:
    def test_model_repr_hides_key_and_env(self):
        cred = ApiKeyCredential(
            key="sk-live-123", env={"VOLCENGINE_API_KEY": "volc-secret"}
        )
        rendered = repr(cred)
        assert "sk-live-123" not in rendered
        assert "volc-secret" not in rendered
        assert "api_key" in rendered  # type 字段仍可见

    def test_dump_preserves_plaintext(self):
        cred = ApiKeyCredential(key="sk-live-123")
        assert cred.model_dump()["key"] == "sk-live-123"


class TestCredentialRoundtrip:
    """凭据模型的持久化 schema（roundtrip + 形状拒绝）。"""

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
        """extra="allow" 扩展位：provider 扩展字段随往返不丢。"""
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
        assert cred.model_dump()["provider_specific"] == {"nested": True}

    def test_oauth_credential_rejects_wrong_type_literal(self):
        with pytest.raises(ValidationError):
            OAuthCredential.model_validate({"type": "api_key", "access": "a"})

    def test_api_key_credential_rejects_bad_shape(self):
        with pytest.raises(ValidationError):
            ApiKeyCredential.model_validate({"type": "api_key", "key": 123})


class TestAuthContainers:
    """auth 运行时容器（dataclass 选型——行为/Callable 家族）。"""

    def test_dataclass_containers(self):
        result = AuthResult(auth={"api_key": "k"}, source="test")
        assert result.env is None
        check = AuthCheck(type="api_key", source="s")
        assert check.type == "api_key"
