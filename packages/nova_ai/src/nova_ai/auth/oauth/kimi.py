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

import asyncio
import math
import os
import platform
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from nova_protocol import (
    AbortSignal,
    AuthEvent,
    AuthInteraction,
    ModelAuth,
    OAuthAuth,
    OAuthCredential,
)

from .device_code import (
    DeviceCodePollOptions,
    DeviceCodePollResult,
    _is_aborted,
    poll_oauth_device_code_flow,
)

_DEFAULT_OAUTH_HOST = "https://auth.kimi.com"
_CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
_DEVICE_AUTH_PATH = "/api/oauth/device_authorization"
_TOKEN_PATH = "/api/oauth/token"
_DEVICE_CODE_TIMEOUT_SECONDS = 15 * 60
_DEFAULT_POLL_INTERVAL_SECONDS = 5
_REQUEST_TIMEOUT_SECONDS = 30

# 进程内稳定的 device id。持久化到磁盘需要上层传入 homeDir，目前先用环境变量 +
# 模块级缓存兜底；跨进程重启会重新生成，不影响功能正确性，仅影响 Kimi 侧设备归因。
_DEVICE_ID: Optional[str] = None


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


def _get_device_id() -> str:
    """获取设备标识，优先环境变量，其次进程内缓存。"""
    global _DEVICE_ID
    env_id = os.environ.get("NOVA_DEVICE_ID")
    if env_id:
        return env_id
    if _DEVICE_ID is None:
        _DEVICE_ID = str(uuid.uuid4())
    return _DEVICE_ID


def _default_device_headers() -> Dict[str, str]:
    """构造 Kimi 设备标识头（可选，与 TS createKimiDeviceHeaders 对齐）。"""
    return {
        "User-Agent": "nova/0.1.0",
        "X-Msh-Platform": "nova",
        "X-Msh-Version": "0.1.0",
        "X-Msh-Device-Name": _ascii_header(platform.node() or "unknown"),
        "X-Msh-Device-Model": _ascii_header(platform.system() or "unknown"),
        "X-Msh-Os-Version": _ascii_header(platform.release() or "unknown"),
        "X-Msh-Device-Id": _get_device_id(),
    }


def _ascii_header(value: str, fallback: str = "unknown") -> str:
    cleaned = "".join(ch for ch in value if "\u0020" <= ch <= "\u007e").strip()
    return cleaned or fallback


def _token_from_response(data: Dict[str, Any]) -> OAuthCredential:
    """从 token endpoint 响应解析并构造 OAuth credential（边界校验后直达正典形状）。"""
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(refresh_token, str)
        or not refresh_token
    ):
        raise RuntimeError(f"OAuth response missing access/refresh token: {data}")

    try:
        expires_in_seconds = float(expires_in)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            f"OAuth response missing invalid expires_in: {data}"
        ) from error

    if expires_in_seconds <= 0 or not math.isfinite(expires_in_seconds):
        raise RuntimeError(f"OAuth response invalid expires_in: {expires_in}")

    expires_at_ms = int(time.time() * 1000) + int(expires_in_seconds) * 1000
    return OAuthCredential(
        access=access_token,
        refresh=refresh_token,
        expires=expires_at_ms,
    )


async def _post_form(
    url: str,
    params: Dict[str, str],
    headers: Optional[Dict[str, str]] = None,
    signal: Optional[AbortSignal] = None,
) -> httpx.Response:
    """向 Kimi OAuth endpoint 发送 form-encoded POST。

    signal 取消复合进在飞请求（watchdog 模式）：举旗即刻打断，
    不再等 httpx 超时兜底。
    """
    merged_headers: Dict[str, str] = {"Accept": "application/json"}
    if headers:
        merged_headers.update(headers)

    async def _do_post() -> httpx.Response:
        try:
            async with httpx.AsyncClient() as client:
                return await client.post(
                    url,
                    data=params,
                    headers=merged_headers,
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
        except httpx.TransportError as error:
            raise RuntimeError(f"OAuth request to {url} failed: {error}") from error

    post_task = asyncio.ensure_future(_do_post())
    if signal is None:
        return await post_task

    watcher = asyncio.ensure_future(signal.wait())
    try:
        done, _pending = await asyncio.wait(
            [post_task, watcher], return_when=asyncio.FIRST_COMPLETED
        )
        if watcher in done and post_task not in done:
            post_task.cancel()
            raise asyncio.CancelledError("Request aborted")
        return post_task.result()
    finally:
        watcher.cancel()
        if not post_task.done():
            post_task.cancel()


@dataclass(frozen=True, kw_only=True)
class _DeviceAuthorization:
    """设备码授权响应的已校验形态（模块内值对象——规则 5）。"""

    user_code: str
    device_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: float
    interval: float


async def _request_device_authorization(
    oauth_host: str, signal: Optional[AbortSignal] = None
) -> _DeviceAuthorization:
    """请求设备码。"""
    url = f"{oauth_host}{_DEVICE_AUTH_PATH}"
    response = await _post_form(
        url,
        {"client_id": _CLIENT_ID},
        headers=_default_device_headers(),
        signal=signal,
    )

    if response.status_code >= 400:
        text = response.text or response.reason_phrase
        raise RuntimeError(
            f"Kimi device authorization failed ({response.status_code}): {text}"
        )

    data = response.json()
    user_code = data.get("user_code")
    device_code = data.get("device_code")
    verification_uri_complete = data.get("verification_uri_complete")
    verification_uri = data.get("verification_uri")
    if (
        not isinstance(user_code, str)
        or not user_code
        or not isinstance(device_code, str)
        or not device_code
        or _trusted_http_url(verification_uri_complete) is None
        or (
            verification_uri is not None and _trusted_http_url(verification_uri) is None
        )
    ):
        raise RuntimeError(f"Invalid Kimi device authorization response: {data}")

    interval = data.get("interval")
    if (
        not isinstance(interval, (int, float))
        or isinstance(interval, bool)
        or interval <= 0
    ):
        interval = _DEFAULT_POLL_INTERVAL_SECONDS
    expires_in = data.get("expires_in")
    if (
        not isinstance(expires_in, (int, float))
        or isinstance(expires_in, bool)
        or expires_in <= 0
    ):
        expires_in = _DEVICE_CODE_TIMEOUT_SECONDS

    return _DeviceAuthorization(
        user_code=user_code,
        device_code=device_code,
        verification_uri=verification_uri if isinstance(verification_uri, str) else "",
        verification_uri_complete=verification_uri_complete,
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
        headers=_default_device_headers(),
        signal=signal,
    )

    if response.status_code == 200 and isinstance(
        response.json().get("access_token"), str
    ):
        credential = _token_from_response(response.json())
        return DeviceCodePollResult(
            status="complete",
            value=credential,
        )

    if response.status_code >= 500:
        text = response.text or response.reason_phrase
        return DeviceCodePollResult(
            status="failed",
            message=f"Kimi token polling server error ({response.status_code}): {text}",
        )

    data = response.json()
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
        )
    if error_code == "access_denied":
        detail = description or "authorization denied"
        return DeviceCodePollResult(status="failed", message=detail)

    detail = description or f"{error_code}"
    return DeviceCodePollResult(
        status="failed",
        message=f"Kimi token polling failed ({response.status_code}): {detail}",
    )


async def _login_device_code(
    interaction: AuthInteraction, signal: Optional[AbortSignal] = None
) -> OAuthCredential:
    """完整的 device code 登录流程。

    与 TS OAuthManager.login() 对齐：只请求一次 device code，然后在本地 15 分钟
    超时内轮询；遇到 expired_token / 超时 / denied 即结束，不会自动重新申请新
    设备码（避免对 UI 重复弹窗）。

    轮询复用 ``poll_oauth_device_code_flow``，slow_down / 超时有统一处理。
    """
    if _is_aborted(signal):
        raise asyncio.CancelledError("Login cancelled")

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
            expires_in_seconds=_DEVICE_CODE_TIMEOUT_SECONDS,
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

    max_retries = 3
    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        if _is_aborted(signal):
            raise RuntimeError("Refresh cancelled")

        try:
            response = await _post_form(
                url,
                {
                    "client_id": _CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": credential.refresh,
                },
                headers=_default_device_headers(),
                signal=signal,
            )
        except httpx.TransportError as error:
            last_error = error
            if attempt < max_retries - 1:
                await asyncio.sleep(2**attempt)
                continue
            raise RuntimeError(f"Kimi token refresh failed: {error}") from error

        if response.status_code == 200 and isinstance(
            response.json().get("access_token"), str
        ):
            return _token_from_response(response.json())

        data = response.json()
        error_code = data.get("error") if isinstance(data.get("error"), str) else ""
        if response.status_code in (401, 403) or error_code == "invalid_grant":
            error_description = data.get("error_description")
            detail = error_description if isinstance(error_description, str) else ""
            raise RuntimeError(
                f"Kimi token refresh unauthorized: {detail or 're-login required'}"
            )

        text = response.text or response.reason_phrase
        last_error = RuntimeError(
            f"Kimi token refresh failed ({response.status_code}): {text}"
        )
        if (
            response.status_code in (429, 500, 502, 503, 504)
            and attempt < max_retries - 1
        ):
            await asyncio.sleep(2**attempt)
            continue
        raise last_error

    raise (
        last_error
        if last_error is not None
        else RuntimeError("Kimi token refresh failed after retries")
    )


async def _to_auth(credential: OAuthCredential) -> ModelAuth:
    return ModelAuth(api_key=credential.access)


kimi_oauth = OAuthAuth(
    name="Kimi (Moonshot AI)",
    login=_login,
    refresh=_refresh,
    to_auth=_to_auth,
)

__all__ = ["kimi_oauth"]
