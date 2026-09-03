"""Auth 辅助函数。

对齐 TypeScript ``src/auth/helpers.ts``：
- ``env_api_key_auth``：标准 API key 鉴权
- ``lazy_oauth``：延迟加载 OAuth 实现
"""

from typing import Any, Awaitable, Callable, List, Optional

from ..signal import AbortSignal
from ..types.auth import (
    ApiKeyAuth,
    ApiKeyCredential,
    AuthCheck,
    AuthContext,
    AuthInteraction,
    AuthPrompt,
    AuthResult,
    ModelAuth,
    OAuthAuth,
    OAuthCredential,
)


def env_api_key_auth(name: str, env_vars: List[str]) -> ApiKeyAuth:
    """标准 API key 鉴权：优先用存储的 key，否则依次读取环境变量。"""

    async def resolve(input: dict) -> Optional[AuthResult]:
        ctx: AuthContext = input["ctx"]
        credential: Optional[ApiKeyCredential] = input.get("credential")

        if credential and credential.key:
            return AuthResult(
                auth=ModelAuth(api_key=credential.key),
                env=credential.env,
                source="stored credential",
            )

        for env_var in env_vars:
            value = await ctx.env(env_var)
            if value:
                return AuthResult(auth=ModelAuth(api_key=value), source=env_var)

        return None

    async def login(interaction: AuthInteraction) -> ApiKeyCredential:
        key = await interaction.prompt(
            AuthPrompt(type="secret", message=f"Enter {name}")
        )
        return ApiKeyCredential(key=key)

    async def check(input: dict) -> Optional[AuthCheck]:
        result = await resolve(input)
        if result and result.auth.get("api_key"):
            return AuthCheck(type="api_key", source=result.source)
        return None

    return ApiKeyAuth(name=name, resolve=resolve, login=login, check=check)


def lazy_oauth(
    name: str,
    load: Callable[[], Awaitable[OAuthAuth]],
    login_label: Optional[str] = None,
) -> OAuthAuth:
    """延迟加载 OAuth 实现，避免在导入时引入 Node-only 代码。"""
    promise: Optional[Awaitable[OAuthAuth]] = None

    async def loaded() -> OAuthAuth:
        nonlocal promise
        if promise is None:
            promise = load()
        return await promise

    async def login(interaction: AuthInteraction) -> OAuthCredential:
        return await (await loaded()).login(interaction)

    async def refresh(
        credential: OAuthCredential, signal: Optional[AbortSignal] = None
    ) -> OAuthCredential:
        return await (await loaded()).refresh(credential, signal)

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return await (await loaded()).to_auth(credential)

    return OAuthAuth(
        name=name,
        login_label=login_label,
        login=login,
        refresh=refresh,
        to_auth=to_auth,
    )


__all__ = ["env_api_key_auth", "lazy_oauth"]
