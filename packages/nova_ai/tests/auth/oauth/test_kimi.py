"""Kimi (Moonshot AI) OAuth device code flow 测试（respx HTTP 级——
URL/表单体/状态码处理全真跑；退避等待经存量时钟缝（abortable_sleep）封堵）。

轮询机器的通用语义（slow_down 退避/超时/取消）归 device_code 层自测，
本文件只锁 kimi 侧的接线与错误裁决。
"""

from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from nova_protocol import (
    AbortedError,
    AuthEvent,
    AuthInteraction,
    AuthPrompt,
    OAuthCredential,
)

from nova_ai.auth.oauth.kimi import kimi_oauth

pytestmark = pytest.mark.asyncio

_HOST = "https://auth.kimi.com"
_DEVICE_AUTH_URL = f"{_HOST}/api/oauth/device_authorization"
_TOKEN_URL = f"{_HOST}/api/oauth/token"


class _FakeInteraction(AuthInteraction):
    """记录 notify 事件的假 interaction。"""

    signal: Any = None

    def __init__(self) -> None:
        self.events: List[AuthEvent] = []

    async def prompt(self, prompt: AuthPrompt) -> str:
        raise NotImplementedError

    def notify(self, event: AuthEvent) -> None:
        self.events.append(event)


def _device_auth_payload(user_code: str = "ABCD-EFGH") -> Dict[str, Any]:
    return {
        "user_code": user_code,
        "device_code": "dev-123",
        "verification_uri_complete": f"https://auth.kimi.com/verify?code={user_code}",
        "verification_uri": "https://auth.kimi.com/verify",
        "expires_in": 1800,
        "interval": 1,
    }


def _token_payload(
    access: str = "access-123", refresh: str = "refresh-456"
) -> Dict[str, Any]:
    return {"access_token": access, "refresh_token": refresh, "expires_in": 3600}


async def _noop_abortable_sleep(_ms: float, _signal: Optional[Any] = None) -> None:
    """存量时钟缝：封堵轮询/退避的真实等待（仅多轮等待的用例使用）。"""
    return


def _mock_device_flow(token_side_effect: list) -> None:
    respx.post(_DEVICE_AUTH_URL).mock(
        return_value=httpx.Response(200, json=_device_auth_payload())
    )
    respx.post(_TOKEN_URL).mock(side_effect=token_side_effect)


@respx.mock
async def test_device_code_login_success():
    _mock_device_flow([httpx.Response(200, json=_token_payload())])
    interaction = _FakeInteraction()
    credential = await kimi_oauth.login(interaction)

    assert credential.access == "access-123"
    assert credential.refresh == "refresh-456"
    assert credential.expires > 0
    assert len(interaction.events) == 1
    event = interaction.events[0]
    assert event.type == "device_code"
    assert event.user_code == "ABCD-EFGH"
    assert (
        event.verification_uri_complete == "https://auth.kimi.com/verify?code=ABCD-EFGH"
    )


@respx.mock
async def test_device_code_pending_then_success(monkeypatch):
    """pending 后成功：slow_down/pending 映射与轮询续跑。"""
    monkeypatch.setattr(
        "nova_ai.auth.oauth.device_code.abortable_sleep", _noop_abortable_sleep
    )
    _mock_device_flow(
        [
            httpx.Response(400, json={"error": "slow_down"}),
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(200, json=_token_payload(access="access-slow")),
        ]
    )
    credential = await kimi_oauth.login(_FakeInteraction())
    assert credential.access == "access-slow"

    route = respx.post(_TOKEN_URL)
    assert route.call_count == 3


@respx.mock
async def test_device_code_access_denied_raises():
    _mock_device_flow(
        [
            httpx.Response(
                400,
                json={"error": "access_denied", "error_description": "user rejected"},
            )
        ]
    )
    with pytest.raises(RuntimeError, match="user rejected"):
        await kimi_oauth.login(_FakeInteraction())


async def test_device_code_pre_aborted_signal_cancels_before_any_http():
    """举旗进入：任何 HTTP 之前即以 AbortedError 收场（respx 缺席——
    若误发请求会触真实网络而暴露）。"""

    class _AbortedSignal:
        aborted = True

    interaction = _FakeInteraction()
    interaction.signal = _AbortedSignal()
    with pytest.raises(AbortedError, match="Login cancelled"):
        await kimi_oauth.login(interaction)


@respx.mock
@pytest.mark.parametrize("env_name", ["KIMI_CODE_OAUTH_HOST", "KIMI_OAUTH_HOST"])
async def test_oauth_host_env_override_drives_request_target(monkeypatch, env_name):
    """host 覆盖的公开面验证：请求真的发到自定义 host（替代私有函数直测）。"""
    custom = "https://auth.example.com"
    monkeypatch.setenv(env_name, custom)
    route = respx.post(f"{custom}/api/oauth/device_authorization").mock(
        return_value=httpx.Response(200, json=_device_auth_payload())
    )
    respx.post(f"{custom}/api/oauth/token").mock(
        return_value=httpx.Response(200, json=_token_payload())
    )
    credential = await kimi_oauth.login(_FakeInteraction())
    assert route.called
    assert credential.access == "access-123"


@respx.mock
async def test_refresh_success():
    route = respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json=_token_payload(access="access-new", refresh="refresh-new")
        )
    )
    old = OAuthCredential(access="access-old", refresh="refresh-old", expires=0)
    new = await kimi_oauth.refresh(old)

    assert new.access == "access-new"
    assert new.refresh == "refresh-new"
    assert new.expires > 0
    # 线上表单体断言（真实 wire 字节，强于参数面断言）
    form = parse_qs(route.calls[0].request.content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["refresh-old"]


@respx.mock
async def test_refresh_unauthorized():
    respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(
            401, json={"error": "invalid_grant", "error_description": "token revoked"}
        )
    )
    old = OAuthCredential(access="access-old", refresh="refresh-old", expires=0)
    with pytest.raises(RuntimeError, match="token revoked"):
        await kimi_oauth.refresh(old)


@respx.mock
async def test_refresh_retries_on_non_json_error_body(monkeypatch):
    """429/5xx 的非 JSON 错误体（网关 HTML/空体）：状态码重试逻辑必须先行，
    不得让 JSONDecodeError 逃逸（回归：此前 data = response.json() 在重试判断之前）。"""
    monkeypatch.setattr(
        "nova_ai.auth.oauth.kimi.abortable_sleep", _noop_abortable_sleep
    )
    route = respx.post(_TOKEN_URL).mock(
        side_effect=[
            httpx.Response(429, content=b"<html>rate limited</html>"),
            httpx.Response(200, json=_token_payload(access="a", refresh="r")),
        ]
    )
    old = OAuthCredential(access="access-old", refresh="refresh-old", expires=0)
    new = await kimi_oauth.refresh(old)
    assert new.access == "a"
    assert route.call_count == 2


@respx.mock
async def test_refresh_non_json_error_body_terminal_unauthorized():
    """401 非 JSON 错误体：按 unauthorized 干净收场，不炸 JSONDecodeError。"""
    respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(401, content=b"<html>denied</html>")
    )
    old = OAuthCredential(access="access-old", refresh="refresh-old", expires=0)
    with pytest.raises(RuntimeError, match="unauthorized"):
        await kimi_oauth.refresh(old)


@respx.mock
async def test_refresh_abort_during_backoff_raises_aborted(monkeypatch):
    """刷新退避期间 abort：冒 AbortedError（不是 RuntimeError 假冒，也不继续重试）。"""
    respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(429, json={"error": "rate_limited"})
    )

    async def _abort_sleep(_ms: float, _signal: Optional[Any] = None) -> None:
        raise AbortedError("The operation was aborted")

    monkeypatch.setattr("nova_ai.auth.oauth.kimi.abortable_sleep", _abort_sleep)
    old = OAuthCredential(access="access-old", refresh="refresh-old", expires=0)
    with pytest.raises(AbortedError):
        await kimi_oauth.refresh(old)


class TestResponseValidationNoLeak:
    """边界校验模型化（_TokenResponse/_DeviceAuthorizationResponse）——
    活凭据不进出错误消息（reporting 铁律 6 的回归锁；消息只报字段名）。"""

    @respx.mock
    async def test_missing_refresh_token_lists_field_not_values(self):
        """200 但缺 refresh_token：活 access_token 不进出错误消息。"""
        secret_access = "eyJhbGciOiJIUzI1NiJ9.eyJhY2NvdW50IjoiYWJjMTIzIn0.fakesig"
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": secret_access, "expires_in": 3600}
            )
        )
        old = OAuthCredential(access="access-old", refresh="refresh-old", expires=0)
        with pytest.raises(RuntimeError) as exc_info:
            await kimi_oauth.refresh(old)
        message = str(exc_info.value)
        assert "refresh_token" in message  # 字段名指引（可行动文案）
        assert secret_access not in message  # 活 token 不进消息

    @respx.mock
    async def test_expires_in_string_number_accepted(self):
        """等价矩阵：kimi 容忍字符串数字 expires_in（lax 强转，现状语义保留）。"""
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "a1",
                    "refresh_token": "r1",
                    "expires_in": "3600",
                },
            )
        )
        new = await kimi_oauth.refresh(
            OAuthCredential(access="a", refresh="r", expires=0)
        )
        assert new.access == "a1"

    @respx.mock
    @pytest.mark.parametrize("bad", [0, -5, "abc", None])
    async def test_expires_in_invalid_rejected_without_echo(self, bad):
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "a1", "refresh_token": "r1", "expires_in": bad},
            )
        )
        with pytest.raises(RuntimeError) as exc_info:
            await kimi_oauth.refresh(
                OAuthCredential(access="a", refresh="r", expires=0)
            )
        message = str(exc_info.value)
        assert "expires_in" in message
        assert "a1" not in message  # 响应体的其余字段不露面

    @respx.mock
    @pytest.mark.parametrize("literal", ["Infinity", "NaN", "-Infinity"])
    async def test_expires_in_non_finite_rejected(self, literal):
        """非有限 expires_in（JSON 扩展字面量 Infinity/NaN）拒绝。"""
        body = (
            '{"access_token": "a1", "refresh_token": "r1", '
            f'"expires_in": {literal}}}'
        ).encode()
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, content=body))
        with pytest.raises(RuntimeError) as exc_info:
            await kimi_oauth.refresh(
                OAuthCredential(access="a", refresh="r", expires=0)
            )
        assert "expires_in" in str(exc_info.value)

    @respx.mock
    async def test_device_auth_untrusted_url_rejected_without_echo(self):
        """不信任 scheme 的 verification_uri 拒绝；device_code 不进消息。"""
        payload = _device_auth_payload()
        payload["verification_uri_complete"] = "file:///etc/passwd"
        payload["device_code"] = "device-secret-xyz-123"
        respx.post(_DEVICE_AUTH_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )
        with pytest.raises(RuntimeError) as exc_info:
            await kimi_oauth.login(_FakeInteraction())
        message = str(exc_info.value)
        assert "verification_uri_complete" in message
        assert "device-secret-xyz-123" not in message  # 可兑 token 的凭据不进消息

    @respx.mock
    async def test_device_auth_500_html_body_not_echoed(self):
        """错误状态码 + 非 JSON body（WAF/网关 HTML）：reason_phrase 兜底。"""
        respx.post(_DEVICE_AUTH_URL).mock(
            return_value=httpx.Response(500, content=b"<html>nginx exploded</html>")
        )
        with pytest.raises(RuntimeError) as exc_info:
            await kimi_oauth.login(_FakeInteraction())
        message = str(exc_info.value)
        assert "500" in message
        assert "<html>" not in message

    @respx.mock
    async def test_refresh_500_json_error_fields_only(self, monkeypatch):
        """5xx JSON 错误体：只取 error_description，body 其余字段不露面。"""
        monkeypatch.setattr(
            "nova_ai.auth.oauth.kimi.abortable_sleep", _noop_abortable_sleep
        )
        respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(
                500,
                json={
                    "error": "server_error",
                    "error_description": "backend exploded",
                    "debug": {"token": "sk-ant-api03-aaaaaaaaaaaaaaaaaaaa"},
                },
            )
        )
        with pytest.raises(RuntimeError) as exc_info:
            await kimi_oauth.refresh(
                OAuthCredential(access="a", refresh="r", expires=0)
            )
        message = str(exc_info.value)
        assert "backend exploded" in message
        assert "sk-ant" not in message
