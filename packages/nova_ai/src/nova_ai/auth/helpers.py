"""Auth 辅助函数。

对齐 TypeScript ``src/auth/helpers.ts``：
- ``env_api_key_auth``：标准 API key 鉴权
- ``lazy_oauth``：延迟加载 OAuth 实现
"""

import asyncio
from typing import Awaitable, Callable, List, Optional

from nova_ai.auth import ApiKeyAuth, ApiKeyAuthInput, AuthInteraction, OAuthAuth
from nova_async import AbortSignal
from nova_protocol import (
    ApiKeyCredential,
    AuthCheck,
    AuthPrompt,
    AuthResult,
    ModelAuth,
    OAuthCredential,
    RedactedString,
)


def env_api_key_auth(name: str, env_vars: List[str]) -> ApiKeyAuth:
    """标准 API key 鉴权：优先用存储的 key，否则依次读取环境变量。"""

    async def resolve(input: ApiKeyAuthInput) -> Optional[AuthResult]:
        ctx = input["ctx"]
        credential = input["credential"]

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
        return ApiKeyCredential(key=RedactedString(key))

    async def check(input: ApiKeyAuthInput) -> Optional[AuthCheck]:
        result = await resolve(input)
        if result and result.auth.get("api_key"):
            return AuthCheck(type="api_key", source=result.source)
        return None

    return ApiKeyAuth(name=name, resolve=resolve, login=login, check=check)


def lazy_oauth(
    name: str,
    load: Callable[[], Awaitable[OAuthAuth]],
    login_label: Optional[str] = None,
    is_subscription: Optional[bool] = None,
) -> OAuthAuth:
    """延迟加载 OAuth 实现（首次使用时才加载完整流程代码）。

    对齐 pi ``helpers.ts`` 的 ``lazyOAuth``（``promise ??= load()``）：
    JS Promise 可重复 await；Python 侧必须缓存 **Task**——裸协程只能
    await 一次，第二次起抛 ``RuntimeError: cannot reuse already awaited
    coroutine``（并发首访同病）。
    """
    task: "Optional[asyncio.Task[OAuthAuth]]" = None

    async def loaded() -> OAuthAuth:
        nonlocal task
        if (
            task is not None
            and task.done()
            and (task.cancelled() or task.exception() is not None)
        ):
            # 死 task 出缓存：加载失败/被取消的 task 留在缓存会永久毒化
            # 后续所有调用（await 它恒抛）
            task = None
        if task is None:
            task = asyncio.ensure_future(load())
        # shield：等待方被取消不得传染共享的加载任务（传染取消 + 缓存 =
        # 永久毒化——一个等待者的 abort 杀死所有未来调用）
        return await asyncio.shield(task)

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
        is_subscription=is_subscription,
        login_label=login_label,
        login=login,
        refresh=refresh,
        to_auth=to_auth,
    )


__all__ = ["env_api_key_auth", "lazy_oauth"]
