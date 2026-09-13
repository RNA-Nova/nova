"""OpenAI Codex (ChatGPT OAuth) flow。

对齐 TypeScript ``src/auth/oauth/openai-codex.ts``：
支持浏览器登录（收货经 ``AuthInteraction.acquire_authorization_code``，
渠道装配归 nova_harness 接线层）和设备码登录两种模式。
"""

import base64
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

import httpx
from nova_protocol import (
    AbortSignal,
    AuthEvent,
    AuthInteraction,
    AuthorizationRequest,
    AuthPrompt,
    AuthPromptOption,
    ModelAuth,
    OAuthAuth,
    OAuthCredential,
)

from ..oauth_page import oauth_error_html, oauth_success_html
from .device_code import (
    DeviceCodePollOptions,
    DeviceCodePollResult,
    poll_oauth_device_code_flow,
)
from .pkce import generate_pkce

_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_AUTH_BASE_URL = "https://auth.openai.com"
_AUTHORIZE_URL = f"{_AUTH_BASE_URL}/oauth/authorize"
_TOKEN_URL = f"{_AUTH_BASE_URL}/oauth/token"
_REDIRECT_URI = "http://localhost:1455/auth/callback"
_DEVICE_USER_CODE_URL = f"{_AUTH_BASE_URL}/api/accounts/deviceauth/usercode"
_DEVICE_TOKEN_URL = f"{_AUTH_BASE_URL}/api/accounts/deviceauth/token"
_DEVICE_VERIFICATION_URI = f"{_AUTH_BASE_URL}/codex/device"
_DEVICE_REDIRECT_URI = f"{_AUTH_BASE_URL}/deviceauth/callback"
_DEVICE_CODE_TIMEOUT_SECONDS = 15 * 60
_OPENAI_CODEX_BROWSER_LOGIN_METHOD = "browser"
_OPENAI_CODEX_DEVICE_CODE_LOGIN_METHOD = "device_code"
_SCOPE = "openid profile email offline_access"
_JWT_CLAIM_PATH = "https://api.openai.com/auth"


def _create_state() -> str:
    return secrets.token_hex(16)


def _decode_jwt(token: str) -> Optional[Dict[str, Any]]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        result = json.loads(decoded)
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _get_account_id(access_token: str) -> Optional[str]:
    payload = _decode_jwt(access_token)
    if payload is None:
        return None
    auth = payload.get(_JWT_CLAIM_PATH)
    if isinstance(auth, dict):
        account_id = auth.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    return None


def _read_token_response(response: httpx.Response, operation: str) -> OAuthCredential:
    """解析 token endpoint 响应为 credential 基底（边界校验后直达正典形状）。"""
    if response.status_code >= 400:
        text = response.text or response.reason_phrase
        raise RuntimeError(
            f"OpenAI Codex token {operation} failed ({response.status_code}): {text}"
        )

    data = response.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(refresh_token, str)
        or not refresh_token
        or not isinstance(expires_in, (int, float))
    ):
        raise RuntimeError(
            f"OpenAI Codex token {operation} response missing fields: {data}"
        )

    return OAuthCredential(
        access=access_token,
        refresh=refresh_token,
        expires=int(time.time() * 1000) + int(expires_in) * 1000,
    )


def _credentials_from_token(token: OAuthCredential) -> OAuthCredential:
    """补上从 access token JWT 提取的 accountId，产出完整 credential。"""
    account_id = _get_account_id(token.access)
    if not account_id:
        raise RuntimeError("Failed to extract accountId from token")
    return token.model_copy(update={"accountId": account_id})


async def _exchange_authorization_code(
    code: str,
    verifier: str,
    redirect_uri: str = _REDIRECT_URI,
    signal: Optional[AbortSignal] = None,
) -> OAuthCredential:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": _CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
            timeout=30.0,
        )
    return _read_token_response(response, "exchange")


async def _refresh_access_token(refresh_token: str) -> OAuthCredential:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _CLIENT_ID,
            },
            timeout=30.0,
        )
    return _read_token_response(response, "refresh")


@dataclass(frozen=True, kw_only=True)
class _DeviceCodeStart:
    """设备码登录的初始响应（模块内值对象——规则 5）。"""

    device_auth_id: str
    user_code: str
    interval_seconds: float


async def _start_openai_codex_device_auth(
    signal: Optional[AbortSignal] = None,
) -> _DeviceCodeStart:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _DEVICE_USER_CODE_URL,
            json={"client_id": _CLIENT_ID},
            timeout=30.0,
        )

    if response.status_code >= 400:
        if response.status_code == 404:
            raise RuntimeError(
                "OpenAI Codex device code login is not enabled for this server. "
                "Use browser login or verify the server URL."
            )
        body = response.text
        raise RuntimeError(
            f"OpenAI Codex device code request failed with status {response.status_code}"
            f"{body and f': {body}'}"
        )

    data = response.json()
    interval = data.get("interval")
    interval_seconds = (
        float(interval.strip()) if isinstance(interval, str) else interval
    )
    device_auth_id = data.get("device_auth_id")
    user_code = data.get("user_code")
    if (
        not isinstance(device_auth_id, str)
        or not device_auth_id
        or not isinstance(user_code, str)
        or not user_code
        or not isinstance(interval_seconds, (int, float))
        or interval_seconds < 0
    ):
        raise RuntimeError(f"Invalid OpenAI Codex device code response: {data}")

    return _DeviceCodeStart(
        device_auth_id=device_auth_id,
        user_code=user_code,
        interval_seconds=interval_seconds,
    )


@dataclass(frozen=True, kw_only=True)
class _DeviceCodeGrant:
    """设备码轮询终值：授权码 + 配对的 PKCE verifier（模块内值对象）。"""

    authorization_code: str
    code_verifier: str


async def _poll_openai_codex_device_auth(
    device: _DeviceCodeStart, signal: Optional[AbortSignal] = None
) -> _DeviceCodeGrant:
    async def _poll() -> DeviceCodePollResult[_DeviceCodeGrant]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _DEVICE_TOKEN_URL,
                json={
                    "device_auth_id": device.device_auth_id,
                    "user_code": device.user_code,
                },
                timeout=30.0,
            )

        if response.status_code < 400:
            data = response.json()
            auth_code = data.get("authorization_code")
            verifier = data.get("code_verifier")
            if not auth_code or not verifier:
                return DeviceCodePollResult(
                    status="failed",
                    message=f"Invalid OpenAI Codex device auth token response: {data}",
                )
            return DeviceCodePollResult(
                status="complete",
                value=_DeviceCodeGrant(
                    authorization_code=auth_code, code_verifier=verifier
                ),
            )

        if response.status_code in (403, 404):
            return DeviceCodePollResult(status="pending")

        body = response.text
        error_code = None
        try:
            err_data = response.json()
            error = err_data.get("error")
            if isinstance(error, dict):
                error_code = error.get("code")
            else:
                error_code = error
        except Exception:
            pass

        if error_code == "deviceauth_authorization_pending":
            return DeviceCodePollResult(status="pending")
        if error_code == "slow_down":
            return DeviceCodePollResult(status="slow_down")

        return DeviceCodePollResult(
            status="failed",
            message=(
                f"OpenAI Codex device auth failed with status {response.status_code}"
                f"{body and f': {body}'}"
            ),
        )

    return await poll_oauth_device_code_flow(
        DeviceCodePollOptions(
            poll=_poll,
            interval_seconds=device.interval_seconds,
            expires_in_seconds=_DEVICE_CODE_TIMEOUT_SECONDS,
            signal=signal,
        )
    )


@dataclass(frozen=True, kw_only=True)
class _AuthorizationFlow:
    """浏览器授权流的初始参数（模块内值对象）。"""

    verifier: str
    state: str
    url: str


async def _create_authorization_flow(originator: str = "nova") -> _AuthorizationFlow:
    pkce = generate_pkce()
    state = _create_state()
    params = {
        "response_type": "code",
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "scope": _SCOPE,
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": originator,
    }
    url = f"{_AUTHORIZE_URL}?{urlencode(params, quote_via=quote)}"
    return _AuthorizationFlow(verifier=pkce.verifier, state=state, url=url)


_CALLBACK_PORT = 1455


async def _login_openai_codex_device_code(
    interaction: AuthInteraction,
) -> OAuthCredential:
    device = await _start_openai_codex_device_auth(interaction.signal)
    interaction.notify(
        AuthEvent(
            type="device_code",
            user_code=device.user_code,
            verification_uri=_DEVICE_VERIFICATION_URI,
            interval_seconds=device.interval_seconds,
            expires_in_seconds=_DEVICE_CODE_TIMEOUT_SECONDS,
        )
    )
    grant = await _poll_openai_codex_device_auth(device, interaction.signal)
    token = await _exchange_authorization_code(
        grant.authorization_code,
        grant.code_verifier,
        _DEVICE_REDIRECT_URI,
        interaction.signal,
    )
    return _credentials_from_token(token)


async def _login_openai_codex_browser(interaction: AuthInteraction) -> OAuthCredential:
    flow = await _create_authorization_flow()

    interaction.notify(
        AuthEvent(
            type="auth_url",
            url=flow.url,
            instructions="A browser window should open. Complete login to finish.",
        )
    )

    # 收货渠道装配（宿主监听/本地监听/粘贴框竞速）归接线层——流程只声明
    # 需求；开浏览器归宿主（host:openUrl，由交互层经 auth_url 事件触发）。
    code = await interaction.acquire_authorization_code(
        AuthorizationRequest(
            auth_url=flow.url,
            state=flow.state,
            redirect_port=_CALLBACK_PORT,
            timeout_seconds=300.0,
            manual_prompt=AuthPrompt(
                type="manual_code",
                message=(
                    "Complete login in your browser, or paste the "
                    "authorization code / redirect URL here:"
                ),
                placeholder=_REDIRECT_URI,
            ),
            success_html=oauth_success_html(
                "OpenAI authentication completed. You can close this window."
            ),
            error_html=oauth_error_html("Authentication failed."),
        )
    )

    token = await _exchange_authorization_code(
        code, flow.verifier, _REDIRECT_URI, interaction.signal
    )
    return _credentials_from_token(token)


async def _login(interaction: AuthInteraction) -> OAuthCredential:
    method = await interaction.prompt(
        AuthPrompt(
            type="select",
            message="Select OpenAI Codex login method:",
            options=[
                AuthPromptOption(
                    id=_OPENAI_CODEX_BROWSER_LOGIN_METHOD,
                    label="Browser login (default)",
                ),
                AuthPromptOption(
                    id=_OPENAI_CODEX_DEVICE_CODE_LOGIN_METHOD,
                    label="Device code login (headless)",
                ),
            ],
        )
    )

    if method == _OPENAI_CODEX_DEVICE_CODE_LOGIN_METHOD:
        return await _login_openai_codex_device_code(interaction)
    if method != _OPENAI_CODEX_BROWSER_LOGIN_METHOD:
        raise RuntimeError(f"Unknown OpenAI Codex login method: {method}")
    return await _login_openai_codex_browser(interaction)


async def _refresh(
    credential: OAuthCredential, signal: Optional[AbortSignal] = None
) -> OAuthCredential:
    token = await _refresh_access_token(credential.refresh)
    return _credentials_from_token(token)


async def _to_auth(credential: OAuthCredential) -> ModelAuth:
    return ModelAuth(api_key=credential.access)


openai_codex_oauth = OAuthAuth(
    name="OpenAI (ChatGPT Plus/Pro)",
    login=_login,
    refresh=_refresh,
    to_auth=_to_auth,
)

__all__ = ["openai_codex_oauth"]
