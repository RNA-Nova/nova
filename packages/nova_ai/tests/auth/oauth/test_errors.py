"""OAuthFlowError（auth/oauth/errors.py）测试——自声明 kind 与状态透传。"""

import httpx
from nova_protocol import NovaBaseModel, NovaErrorKind
from pydantic import ConfigDict, Field, ValidationError

from nova_ai.auth.oauth.errors import (
    OAuthFlowError,
    oauth_http_kind,
    response_error_detail,
    validation_error,
    validation_message,
)


class TestOAuthHttpKind:
    def test_4xx_unauthorized(self):
        for status in (400, 401, 403, 404, 422):
            assert oauth_http_kind(status) is NovaErrorKind.UNAUTHORIZED

    def test_5xx_upstream(self):
        for status in (500, 502, 503, 529):
            assert oauth_http_kind(status) is NovaErrorKind.UPSTREAM_ERROR


class TestOAuthFlowError:
    def test_kind_and_status_attached(self):
        err = OAuthFlowError(
            "refresh unauthorized",
            kind=NovaErrorKind.UNAUTHORIZED,
            status_code=401,
        )
        assert err.nova_error_kind is NovaErrorKind.UNAUTHORIZED
        assert err.status_code == 401
        assert str(err) == "refresh unauthorized"
        # 保持 RuntimeError 兼容（存量捕获面不破坏）
        assert isinstance(err, RuntimeError)

    def test_kind_omitted_no_attribute(self):
        """不传 kind：无 nova_error_kind 属性——归一按 status_code 标准映射判定。"""
        err = OAuthFlowError("boom", status_code=429)
        assert getattr(err, "nova_error_kind", None) is None
        assert err.status_code == 429

    def test_bare_error_no_attributes(self):
        err = OAuthFlowError("boom")
        assert getattr(err, "nova_error_kind", None) is None
        assert getattr(err, "status_code", None) is None


class TestValidationMessage:
    """响应校验失败的消息构造——只报字段名，响应体永不进消息。"""

    class _Shape(NovaBaseModel):
        model_config = ConfigDict(alias_generator=None)  # 与生产模型同款（snake）

        access_token: str = Field(min_length=1)
        expires_in: float

    def _invalid(self, data: dict) -> ValidationError:
        try:
            self._Shape.model_validate(data)
        except ValidationError as error:
            return error
        raise AssertionError("expected ValidationError")

    def test_lists_field_names(self):
        err = self._invalid({"expires_in": 3600})  # 缺 access_token
        message = validation_message("Test endpoint", err)
        assert "access_token" in message
        assert "Field required" in message

    def test_never_echoes_values(self):
        """响应体里的值（含活凭据形状）不进消息——本纪律的核心断言。"""
        secret = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ4In0.sigpart"
        err = self._invalid({"access_token": secret, "expires_in": "not-a-number"})
        message = validation_message("Test endpoint", err)
        assert secret not in message
        assert "not-a-number" not in message
        assert "expires_in" in message

    def test_validation_error_wraps_with_kind(self):
        err = validation_error("Test endpoint", self._invalid({}))
        assert isinstance(err, OAuthFlowError)
        assert err.nova_error_kind is NovaErrorKind.UPSTREAM_ERROR


class TestResponseErrorDetail:
    """错误状态码响应的安全细节——结构化错误字段优先，不回显原始 body。"""

    def test_json_error_description_preferred(self):
        resp = httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "token revoked"}
        )
        assert response_error_detail(resp) == "token revoked"

    def test_json_error_string_fallback(self):
        resp = httpx.Response(429, json={"error": "rate_limited"})
        assert response_error_detail(resp) == "rate_limited"

    def test_json_error_object_code(self):
        resp = httpx.Response(400, json={"error": {"code": "bad_request"}})
        assert response_error_detail(resp) == "bad_request"

    def test_non_json_body_falls_back_to_reason_phrase(self):
        """网关 HTML/空体：reason_phrase 兜底，原始 body 不进消息。"""
        resp = httpx.Response(502, content=b"<html>bad gateway page</html>")
        detail = response_error_detail(resp)
        assert detail == "Bad Gateway"
        assert "<html>" not in detail
