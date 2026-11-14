"""OpenAI Codex OAuth flow 单元测试。

HTTP 级（respx）：token 交换/刷新、设备码授权与轮询全走真实请求面；
泄漏断言锁"活凭据不进出错误消息"（reporting 铁律 6）。
"""

import base64
import hashlib
import json
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from nova_ai.auth import AuthInteraction
from nova_ai.auth.oauth.errors import OAuthFlowError
from nova_ai.auth.oauth.openai_codex import (
    create_authorization_flow,
    openai_codex_oauth,
)
from nova_protocol import (
    AuthEvent,
    AuthorizationRequest,
    AuthPrompt,
    NovaErrorKind,
    OAuthCredential,
    RedactedString,
)
from tests._helpers.auth import oauth_credential

pytestmark = pytest.mark.asyncio

_TOKEN_URL = "https://auth.openai.com/oauth/token"
_DEVICE_USER_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
_DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"


def _jwt_with_account(account_id: str = "acct-123") -> str:
    """构造带 accountId claim 的 JWT 形 token（_credentials_from_token 的提取面）。"""

    def _b64(obj: Dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    payload = {"https://api.openai.com/auth": {"chatgpt_account_id": account_id}}
    return f"{_b64({'alg': 'none'})}.{_b64(payload)}.fakesig"


def _token_payload(access: str, refresh: str = "refresh-456") -> Dict[str, Any]:
    return {"access_token": access, "refresh_token": refresh, "expires_in": 3600}


class _FakeInteraction(AuthInteraction):
    """记录 notify 事件 + 预定 prompt 应答的假 interaction。"""

    signal: Any = None

    def __init__(self, method: str = "device_code") -> None:
        self.events: List[AuthEvent] = []
        self._method = method

    async def prompt(self, prompt: AuthPrompt) -> str:
        return self._method

    async def acquire_authorization_code(self, request: AuthorizationRequest) -> str:
        raise NotImplementedError

    def notify(self, event: AuthEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_create_authorization_flow_url_encodes_query():
    flow = await create_authorization_flow()
    url = flow.url
    # scope 包含空格，必须被编码成 %20
    assert "scope=openid%20profile%20email%20offline_access" in url
    assert "response_type=code" in url
    assert "code_challenge=" in url
    assert "state=" in url


@pytest.mark.asyncio
async def test_authorization_flow_repr_hides_verifier_state_and_url():
    flow = await create_authorization_flow()
    text = repr(flow)
    assert flow.verifier not in text
    assert flow.state not in text
    assert flow.url not in text


@pytest.mark.asyncio
@respx.mock
async def test_browser_login_connects_url_callback_pkce_and_token_exchange():
    class BrowserInteraction(_FakeInteraction):
        def __init__(self):
            super().__init__("browser")
            self.requests = []

        async def acquire_authorization_code(self, request):
            self.requests.append(request)
            return "synthetic-code"

    route = respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json=_token_payload(_jwt_with_account()))
    )
    interaction = BrowserInteraction()
    credential = await openai_codex_oauth.login(interaction)
    assert credential.accountId == "acct-123"
    assert len(interaction.requests) == 1
    request = interaction.requests[0]
    assert interaction.events[0].url == request.auth_url
    form = parse_qs(route.calls[0].request.content.decode())
    assert form["code"] == ["synthetic-code"]
    assert form["redirect_uri"] == ["http://localhost:1455/auth/callback"]
    verifier = form["code_verifier"][0]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    query = parse_qs(urlparse(request.auth_url).query)
    assert query["code_challenge"] == [challenge]
    assert query["state"] == [request.state]


class TestTokenResponseBoundary:
    """token 端点响应的边界校验（_TokenResponse）——形状/容忍度/不泄漏。"""

    @respx.mock
    async def test_refresh_success(self):
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=_token_payload(_jwt_with_account()))
        )
        old = oauth_credential(access="access-old", refresh="refresh-old", expires=0)
        new = await openai_codex_oauth.refresh(old, None)
        assert new.refresh == "refresh-456"
        assert new.accountId == "acct-123"  # JWT claim 提取接线

    @respx.mock
    async def test_missing_refresh_lists_field_not_values(self):
        """200 缺 refresh_token：活 access_token（JWT 形）不进出错误消息。"""
        secret_access = _jwt_with_account()
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": secret_access, "expires_in": 3600}
            )
        )
        old = oauth_credential(access="access-old", refresh="refresh-old", expires=0)
        with pytest.raises(RuntimeError) as exc_info:
            await openai_codex_oauth.refresh(old, None)
        message = str(exc_info.value)
        assert "refresh_token" in message
        assert secret_access not in message

    @respx.mock
    async def test_expires_in_bool_rejected(self):
        """等价矩阵：codex 严格拒 bool expires_in（与 kimi 宽松相反）。"""
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json=_token_payload(_jwt_with_account()) | {"expires_in": True}
            )
        )
        with pytest.raises(RuntimeError) as exc_info:
            await openai_codex_oauth.refresh(
                oauth_credential(access="a", refresh="r", expires=0), None
            )
        assert "expires_in" in str(exc_info.value)

    @respx.mock
    async def test_expires_in_string_number_rejected(self):
        """等价矩阵：codex 拒字符串数字 expires_in（kimi 侧则容忍）。"""
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json=_token_payload(_jwt_with_account()) | {"expires_in": "3600"}
            )
        )
        with pytest.raises(RuntimeError) as exc_info:
            await openai_codex_oauth.refresh(
                oauth_credential(access="a", refresh="r", expires=0), None
            )
        assert "expires_in" in str(exc_info.value)

    @respx.mock
    async def test_error_status_json_error_fields_only(self):
        """错误状态码：只取结构化错误字段，body 其余内容不回显。"""
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {"code": "invalid_grant"},
                    "debug": {"token": "sk-proj-abcdefgh12345678"},
                },
            )
        )
        with pytest.raises(RuntimeError) as exc_info:
            await openai_codex_oauth.refresh(
                oauth_credential(access="a", refresh="r", expires=0), None
            )
        message = str(exc_info.value)
        assert "invalid_grant" in message
        assert "sk-proj" not in message


class TestDeviceCodeBoundary:
    """设备码链路（_DeviceCodeStartResponse/_DevicePollResponse）边界。"""

    @respx.mock
    async def test_device_flow_interval_string_coerced(self):
        """等价矩阵：interval 字符串数字强转（现状语义保留）。"""
        respx.post(_DEVICE_USER_CODE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "device_auth_id": "dev-1",
                    "user_code": "CODE-1",
                    "interval": "5",
                },
            )
        )
        respx.post(_DEVICE_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "authorization_code": "auth-code-1",
                    "code_verifier": "verifier-1",
                },
            )
        )
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=_token_payload(_jwt_with_account()))
        )
        credential = await openai_codex_oauth.login(_FakeInteraction())
        assert credential.refresh == "refresh-456"

    @respx.mock
    async def test_device_start_invalid_lists_field_not_values(self):
        """device_auth_id（轮询凭据）不进出校验失败消息。"""
        respx.post(_DEVICE_USER_CODE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "device_auth_id": "dev-secret-123",
                    "user_code": "CODE-1",
                    "interval": -1,  # 负数拒绝
                },
            )
        )
        with pytest.raises(RuntimeError) as exc_info:
            await openai_codex_oauth.login(_FakeInteraction())
        message = str(exc_info.value)
        assert "interval" in message
        assert "dev-secret-123" not in message

    @respx.mock
    async def test_poll_missing_authorization_code_no_echo(self):
        """轮询 200 缺 authorization_code：code_verifier（可兑 token）不进消息。"""
        respx.post(_DEVICE_USER_CODE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "device_auth_id": "dev-1",
                    "user_code": "CODE-1",
                    "interval": 1,
                },
            )
        )
        respx.post(_DEVICE_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"code_verifier": "verifier-secret-456"}
            )
        )
        with pytest.raises(OAuthFlowError) as exc_info:
            await openai_codex_oauth.login(_FakeInteraction())
        message = str(exc_info.value)
        assert exc_info.value.nova_error_kind is NovaErrorKind.UPSTREAM_ERROR
        assert "authorization_code" in message
        assert "verifier-secret-456" not in message

    @respx.mock
    async def test_poll_error_status_no_raw_body(self):
        """轮询错误状态码：原始 body 不回显。"""
        respx.post(_DEVICE_USER_CODE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "device_auth_id": "dev-1",
                    "user_code": "CODE-1",
                    "interval": 1,
                },
            )
        )
        respx.post(_DEVICE_TOKEN_URL).mock(
            return_value=httpx.Response(500, content=b"<html>cloudflare</html>")
        )
        with pytest.raises(RuntimeError) as exc_info:
            await openai_codex_oauth.login(_FakeInteraction())
        message = str(exc_info.value)
        assert "500" in message
        assert "<html>" not in message

    @respx.mock
    async def test_token_non_json_is_safe_upstream_error(self):
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(200, content=b"not-json")
        )
        with pytest.raises(OAuthFlowError) as exc_info:
            await openai_codex_oauth.refresh(
                oauth_credential(access="a", refresh="r", expires=0), None
            )
        assert exc_info.value.nova_error_kind is NovaErrorKind.UPSTREAM_ERROR
        assert "not-json" not in str(exc_info.value)

    async def test_temporary_oauth_values_are_redacted_from_repr(self):
        from nova_ai.auth.oauth.openai_codex import (
            _DeviceCodeGrant,
            _DeviceCodeStartResponse,
            _TokenResponse,
        )

        token = _TokenResponse(
            access_token=RedactedString("synthetic-access"),
            refresh_token=RedactedString("synthetic-refresh"),
            expires_in=60,
        )
        start = _DeviceCodeStartResponse(
            device_auth_id=RedactedString("synthetic-device-id"),
            user_code=RedactedString("synthetic-user-code"),
            interval=0,
        )
        grant = _DeviceCodeGrant(
            authorization_code=RedactedString("synthetic-code"),
            code_verifier=RedactedString("synthetic-verifier"),
        )
        for secret in (
            "synthetic-access",
            "synthetic-refresh",
            "synthetic-device-id",
            "synthetic-user-code",
            "synthetic-code",
            "synthetic-verifier",
        ):
            assert secret not in repr(token)
            assert secret not in repr(start)
            assert secret not in repr(grant)
