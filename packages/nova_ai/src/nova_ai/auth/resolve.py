"""Auth 解析。

对齐 TypeScript ``src/auth/resolve.ts``：根据 provider.auth、已存储 credential
和环境上下文解析出请求可用的鉴权信息。

OAuth 刷新语义（对齐 TS ``resolveStoredOAuth`` 的双重检查锁）：

- **提前刷新窗口**：剩余有效期不足 5 分钟（默认，``min_oauth_validity_ms``
  可加大）即触发刷新——请求不再携带几秒后过期的 token；
- **锁内复查**：乐观检查判定需刷新后，权威检查在 store 锁内进行——
  另一请求刚刷新过的凭据直接复用，全局只刷一次；
- **刷新超时**：刷新网络调用有 15 秒硬超时，与调用方 signal 取并集；
- **无静默回落**：刷新失败不回落环境变量；期间登出（凭据消失）返回 None。
"""

import time
from typing import Literal, Optional

from ..signal import AbortedError, AbortSignal
from ..types.auth import (
    ApiKeyAuth,
    ApiKeyCredential,
    AuthContext,
    AuthResult,
    Credential,
    CredentialStore,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
    ProviderEnv,
)
from ..utils.abort import operation_signal, race_with_abort

ModelsErrorCode = Literal[
    "model_source", "model_validation", "provider", "stream", "auth", "oauth"
]

DEFAULT_OAUTH_MINIMUM_VALIDITY_MS = 5 * 60 * 1000
DEFAULT_OAUTH_REFRESH_TIMEOUT_MS = 15_000


class AuthResolutionOverrides:
    """调用方可传入的鉴权覆盖。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        env: Optional[ProviderEnv] = None,
        signal: Optional["AbortSignal"] = None,
        min_oauth_validity_ms: Optional[int] = None,
    ):
        self.api_key = api_key
        self.env = env
        self.signal = signal
        """要求 OAuth token 至少剩余这么多有效期；缺省用 5 分钟默认窗。"""
        self.min_oauth_validity_ms = min_oauth_validity_ms


def _with_cause_detail(message: str, cause: Optional[BaseException]) -> str:
    """把底层原因拼进 message（对齐 TS withCauseDetail）——调用方常只看 str(e)。"""
    if cause is None:
        return message
    detail = str(cause).strip()
    if not detail or detail in message:
        return message
    return f"{message}: {detail}"


class ModelsError(Exception):
    """Models 集合运行时的错误。"""

    def __init__(
        self,
        code: ModelsErrorCode,
        message: str,
        cause: Optional[BaseException] = None,
    ):
        super().__init__(_with_cause_detail(message, cause))
        self.code = code
        if cause is not None:
            self.__cause__ = cause


async def resolve_provider_auth(
    provider_id: str,
    auth: ProviderAuth,
    credentials: CredentialStore,
    auth_context: AuthContext,
    overrides: Optional[AuthResolutionOverrides] = None,
) -> Optional[AuthResult]:
    """解析单个 provider 的鉴权。

    优先级（凭据占有 provider——有存储凭据时不再咨询环境变量）：
    1. 调用方传入的 api_key override
    2. 已存储的 credential
    3. 环境变量等 ambient 来源
    """
    signal = operation_signal(overrides.signal if overrides else None)
    return await race_with_abort(
        _resolve_provider_auth_inner(
            provider_id, auth, credentials, auth_context, overrides, signal
        ),
        signal,
    )


async def _resolve_provider_auth_inner(
    provider_id: str,
    auth: ProviderAuth,
    credentials: CredentialStore,
    auth_context: AuthContext,
    overrides: Optional[AuthResolutionOverrides],
    signal: AbortSignal,
) -> Optional[AuthResult]:
    signal.throw_if_aborted()
    request_ctx = (
        _overlay_env_auth_context(auth_context, overrides.env)
        if overrides and overrides.env
        else auth_context
    )

    if overrides and overrides.api_key is not None and auth.api_key:
        return await _resolve_api_key(
            request_ctx,
            auth.api_key,
            provider_id,
            ApiKeyCredential(key=overrides.api_key, env=overrides.env),
            signal,
        )

    stored = await _read_credential(credentials, provider_id, signal)
    if stored:
        if stored.type == "oauth" and auth.oauth:
            return await _resolve_stored_oauth(
                credentials,
                provider_id,
                auth.oauth,
                stored,
                signal,
                overrides.min_oauth_validity_ms if overrides else None,
            )
        if stored.type == "api_key" and auth.api_key:
            credential = stored
            if overrides and overrides.env:
                merged_env = {**(stored.env or {}), **overrides.env}
                credential = ApiKeyCredential(key=stored.key, env=merged_env)
            return await _resolve_api_key(
                request_ctx, auth.api_key, provider_id, credential, signal
            )
        return None

    if auth.api_key:
        return await _resolve_api_key(
            request_ctx, auth.api_key, provider_id, None, signal
        )

    return None


def _overlay_env_auth_context(base: AuthContext, env: ProviderEnv) -> AuthContext:
    """用传入 env 覆盖 base env 的上下文。

    对齐 TS ``overlayEnvAuthContext``：空值（空字符串）视为未设置，
    回落到 base 环境。
    """

    class _OverlayContext:
        async def env(self, name: str) -> Optional[str]:
            value = env.get(name)
            if value:
                return value
            return await base.env(name)

        async def file_exists(self, path: str) -> bool:
            return await base.file_exists(path)

    return _OverlayContext()


async def _resolve_stored_oauth(
    credentials: CredentialStore,
    provider_id: str,
    oauth: OAuthAuth,
    stored: OAuthCredential,
    signal: AbortSignal,
    min_validity_ms: Optional[int] = None,
) -> Optional[AuthResult]:
    """解析存储的 OAuth credential，必要时刷新（双重检查锁，见模块 docstring）。"""
    minimum_validity_ms = max(DEFAULT_OAUTH_MINIMUM_VALIDITY_MS, min_validity_ms or 0)

    def _expires_soon(credential: OAuthCredential) -> bool:
        return int(time.time() * 1000) + minimum_validity_ms >= credential.expires

    credential: Credential = stored

    if _expires_soon(credential):
        # 乐观检查判定需刷新；权威检查在 store 锁内进行
        async def _modify(current: Optional[Credential]) -> Optional[Credential]:
            if current is None or current.type != "oauth":
                return None  # 期间已登出
            if not _expires_soon(current):
                return None  # 另一请求/进程刚刷新过——直接复用
            refresh_signal = AbortSignal.any(
                [
                    signal,
                    AbortSignal.timeout(DEFAULT_OAUTH_REFRESH_TIMEOUT_MS),
                ]
            )
            try:
                return await oauth.refresh(current, refresh_signal)
            except Exception as error:
                raise ModelsError(
                    "oauth", f"OAuth refresh failed for {provider_id}", error
                )

        try:
            post = await credentials.modify(provider_id, _modify)
        except ModelsError:
            raise
        except Exception as error:
            raise ModelsError(
                "auth", f"Credential store modify failed for {provider_id}", error
            )

        if post is None or post.type != "oauth":
            return None  # 期间已登出
        credential = post
        # 常规 5 分钟窗触发刷新但不强制 provider 契约；显式调用方
        # （如 bearer-token 导出）才要求刷新后满足请求的最小有效期。
        if min_validity_ms is not None and _expires_soon(credential):
            raise ModelsError(
                "oauth",
                f"OAuth refresh returned a token that expires too soon for {provider_id}",
            )

    try:
        auth_result = await oauth.to_auth(credential)
        return AuthResult(auth=auth_result, source="OAuth")
    except Exception as error:
        raise ModelsError(
            "oauth", f"OAuth auth derivation failed for {provider_id}", error
        )


async def _resolve_api_key(
    auth_context: AuthContext,
    api_key: ApiKeyAuth,
    provider_id: str,
    credential: Optional[ApiKeyCredential],
    signal: AbortSignal,
) -> Optional[AuthResult]:
    try:
        return await api_key.resolve(
            {"ctx": auth_context, "credential": credential, "signal": signal}
        )
    except Exception as error:
        raise ModelsError(
            "auth", f"API key auth failed for provider {provider_id}", error
        )


async def _read_credential(
    credentials: CredentialStore,
    provider_id: str,
    signal: AbortSignal,
) -> Optional[Credential]:
    try:
        return await race_with_abort(credentials.read(provider_id), signal)
    except Exception as error:
        if isinstance(error, AbortedError):
            raise
        raise ModelsError(
            "auth", f"Credential store read failed for {provider_id}", error
        )


__all__ = [
    "AuthResolutionOverrides",
    "ModelsError",
    "ModelsErrorCode",
    "DEFAULT_OAUTH_MINIMUM_VALIDITY_MS",
    "DEFAULT_OAUTH_REFRESH_TIMEOUT_MS",
    "resolve_provider_auth",
]
