"""Kimi (Moonshot AI) OAuth device code flow.

对齐 Kimi Code ``packages/oauth/src/oauth.ts``：
- OAuth host: https://auth.kimi.com
- client_id: 17e5f671-d194-4dfb-9706-5516cb48c098
- 设备码授权: POST /api/oauth/device_authorization
- 轮询 token:   POST /api/oauth/token (grant_type=device_code)
- 刷新 token:   POST /api/oauth/token (grant_type=refresh_token)

同时复用 nova_ai/auth 内部的 ``poll_oauth_device_code_flow`` 轮询抽象，
支持 ``AbortSignal`` 取消、slow_down、超时等通用能力。

仅实现 device code flow，不接入 managed config / usage / feedback 等 Kimi SDK 能力。
"""

import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from nova_ai.auth import AuthInteraction, OAuthAuth
from nova_ai.utils.sanitize import REDACTED_SECRET, redact_secrets
from nova_async import (
    AbortedError,
    AbortSignal,
    abortable_sleep,
    backoff_delay_ms,
    is_aborted,
)
from nova_protocol import (
    AuthEvent,
    ModelAuth,
    NovaBaseModel,
    NovaErrorKind,
    OAuthCredential,
    RedactedString,
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
)

_DEFAULT_OAUTH_HOST = "https://auth.kimi.com"
_CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
_DEVICE_AUTH_PATH = "/api/oauth/device_authorization"
_TOKEN_PATH = "/api/oauth/token"
_DEVICE_CODE_TIMEOUT_SECONDS = 15 * 60
_DEFAULT_POLL_INTERVAL_SECONDS = 5
_REQUEST_TIMEOUT_SECONDS = 30


def _trusted_http_url(value: Any) -> Optional[str]:
    """服务器返回 URL 的协议白名单校验（仅 http/https）。

    这些 URL 的下游是 ``host:openUrl``——会在用户本机直接打开；
    服务器（或中间人）返回 file:// / 自定义 scheme 会被本机原样执行，
    OAuth 响应面按半不可信输入对待（数据建模标准规则 3）。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("https", "http"):
            return None
        return value
    except Exception:
        return None


def _get_oauth_host() -> str:
    """读取 OAuth host，支持环境变量覆盖。"""
    return (
        os.environ.get("KIMI_CODE_OAUTH_HOST")
        or os.environ.get("KIMI_OAUTH_HOST")
        or _DEFAULT_OAUTH_HOST
    ).rstrip("/")


class _TokenResponse(NovaBaseModel):
    """Kimi token 端点响应的边界校验模型（第三方不可信输入——数据建模规则 3）。

    消息安全：校验失败经 ``validation_error`` 只报字段名，响应体（可能含
    活凭据）永不进错误消息。
    """

    access_token: RedactedString = Field(min_length=1, repr=False)
    refresh_token: RedactedString = Field(min_length=1, repr=False)
    expires_in: float  # lax 强转保留"kimi 容忍字符串数字"的既有语义

    # 厂商边界模型按厂商 casing（snake）——关掉枢纽基类的 camel alias
    model_config = ConfigDict(alias_generator=None)

    @field_validator("expires_in")
    @classmethod
    def _expires_in_positive_finite(cls, value: float) -> float:
        if value <= 0 or not math.isfinite(value):
            raise ValueError("expires_in must be a positive finite number")
        return value


def _token_from_response(data: Dict[str, Any]) -> OAuthCredential:
    """从 token endpoint 响应解析并构造 OAuth credential（边界模型校验后直达正典形状）。"""
    # from None：ValidationError 的 str 会回显输入值——隐式链（__context__）
    # 一并压掉，不让它出现在任何 traceback/日志面
    try:
        resp = _TokenResponse.model_validate(data)
    except ValidationError as error:
        raise validation_error("Kimi OAuth token", error) from None

    expires_at_ms = int(time.time() * 1000) + int(resp.expires_in) * 1000
    return OAuthCredential(
        access=RedactedString(resp.access_token),
        refresh=RedactedString(resp.refresh_token),
        expires=expires_at_ms,
    )


async def _post_form(
    url: str,
    params: Dict[str, str],
    signal: Optional[AbortSignal] = None,
) -> httpx.Response:
    """向 Kimi OAuth endpoint 发送 form-encoded POST。

    走共享的可取消 POST 原语（``request_with_abort``——scope 真杀，举旗即
    杀断在飞请求）；本层只叠加 Kimi 的 Accept 约定与设备标识头。
    """
    return await request_with_abort(
        "POST",
        url,
        data=params,
        headers={"Accept": "application/json"},
        timeout=_REQUEST_TIMEOUT_SECONDS,
        signal=signal,
    )


@dataclass(frozen=True, kw_only=True)
class _DeviceAuthorization:
    """设备码授权响应的已校验形态（模块内值对象——规则 5）。

    ``verification_uri``：``None`` = 服务器未提供（absent 卫生——
    下游 ``AuthEvent.verification_uri`` 以 None 表"本事件不携带"）。
    """

    user_code: RedactedString = field(repr=False)
    device_code: RedactedString = field(repr=False)
    verification_uri: Optional[str] = field(repr=False)
    verification_uri_complete: str = field(repr=False)
    expires_in: float
    interval: float


class _DeviceAuthorizationResponse(NovaBaseModel):
    """Kimi device_authorization 端点响应的边界校验模型（严格字段部分）。

    ``interval``/``expires_in`` 不在模型内——它们是"非法值回落默认"的宽松
    路径（无拒绝、无消息），留在调用点手工处理。
    """

    user_code: RedactedString = Field(min_length=1, repr=False)
    device_code: RedactedString = Field(min_length=1, repr=False)
    verification_uri_complete: str = Field(repr=False)
    verification_uri: Optional[str] = Field(default=None, repr=False)

    model_config = ConfigDict(alias_generator=None)  # 厂商 casing（snake）

    @field_validator("verification_uri_complete")
    @classmethod
    def _complete_trusted(cls, value: str) -> str:
        if _trusted_http_url(value) is None:
            raise ValueError("verification_uri_complete must be http/https URL")
        return value

    @field_validator("verification_uri")
    @classmethod
    def _uri_trusted(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and _trusted_http_url(value) is None:
            raise ValueError("verification_uri must be http/https URL")
        return value


async def _request_device_authorization(
    oauth_host: str, signal: Optional[AbortSignal] = None
) -> _DeviceAuthorization:
    """请求设备码。"""
    url = f"{oauth_host}{_DEVICE_AUTH_PATH}"
    response = await _post_form(
        url,
        {"client_id": _CLIENT_ID},
        signal=signal,
    )

    if response.status_code >= 400:
        raise OAuthFlowError(
            f"Kimi device authorization failed ({response.status_code}): "
            f"{response_error_detail(response)}",
            kind=oauth_http_kind(response.status_code),
            status_code=response.status_code,
        )

    data = response_json_object(response, "Kimi device authorization")
    try:
        resp = _DeviceAuthorizationResponse.model_validate(data)
    except ValidationError as error:
        raise validation_error("Kimi device authorization", error) from None

    interval = data.get("interval")
    if (
        not isinstance(interval, (int, float))
        or isinstance(interval, bool)
        or not math.isfinite(interval)
        or interval <= 0
    ):
        interval = _DEFAULT_POLL_INTERVAL_SECONDS
    expires_in = data.get("expires_in")
    if (
        not isinstance(expires_in, (int, float))
        or isinstance(expires_in, bool)
        or not math.isfinite(expires_in)
        or expires_in <= 0
    ):
        expires_in = _DEVICE_CODE_TIMEOUT_SECONDS

    return _DeviceAuthorization(
        user_code=resp.user_code,
        device_code=resp.device_code,
        verification_uri=resp.verification_uri,
        verification_uri_complete=resp.verification_uri_complete,
        expires_in=expires_in,
        interval=interval,
    )


async def _poll_once(
    oauth_host: str, device_code: str, signal: Optional[AbortSignal] = None
) -> DeviceCodePollResult[OAuthCredential]:
    """单次轮询 device token，转换为通用 DeviceCodePollResult。"""
    url = f"{oauth_host}{_TOKEN_PATH}"
    response = await _post_form(
        url,
        {
            "client_id": _CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        signal=signal,
    )

    # 响应体只解析一次；非 JSON/非对象载荷（WAF、网关 HTML 等）按无载荷
    # 处理，由状态码与错误码逻辑裁决——不得让 JSONDecodeError 逃逸出轮询。
    data: Dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            data = parsed
    except Exception:
        pass

    if response.status_code == 200 and isinstance(data.get("access_token"), str):
        credential = _token_from_response(data)
        return DeviceCodePollResult(
            status="complete",
            value=credential,
        )

    if response.status_code >= 500:
        return DeviceCodePollResult(
            status="failed",
            message=f"Kimi token polling server error ({response.status_code}): "
            f"{response_error_detail(response)}",
            error_kind=oauth_http_kind(response.status_code),
        )

    error_code = (
        data.get("error") if isinstance(data.get("error"), str) else "unknown_error"
    )
    description = (
        data.get("error_description")
        if isinstance(data.get("error_description"), str)
        else ""
    )

    if error_code == "authorization_pending":
        return DeviceCodePollResult(status="pending")
    if error_code == "slow_down":
        # 服务器动态降速指令透传给轮询器（合法正值才用，否则走默认退避）
        interval = data.get("interval")
        interval_seconds = (
            float(interval)
            if isinstance(interval, (int, float))
            and not isinstance(interval, bool)
            and interval > 0
            else None
        )
        return DeviceCodePollResult(
            status="slow_down", interval_seconds=interval_seconds
        )
    if error_code == "expired_token":
        return DeviceCodePollResult(
            status="failed",
            message="Kimi device authorization expired",
            error_kind=NovaErrorKind.UNAUTHORIZED,
        )
    if error_code == "access_denied":
        detail = redact_secrets(description) if description else "authorization denied"
        return DeviceCodePollResult(
            status="failed", message=detail, error_kind=NovaErrorKind.UNAUTHORIZED
        )

    detail = redact_secrets(description) if description else f"{error_code}"
    return DeviceCodePollResult(
        status="failed",
        message=f"Kimi token polling failed ({response.status_code}): {detail}",
        error_kind=(
            NovaErrorKind.UPSTREAM_ERROR
            if response.status_code == 200
            else oauth_http_kind(response.status_code)
        ),
    )


async def _login_device_code(
    interaction: AuthInteraction, signal: Optional[AbortSignal] = None
) -> OAuthCredential:
    """完整的 device code 登录流程；有效期与首 poll 等待均服从服务器响应。"""
    if is_aborted(signal):
        raise AbortedError("Login cancelled")

    oauth_host = _get_oauth_host()
    device = await _request_device_authorization(oauth_host, signal=signal)
    interaction.notify(
        AuthEvent(
            type="device_code",
            user_code=device.user_code,
            verification_uri=device.verification_uri,
            verification_uri_complete=device.verification_uri_complete,
            interval_seconds=device.interval,
            expires_in_seconds=device.expires_in,
        )
    )

    async def _poll() -> DeviceCodePollResult[OAuthCredential]:
        return await _poll_once(oauth_host, device.device_code, signal=signal)

    return await poll_oauth_device_code_flow(
        DeviceCodePollOptions(
            poll=_poll,
            interval_seconds=device.interval,
            expires_in_seconds=device.expires_in,
            wait_before_first_poll=True,
            signal=signal,
        )
    )


async def _login(interaction: AuthInteraction) -> OAuthCredential:
    return await _login_device_code(interaction, interaction.signal)


async def _refresh(
    credential: OAuthCredential, signal: Optional[AbortSignal] = None
) -> OAuthCredential:
    """刷新 Kimi access token。"""
    oauth_host = _get_oauth_host()
    url = f"{oauth_host}{_TOKEN_PATH}"

    max_retries = 3  # 首次 + 三次重试，对齐 pi
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        if is_aborted(signal):
            raise AbortedError("Kimi token refresh aborted")

        try:
            response = await _post_form(
                url,
                {
                    "client_id": _CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": credential.refresh,
                },
                signal=signal,
            )
        except RuntimeError as error:
            last_error = error
            if attempt < max_retries:
                await abortable_sleep(
                    backoff_delay_ms(
                        attempt + 1,
                        base_ms=1000,
                        cap_ms=1000 * 2 ** (max_retries - 1),
                        jitter=0,
                    ),
                    signal,
                )
                continue
            raise

        data: Dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            pass

        if response.status_code == 200 and isinstance(data.get("access_token"), str):
            return _token_from_response(data)

        error_code = data.get("error") if isinstance(data.get("error"), str) else ""
        if response.status_code in (401, 403) or error_code == "invalid_grant":
            error_description = data.get("error_description")
            detail = (
                redact_secrets(error_description)
                if isinstance(error_description, str)
                else ""
            )
            raise OAuthFlowError(
                f"Kimi token refresh unauthorized: {detail or 're-login required'}",
                kind=NovaErrorKind.UNAUTHORIZED,
                status_code=response.status_code,
            )

        # 429/全部 5xx 重试耗尽：不传 kind——归一按 status_code 标准映射。
        last_error = OAuthFlowError(
            f"Kimi token refresh failed ({response.status_code}): "
            f"{response_error_detail(response)}",
            status_code=response.status_code,
        )
        if (
            response.status_code == 429 or response.status_code >= 500
        ) and attempt < max_retries:
            await abortable_sleep(
                backoff_delay_ms(
                    attempt + 1,
                    base_ms=1000,
                    cap_ms=1000 * 2 ** (max_retries - 1),
                    jitter=0,
                ),
                signal,
            )
            continue
        raise last_error

    raise (
        last_error
        if last_error is not None
        else OAuthFlowError("Kimi token refresh failed after retries")
    )


async def _to_auth(credential: OAuthCredential) -> ModelAuth:
    return ModelAuth(api_key=credential.access)


kimi_oauth = OAuthAuth(
    name="Kimi (Moonshot AI)",
    is_subscription=True,
    login=_login,
    refresh=_refresh,
    to_auth=_to_auth,
)

__all__ = ["kimi_oauth"]
