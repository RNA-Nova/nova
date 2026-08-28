"""Kimi (Moonshot AI) OAuth device code flow 单元测试。"""

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx
import pytest

from nova_ai.auth.oauth.kimi import _get_oauth_host, kimi_oauth
from nova_ai.types.auth import AuthEvent, AuthInteraction, AuthPrompt, OAuthCredential


class _FakeInteraction(AuthInteraction):
    """记录 notify 事件的假 interaction。"""

    signal: Any = None

    def __init__(self) -> None:
        self.events: List[AuthEvent] = []

    async def prompt(self, prompt: AuthPrompt) -> str:
        raise NotImplementedError

    def notify(self, event: AuthEvent) -> None:
        self.events.append(event)


def _response(
    status_code: int, json_data: Optional[Dict[str, Any]] = None
) -> httpx.Response:
    return httpx.Response(status_code, json=json_data)


async def _noop_abortable_sleep(_ms: float, _signal: Optional[Any] = None) -> None:
    """用于测试的 no-op sleep，避免等待真实时间。"""
    return


@pytest.mark.asyncio
async def test_device_code_login_success():
    async def _fake_post(
        url: str, params: Dict[str, str], headers=None
    ) -> httpx.Response:
        if "/device_authorization" in url:
            return _response(
                200,
                {
                    "user_code": "ABCD-EFGH",
                    "device_code": "dev-123",
                    "verification_uri_complete": "https://auth.kimi.com/verify?code=ABCD-EFGH",
                    "verification_uri": "https://auth.kimi.com/verify",
                    "expires_in": 1800,
                    "interval": 0,
                },
            )
        return _response(
            200,
            {
                "access_token": "access-123",
                "refresh_token": "refresh-456",
                "expires_in": 3600,
            },
        )

    interaction = _FakeInteraction()
    with patch("nova_ai.auth.oauth.kimi._post_form", _fake_post):
        with patch(
            "nova_ai.auth.oauth.device_code._abortable_sleep",
            _noop_abortable_sleep,
        ):
            credential = await kimi_oauth.login(interaction)

    assert isinstance(credential, OAuthCredential)
    assert credential.access == "access-123"
    assert credential.refresh == "refresh-456"
    assert credential.expires > 0
    assert len(interaction.events) == 1
    event = interaction.events[0]
    assert event.type == "device_code"
    assert event.userCode == "ABCD-EFGH"
    assert (
        event.verificationUriComplete == "https://auth.kimi.com/verify?code=ABCD-EFGH"
    )


@pytest.mark.asyncio
async def test_device_code_slow_down_then_success():
    calls: List[Dict[str, str]] = []

    async def _fake_post(
        url: str, params: Dict[str, str], headers=None
    ) -> httpx.Response:
        if "/device_authorization" in url:
            return _response(
                200,
                {
                    "user_code": "SLOW-0001",
                    "device_code": "dev-slow",
                    "verification_uri_complete": "https://auth.kimi.com/verify",
                    "expires_in": 1800,
                    "interval": 0,
                },
            )
        calls.append(params)
        if len(calls) == 1:
            return _response(400, {"error": "slow_down"})
        return _response(
            200,
            {
                "access_token": "access-slow",
                "refresh_token": "refresh-slow",
                "expires_in": 3600,
            },
        )

    interaction = _FakeInteraction()
    with patch("nova_ai.auth.oauth.kimi._post_form", _fake_post):
        with patch(
            "nova_ai.auth.oauth.device_code._abortable_sleep",
            _noop_abortable_sleep,
        ):
            credential = await kimi_oauth.login(interaction)

    assert credential.access == "access-slow"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_device_code_access_denied_raises():
    async def _fake_post(
        url: str, params: Dict[str, str], headers=None
    ) -> httpx.Response:
        if "/device_authorization" in url:
            return _response(
                200,
                {
                    "user_code": "DENY-0001",
                    "device_code": "dev-deny",
                    "verification_uri_complete": "https://auth.kimi.com/verify",
                    "expires_in": 1800,
                    "interval": 0,
                },
            )
        return _response(
            400, {"error": "access_denied", "error_description": "user rejected"}
        )

    interaction = _FakeInteraction()
    with pytest.raises(RuntimeError, match="user rejected"):
        with patch("nova_ai.auth.oauth.kimi._post_form", _fake_post):
            with patch(
                "nova_ai.auth.oauth.device_code._abortable_sleep",
                _noop_abortable_sleep,
            ):
                await kimi_oauth.login(interaction)


@pytest.mark.asyncio
async def test_device_code_timeout():
    async def _fake_post(
        url: str, params: Dict[str, str], headers=None
    ) -> httpx.Response:
        if "/device_authorization" in url:
            return _response(
                200,
                {
                    "user_code": "TIME-0001",
                    "device_code": "dev-time",
                    "verification_uri_complete": "https://auth.kimi.com/verify",
                    "expires_in": 1800,
                    "interval": 0,
                },
            )
        return _response(400, {"error": "authorization_pending"})

    interaction = _FakeInteraction()
    with pytest.raises(TimeoutError, match="Device flow timed out"):
        with patch("nova_ai.auth.oauth.kimi._post_form", _fake_post):
            with patch("nova_ai.auth.oauth.kimi._DEVICE_CODE_TIMEOUT_SECONDS", 0.05):
                with patch(
                    "nova_ai.auth.oauth.device_code._abortable_sleep",
                    _noop_abortable_sleep,
                ):
                    await kimi_oauth.login(interaction)


@pytest.mark.asyncio
async def test_device_code_cancel_with_signal():
    class _AbortedSignal:
        aborted = True

    async def _fake_post(
        url: str, params: Dict[str, str], headers=None
    ) -> httpx.Response:
        if "/device_authorization" in url:
            return _response(
                200,
                {
                    "user_code": "CANCEL-001",
                    "device_code": "dev-cancel",
                    "verification_uri_complete": "https://auth.kimi.com/verify",
                    "expires_in": 1800,
                    "interval": 0,
                },
            )
        return _response(400, {"error": "authorization_pending"})

    interaction = _FakeInteraction()
    interaction.signal = _AbortedSignal()
    with pytest.raises(asyncio.CancelledError, match="Login cancelled"):
        with patch("nova_ai.auth.oauth.kimi._post_form", _fake_post):
            with patch(
                "nova_ai.auth.oauth.device_code._abortable_sleep",
                _noop_abortable_sleep,
            ):
                await kimi_oauth.login(interaction)


@pytest.mark.asyncio
async def test_refresh_success():
    async def _fake_post(
        url: str, params: Dict[str, str], headers=None
    ) -> httpx.Response:
        assert params.get("grant_type") == "refresh_token"
        assert params.get("refresh_token") == "refresh-old"
        return _response(
            200,
            {
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "expires_in": 3600,
            },
        )

    old = OAuthCredential(access="access-old", refresh="refresh-old", expires=0)
    with patch("nova_ai.auth.oauth.kimi._post_form", _fake_post):
        new = await kimi_oauth.refresh(old)

    assert new.access == "access-new"
    assert new.refresh == "refresh-new"
    assert new.expires > 0


@pytest.mark.asyncio
async def test_refresh_unauthorized():
    async def _fake_post(
        url: str, params: Dict[str, str], headers=None
    ) -> httpx.Response:
        return _response(
            401, {"error": "invalid_grant", "error_description": "token revoked"}
        )

    old = OAuthCredential(access="access-old", refresh="refresh-old", expires=0)
    with pytest.raises(RuntimeError, match="token revoked"):
        with patch("nova_ai.auth.oauth.kimi._post_form", _fake_post):
            await kimi_oauth.refresh(old)


def test_get_oauth_host_env_override(monkeypatch):
    monkeypatch.setenv("KIMI_CODE_OAUTH_HOST", "https://auth.example.com/")
    assert _get_oauth_host() == "https://auth.example.com"

    monkeypatch.delenv("KIMI_CODE_OAUTH_HOST", raising=False)
    monkeypatch.setenv("KIMI_OAUTH_HOST", "https://auth2.example.com")
    assert _get_oauth_host() == "https://auth2.example.com"

    monkeypatch.delenv("KIMI_OAUTH_HOST", raising=False)
    assert _get_oauth_host() == "https://auth.kimi.com"
