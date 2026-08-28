"""OpenAI Codex (ChatGPT OAuth) flow。

对齐 TypeScript ``src/auth/oauth/openai-codex.ts``：
支持浏览器登录（本地回调服务器）和设备码登录两种模式。
"""

import asyncio
import base64
import json
import secrets
import time
import webbrowser
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

from ...signal import AbortSignal
from ...types.auth import (
    AuthEvent,
    AuthInteraction,
    AuthPrompt,
    AuthPromptOption,
    ModelAuth,
    OAuthAuth,
    OAuthCredential,
)
from ...utils.provider_env import get_provider_env_value
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


def _get_callback_host() -> str:
    return get_provider_env_value("NOVA_OAUTH_CALLBACK_HOST") or "127.0.0.1"


def _create_state() -> str:
    return secrets.token_hex(16)


def _parse_authorization_input(value: str) -> dict:
    value = value.strip()
    if not value:
        return {}

    try:
        url = urlparse(value)
        if url.scheme and url.netloc:
            return {
                "code": _first(url.query, "code"),
                "state": _first(url.query, "state"),
            }
    except Exception:
        pass

    if "#" in value:
        code, state = value.split("#", 1)
        return {"code": code, "state": state}

    if "code=" in value:
        qs = parse_qs(value)
        return {
            "code": _first_qs(qs, "code"),
            "state": _first_qs(qs, "state"),
        }

    return {"code": value}


def _first(query: str, key: str) -> Optional[str]:
    parsed = parse_qs(query)
    return _first_qs(parsed, key)


def _first_qs(parsed: dict, key: str) -> Optional[str]:
    values = parsed.get(key)
    return values[0] if values else None


def _decode_jwt(token: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
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


def _read_token_response(response: httpx.Response, operation: str) -> dict:
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
        not access_token
        or not refresh_token
        or not isinstance(expires_in, (int, float))
    ):
        raise RuntimeError(
            f"OpenAI Codex token {operation} response missing fields: {data}"
        )

    return {
        "access": access_token,
        "refresh": refresh_token,
        "expires": int(time.time() * 1000) + int(expires_in) * 1000,
    }


def _credentials_from_token(token: dict) -> OAuthCredential:
    account_id = _get_account_id(token["access"])
    if not account_id:
        raise RuntimeError("Failed to extract accountId from token")
    return OAuthCredential(
        access=token["access"],
        refresh=token["refresh"],
        expires=token["expires"],
        accountId=account_id,
    )


async def _exchange_authorization_code(
    code: str,
    verifier: str,
    redirect_uri: str = _REDIRECT_URI,
    signal: Optional[AbortSignal] = None,
) -> dict:
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


async def _refresh_access_token(refresh_token: str) -> dict:
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


async def _start_openai_codex_device_auth(signal: Optional[AbortSignal] = None) -> dict:
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
    if (
        not data.get("device_auth_id")
        or not data.get("user_code")
        or not isinstance(interval_seconds, (int, float))
        or interval_seconds < 0
    ):
        raise RuntimeError(f"Invalid OpenAI Codex device code response: {data}")

    return {
        "deviceAuthId": data["device_auth_id"],
        "userCode": data["user_code"],
        "intervalSeconds": interval_seconds,
    }


async def _poll_openai_codex_device_auth(
    device: dict, signal: Optional[AbortSignal] = None
) -> dict:
    async def _poll() -> DeviceCodePollResult[dict]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _DEVICE_TOKEN_URL,
                json={
                    "device_auth_id": device["deviceAuthId"],
                    "user_code": device["userCode"],
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
                value={"authorizationCode": auth_code, "codeVerifier": verifier},
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
            intervalSeconds=device["intervalSeconds"],
            expiresInSeconds=_DEVICE_CODE_TIMEOUT_SECONDS,
            signal=signal,
        )
    )


async def _create_authorization_flow(originator: str = "nova") -> dict:
    pkce = generate_pkce()
    state = _create_state()
    params = {
        "response_type": "code",
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "scope": _SCOPE,
        "code_challenge": pkce["challenge"],
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": originator,
    }
    url = f"{_AUTHORIZE_URL}?{urlencode(params, quote_via=quote)}"
    return {"verifier": pkce["verifier"], "state": state, "url": url}


_CALLBACK_PORT = 1455


async def _start_local_oauth_server(state: str) -> dict:
    """启动本地临时 HTTP 服务器接收 OAuth 回调。

    与 TS 对齐：固定使用 ``localhost:1455``，因为 OpenAI Codex 的
    ``redirect_uri`` 已在 OAuth app 中注册为 ``http://localhost:1455/auth/callback``。
    """
    host = _get_callback_host()
    port = _CALLBACK_PORT
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Optional[dict]] = loop.create_future()
    server: Optional[asyncio.AbstractServer] = None

    async def _handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await reader.readline()
            headers = []
            while True:
                line = await reader.readline()
                if line == b"\r\n":
                    break
                headers.append(line)

            request_str = request.decode("latin-1")
            parts = request_str.split(" ")
            path_and_query = parts[1] if len(parts) > 1 else "/"
            parsed = urlparse(path_and_query)

            if parsed.path != "/auth/callback":
                html = oauth_error_html("Callback route not found.")
                status = "404 Not Found"
            else:
                query = parse_qs(parsed.query)
                got_state = _first_qs(query, "state")
                code = _first_qs(query, "code")

                if got_state != state:
                    html = oauth_error_html("State mismatch.")
                    status = "400 Bad Request"
                elif not code:
                    html = oauth_error_html("Missing authorization code.")
                    status = "400 Bad Request"
                else:
                    html = oauth_success_html(
                        "OpenAI authentication completed. You can close this window."
                    )
                    status = "200 OK"
                    if not future.done():
                        future.set_result({"code": code})

            body = html.encode("utf-8")
            response = (
                f"HTTP/1.1 {status}\r\n"
                f"Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("latin-1") + body
            writer.write(response)
            await writer.drain()
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(_handler, host, port)
    except Exception as exc:
        future.set_result(None)
        return {
            "port": port,
            "close": lambda: None,
            "cancelWait": lambda: None if future.done() else future.set_result(None),
            "waitForCode": lambda: future,
        }

    def _close() -> None:
        if server is not None:
            server.close()

    def _cancel() -> None:
        if not future.done():
            future.set_result(None)

    return {
        "port": port,
        "close": _close,
        "cancelWait": _cancel,
        "waitForCode": lambda: future,
    }


async def _login_openai_codex_device_code(
    interaction: AuthInteraction,
) -> OAuthCredential:
    device = await _start_openai_codex_device_auth(interaction.signal)
    interaction.notify(
        AuthEvent(
            type="device_code",
            userCode=device["userCode"],
            verificationUri=_DEVICE_VERIFICATION_URI,
            intervalSeconds=device["intervalSeconds"],
            expiresInSeconds=_DEVICE_CODE_TIMEOUT_SECONDS,
        )
    )
    code = await _poll_openai_codex_device_auth(device, interaction.signal)
    token = await _exchange_authorization_code(
        code["authorizationCode"],
        code["codeVerifier"],
        _DEVICE_REDIRECT_URI,
        interaction.signal,
    )
    return _credentials_from_token(token)


async def _login_openai_codex_browser(interaction: AuthInteraction) -> OAuthCredential:
    flow = await _create_authorization_flow()
    server = await _start_local_oauth_server(flow["state"])

    interaction.notify(
        AuthEvent(
            type="auth_url",
            url=flow["url"],
            instructions="A browser window should open. Complete login to finish.",
        )
    )

    # 尝试自动打开浏览器
    try:
        webbrowser.open(flow["url"])
    except Exception:
        pass

    manual_code: Optional[str] = None
    manual_error: Optional[Exception] = None

    try:

        async def _prompt_manual() -> None:
            nonlocal manual_code, manual_error
            try:
                manual_code = await interaction.prompt(
                    AuthPrompt(
                        type="manual_code",
                        message=(
                            "Complete login in your browser, or paste the "
                            "authorization code / redirect URL here:"
                        ),
                        placeholder=_REDIRECT_URI,
                    )
                )
                server["cancelWait"]()
            except Exception as exc:
                manual_error = (
                    exc if isinstance(exc, Exception) else RuntimeError(str(exc))
                )
                server["cancelWait"]()

        prompt_task = asyncio.create_task(_prompt_manual())

        try:
            result = await server["waitForCode"]()
        except Exception:
            result = None

        if manual_error:
            raise manual_error

        code = None
        if result and result.get("code"):
            code = result["code"]
        elif manual_code:
            parsed = _parse_authorization_input(manual_code)
            if parsed.get("state") and parsed["state"] != flow["state"]:
                raise RuntimeError("State mismatch")
            code = parsed.get("code")

        if not code:
            await prompt_task
            if manual_error:
                raise manual_error
            if manual_code:
                parsed = _parse_authorization_input(manual_code)
                if parsed.get("state") and parsed["state"] != flow["state"]:
                    raise RuntimeError("State mismatch")
                code = parsed.get("code")

        if not code:
            raise RuntimeError("Missing authorization code")

        token = await _exchange_authorization_code(
            code, flow["verifier"], _REDIRECT_URI, interaction.signal
        )
        return _credentials_from_token(token)
    finally:
        if not prompt_task.done():
            prompt_task.cancel()
        server["close"]()


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
    return ModelAuth(apiKey=credential.access)


openai_codex_oauth = OAuthAuth(
    name="OpenAI (ChatGPT Plus/Pro)",
    login=_login,
    refresh=_refresh,
    toAuth=_to_auth,
)


__all__ = ["openai_codex_oauth"]
