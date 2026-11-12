"""OpenAI Codex (ChatGPT OAuth) flow。

对齐 TypeScript ``src/auth/oauth/openai-codex.ts``：
支持浏览器登录（收货经 ``AuthInteraction.acquire_authorization_code``，
渠道装配归 nova_harness 接线层）和设备码登录两种模式。
"""

import base64
import json
import math
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

import httpx
from nova_protocol import (
    AbortedError,
    AbortSignal,
    AuthEvent,
    AuthInteraction,
    AuthorizationRequest,
    AuthPrompt,
    AuthPromptOption,
    ModelAuth,
    NovaBaseModel,
    NovaErrorKind,
    OAuthAuth,
    OAuthCredential,
    RedactedString,
    is_aborted,
)
from pydantic import ConfigDict, Field, ValidationError, field_validator

from ...utils.http import request_with_abort
from .device_code import (
    DeviceCodePollOptions,
    DeviceCodePollResult,
    poll_oauth_device_code_flow,
)
from .errors import (
    OAuthFlowError,
    oauth_http_kind,
    response_error_detail,
    response_json_object,
    validation_error,
    validation_message,
)
from .oauth_page import oauth_error_html, oauth_success_html
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


class _TokenResponse(NovaBaseModel):
    """OpenAI Codex token 端点响应的边界校验模型（第三方不可信输入——规则 3）。

    消息安全：校验失败经 ``validation_error`` 只报字段名，响应体（可能含
    活凭据）永不进错误消息。
    """

    access_token: RedactedString = Field(min_length=1, repr=False)
    refresh_token: RedactedString = Field(min_length=1, repr=False)
    expires_in: float

    # 厂商边界模型按厂商 casing（snake）——关掉枢纽基类的 camel alias
    model_config = ConfigDict(alias_generator=None)

    @field_validator("expires_in", mode="before")
    @classmethod
    def _expires_in_strict_number(cls, value: Any) -> Any:
        # 现状语义逐字保留：拒 bool、拒非数值、拒 <=0、拒非有限
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value <= 0
            or not math.isfinite(value)
        ):
            raise ValueError("expires_in must be a positive finite number")
        return value


def _read_token_response(response: httpx.Response, operation: str) -> OAuthCredential:
    """解析 token endpoint 响应为 credential 基底（边界模型校验后直达正典形状）。"""
    if response.status_code >= 400:
        raise OAuthFlowError(
            f"OpenAI Codex token {operation} failed ({response.status_code}): "
            f"{response_error_detail(response)}",
            kind=oauth_http_kind(response.status_code),
            status_code=response.status_code,
        )

    data = response_json_object(response, f"OpenAI Codex token {operation}")
    try:
        resp = _TokenResponse.model_validate(data)
    except ValidationError as error:
        # from None：ValidationError 的 str 会回显输入值——隐式链一并压掉
        raise validation_error(f"OpenAI Codex token {operation}", error) from None

    return OAuthCredential(
        access=RedactedString(resp.access_token),
        refresh=RedactedString(resp.refresh_token),
        expires=int(time.time() * 1000) + int(resp.expires_in) * 1000,
    )


def _credentials_from_token(token: OAuthCredential) -> OAuthCredential:
    """补上从 access token JWT 提取的 accountId，产出完整 credential。"""
    account_id = _get_account_id(token.access)
    if not account_id:
        raise OAuthFlowError(
            "Failed to extract accountId from token", kind=NovaErrorKind.INTERNAL
        )
    return token.model_copy(update={"accountId": account_id})


async def _exchange_authorization_code(
    code: str,
    verifier: str,
    redirect_uri: str = _REDIRECT_URI,
    signal: Optional[AbortSignal] = None,
) -> OAuthCredential:
    response = await request_with_abort(
        "POST",
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": _CLIENT_ID,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        },
        signal=signal,
    )
    return _read_token_response(response, "exchange")


async def _refresh_access_token(
    refresh_token: str, signal: Optional[AbortSignal] = None
) -> OAuthCredential:
    response = await request_with_abort(
        "POST",
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _CLIENT_ID,
        },
        signal=signal,
    )
    return _read_token_response(response, "refresh")


@dataclass(frozen=True, kw_only=True)
class _DeviceCodeStart:
    """设备码登录的初始响应（模块内值对象——规则 5）。"""

    device_auth_id: RedactedString = field(repr=False)
    user_code: RedactedString = field(repr=False)
    interval_seconds: float


class _DeviceCodeStartResponse(NovaBaseModel):
    """OpenAI Codex deviceauth/usercode 端点响应的边界校验模型。"""

    device_auth_id: RedactedString = Field(min_length=1, repr=False)
    user_code: RedactedString = Field(min_length=1, repr=False)
    interval: float = Field(ge=0)  # 0 合法（与现状一致——仅负数拒绝）

    model_config = ConfigDict(alias_generator=None)  # 厂商 casing（snake）

    @field_validator("interval", mode="before")
    @classmethod
    def _interval_coerce(cls, value: Any) -> Any:
        # 现状语义：字符串数字经 strip 强转；脏字符串落 None 走统一校验报错
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return value


async def _start_openai_codex_device_auth(
    signal: Optional[AbortSignal] = None,
) -> _DeviceCodeStart:
    response = await request_with_abort(
        "POST",
        _DEVICE_USER_CODE_URL,
        json_body={"client_id": _CLIENT_ID},
        signal=signal,
    )

    if response.status_code >= 400:
        if response.status_code == 404:
            raise OAuthFlowError(
                "OpenAI Codex device code login is not enabled for this server. "
                "Use browser login or verify the server URL.",
                kind=NovaErrorKind.BAD_REQUEST,
                status_code=response.status_code,
            )
        raise OAuthFlowError(
            f"OpenAI Codex device code request failed with status "
            f"{response.status_code}: {response_error_detail(response)}",
            kind=oauth_http_kind(response.status_code),
            status_code=response.status_code,
        )

    data = response_json_object(response, "OpenAI Codex device code")
    try:
        resp = _DeviceCodeStartResponse.model_validate(data)
    except ValidationError as error:
        raise validation_error("OpenAI Codex device code", error) from None

    return _DeviceCodeStart(
        device_auth_id=resp.device_auth_id,
        user_code=resp.user_code,
        interval_seconds=resp.interval,
    )


@dataclass(frozen=True, kw_only=True)
class _DeviceCodeGrant:
    """设备码轮询终值：授权码 + 配对的 PKCE verifier（模块内值对象）。"""

    authorization_code: RedactedString = field(repr=False)
    code_verifier: RedactedString = field(repr=False)


class _DevicePollResponse(NovaBaseModel):
    """deviceauth/token 轮询成功响应的边界校验模型（授权码 + PKCE verifier）。

    注：现状对非 str 的真值曾静默容忍（``if not auth_code`` 只挡空值）——
    模型化为 str 是**有意的微收紧**（端点契约本就是字符串，宽松无收益）。
    """

    authorization_code: RedactedString = Field(min_length=1, repr=False)
    code_verifier: RedactedString = Field(min_length=1, repr=False)

    model_config = ConfigDict(alias_generator=None)  # 厂商 casing（snake）


async def _poll_openai_codex_device_auth(
    device: _DeviceCodeStart, signal: Optional[AbortSignal] = None
) -> _DeviceCodeGrant:
    async def _poll() -> DeviceCodePollResult[_DeviceCodeGrant]:
        response = await request_with_abort(
            "POST",
            _DEVICE_TOKEN_URL,
            json_body={
                "device_auth_id": device.device_auth_id,
                "user_code": device.user_code,
            },
            signal=signal,
        )

        if response.status_code < 400:
            data = response_json_object(response, "OpenAI Codex device auth token")
            try:
                resp = _DevicePollResponse.model_validate(data)
            except ValidationError as error:
                return DeviceCodePollResult(
                    status="failed",
                    message=validation_message("OpenAI Codex device auth token", error),
                    error_kind=NovaErrorKind.UPSTREAM_ERROR,
                )
            return DeviceCodePollResult(
                status="complete",
                value=_DeviceCodeGrant(
                    authorization_code=resp.authorization_code,
                    code_verifier=resp.code_verifier,
                ),
            )

        if response.status_code in (403, 404):
            return DeviceCodePollResult(status="pending")

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
                f"OpenAI Codex device auth failed with status "
                f"{response.status_code}: {response_error_detail(response)}"
            ),
            error_kind=oauth_http_kind(response.status_code),
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
class AuthorizationFlow:
    """浏览器授权流的初始参数（不可变值对象——规则 5）。"""

    verifier: str = field(repr=False)
    state: str = field(repr=False)
    url: str = field(repr=False)  # URL 携带 state/code_challenge，不进默认 repr


async def create_authorization_flow(originator: str = "nova") -> AuthorizationFlow:
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
    return AuthorizationFlow(verifier=pkce.verifier, state=state, url=url)


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
    flow = await create_authorization_flow()

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
        raise OAuthFlowError(
            f"Unknown OpenAI Codex login method: {method}",
            kind=NovaErrorKind.INTERNAL,
        )
    return await _login_openai_codex_browser(interaction)


async def _refresh(
    credential: OAuthCredential, signal: Optional[AbortSignal] = None
) -> OAuthCredential:
    if is_aborted(signal):
        raise AbortedError("OpenAI Codex token refresh aborted")
    token = await _refresh_access_token(credential.refresh, signal)
    return _credentials_from_token(token)


async def _to_auth(credential: OAuthCredential) -> ModelAuth:
    return ModelAuth(api_key=credential.access)


openai_codex_oauth = OAuthAuth(
    name="OpenAI (ChatGPT Plus/Pro)",
    is_subscription=True,
    login=_login,
    refresh=_refresh,
    to_auth=_to_auth,
)

__all__ = ["openai_codex_oauth"]
