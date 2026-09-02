"""Auth 解析。

对齐 TypeScript ``src/auth/resolve.ts``：根据 provider.auth、已存储 credential
和环境上下文解析出请求可用的鉴权信息。
"""

from typing import Literal, Optional

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

ModelsErrorCode = Literal[
    "model_source", "model_validation", "provider", "stream", "auth", "oauth"
]


class AuthResolutionOverrides:
    """调用方可传入的鉴权覆盖。"""

    def __init__(
        self,
        apiKey: Optional[str] = None,
        env: Optional[ProviderEnv] = None,
    ):
        self.apiKey = apiKey
        self.env = env


class ModelsError(Exception):
    """Models 集合运行时的错误。"""

    def __init__(
        self,
        code: ModelsErrorCode,
        message: str,
        cause: Optional[BaseException] = None,
    ):
        super().__init__(message)
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

    优先级：
    1. 调用方传入的 api_key override
    2. 已存储的 credential
    3. 环境变量等 ambient 来源
    """
    request_ctx = (
        _overlay_env_auth_context(auth_context, overrides.env)
        if overrides and overrides.env
        else auth_context
    )

    if overrides and overrides.apiKey is not None and auth.apiKey:
        return await _resolve_api_key(
            request_ctx,
            auth.apiKey,
            provider_id,
            ApiKeyCredential(key=overrides.apiKey, env=overrides.env),
        )

    stored = await _read_credential(credentials, provider_id)
    if stored:
        if stored.type == "oauth" and auth.oauth:
            return await _resolve_stored_oauth(
                credentials, provider_id, auth.oauth, stored
            )
        if stored.type == "api_key" and auth.apiKey:
            credential = stored
            if overrides and overrides.env:
                merged_env = {**(stored.env or {}), **overrides.env}
                credential = ApiKeyCredential(key=stored.key, env=merged_env)
            return await _resolve_api_key(
                request_ctx, auth.apiKey, provider_id, credential
            )
        return None

    if auth.apiKey:
        return await _resolve_api_key(request_ctx, auth.apiKey, provider_id, None)

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

        async def fileExists(self, path: str) -> bool:
            return await base.fileExists(path)

    return _OverlayContext()


async def _resolve_stored_oauth(
    credentials: CredentialStore,
    provider_id: str,
    oauth: OAuthAuth,
    stored: OAuthCredential,
) -> Optional[AuthResult]:
    """解析存储的 OAuth credential，必要时刷新。"""
    credential = stored

    if _is_expired(credential.expires):
        try:
            post = await credentials.modify(
                provider_id,
                lambda current: _refresh_if_needed(current, oauth, provider_id),
            )
        except ModelsError:
            raise
        except Exception as error:
            raise ModelsError(
                "auth", f"Credential store modify failed for {provider_id}", error
            )

        if post is None or post.type != "oauth":
            return None
        credential = post

    try:
        auth = await oauth.toAuth(credential)
        return AuthResult(auth=auth, source="OAuth")
    except Exception as error:
        raise ModelsError(
            "oauth", f"OAuth auth derivation failed for {provider_id}", error
        )


def _is_expired(expires: int) -> bool:
    import time

    return int(time.time() * 1000) >= expires


async def _refresh_if_needed(
    current: Optional[Credential],
    oauth: OAuthAuth,
    provider_id: str,
) -> Optional[Credential]:
    """在 store lock 内检查并刷新 OAuth token。"""
    if current is None or current.type != "oauth":
        return None
    if not _is_expired(current.expires):
        return None
    try:
        return await oauth.refresh(current, None)
    except Exception as error:
        raise ModelsError("oauth", f"OAuth refresh failed for {provider_id}", error)


async def _resolve_api_key(
    auth_context: AuthContext,
    api_key: ApiKeyAuth,
    provider_id: str,
    credential: Optional[ApiKeyCredential],
) -> Optional[AuthResult]:
    try:
        return await api_key.resolve({"ctx": auth_context, "credential": credential})
    except Exception as error:
        raise ModelsError(
            "auth", f"API key auth failed for provider {provider_id}: {error}", error
        )


async def _read_credential(
    credentials: CredentialStore, provider_id: str
) -> Optional[Credential]:
    try:
        return await credentials.read(provider_id)
    except Exception as error:
        raise ModelsError(
            "auth", f"Credential store read failed for {provider_id}", error
        )


__all__ = [
    "AuthResolutionOverrides",
    "ModelsError",
    "ModelsErrorCode",
    "resolve_provider_auth",
]
