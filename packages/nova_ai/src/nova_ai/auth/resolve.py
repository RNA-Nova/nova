"""Auth 解析。

对齐 TypeScript ``src/auth/resolve.ts``：根据 provider.auth、已存储 credential
和环境上下文解析出请求可用的鉴权信息。

OAuth 刷新语义（对齐 TS ``resolveStoredOAuth`` 的双重检查锁）：

- **提前刷新窗口**：剩余有效期不足 5 分钟即触发刷新——请求不再携带
  几秒后过期的 token；
- **锁内复查**：乐观检查判定需刷新后，权威检查在 per-provider 刷新锁内进行——
  另一请求刚刷新过的凭据直接复用，全局只刷一次；
- **刷新超时**：刷新网络调用有 15 秒硬超时——走 ``asyncio.timeout`` 独立
  通道抛 ``RequestTimeoutError``（死因分型：超时≠取消，codex
  ``ExecExpiration`` 对位），不借 timeout-signal 伪装成用户取消；
- **无静默回落**：刷新失败不回落环境变量；期间登出（凭据消失）返回 None。
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Literal, Optional

from nova_async import (
    KeyedLockFactory,
    await_with_abort,
    default_keyed_locks,
    operation_signal,
)
from nova_protocol import (
    AbortedError,
    AbortSignal,
    ApiKeyAuth,
    ApiKeyCredential,
    AuthContext,
    AuthResult,
    Credential,
    CredentialStore,
    NovaErrorKind,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
    ProviderEnv,
    RedactedString,
)

from ..utils.errors import RequestTimeoutError, with_cause_detail

ModelsErrorCode = Literal[
    "model_source", "model_validation", "provider", "stream", "auth", "oauth"
]

DEFAULT_OAUTH_MINIMUM_VALIDITY_MS = 5 * 60 * 1000
DEFAULT_OAUTH_REFRESH_TIMEOUT_MS = 15_000


@dataclass(frozen=True, kw_only=True)
class AuthResolutionOverrides:
    """调用方可传入的鉴权覆盖（不可变参数包——规则 4/5）。

    - ``api_key``：调用方显式 key（最高优先级）；``None`` = 不覆盖；
    - ``env``：provider 级环境变量覆盖；``None`` = 用进程环境；
    - ``signal``：取消信号；``None`` = 不取消；
    - ``refresh_locks``：per-provider 刷新锁工厂（编排去重）；
      ``None`` = 用进程内缺省锁（多进程部署由 harness 注入文件锁）。
    """

    api_key: Optional[str] = None
    env: Optional[ProviderEnv] = None
    signal: Optional[AbortSignal] = None
    refresh_locks: Optional[KeyedLockFactory] = None


class ModelsError(Exception):
    """ModelsManager 集合运行时的错误。

    ``code`` 是子系统归属（排查/遥测用）；``kind`` 是可选的用户面分类
    （``nova_error_kind`` 自声明——归一阶梯第 1 级消费）。多数包装点不传
    kind：底层 cause 的分类沿 ``__cause__`` 链上浮，包装不掩盖。消息经
    ``with_cause_detail`` 拼入 cause 链最深层真因（调用方常只看 str(e)）。
    """

    def __init__(
        self,
        code: ModelsErrorCode,
        message: str,
        cause: Optional[BaseException] = None,
        *,
        kind: Optional[NovaErrorKind] = None,
    ):
        super().__init__(with_cause_detail(message, cause))
        self.code = code
        if kind is not None:
            self.nova_error_kind = kind
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
    return await await_with_abort(
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
            ApiKeyCredential(key=RedactedString(overrides.api_key), env=overrides.env),
            signal,
        )

    stored = _read_credential(credentials, provider_id)
    if stored:
        if stored.type == "oauth" and auth.oauth:
            return await _resolve_stored_oauth(
                credentials,
                provider_id,
                auth.oauth,
                stored,
                signal,
                (
                    overrides.refresh_locks
                    if overrides and overrides.refresh_locks
                    else default_keyed_locks
                ),
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
    refresh_locks: KeyedLockFactory = default_keyed_locks,
) -> Optional[AuthResult]:
    """解析存储的 OAuth credential，必要时刷新（per-provider 锁编排，见模块 docstring）。

    刷新序列（复查 → 网络 → 写）在 per-provider 锁内原子执行——网络已挪出
    store（store 同步化），去重锁只挡同 provider；锁等待与网络都可被外层
    AbortScope 真杀。
    """
    minimum_validity_ms = DEFAULT_OAUTH_MINIMUM_VALIDITY_MS

    def _expires_soon(credential: OAuthCredential) -> bool:
        return int(time.time() * 1000) + minimum_validity_ms >= credential.expires

    credential: Credential = stored

    if _expires_soon(credential):
        # 乐观检查判定需刷新；权威检查在 per-provider 锁内复查
        async with refresh_locks(provider_id):
            try:
                current = credentials.read(provider_id)
            except Exception as error:
                raise ModelsError(
                    "auth", f"Credential store read failed for {provider_id}", error
                )
            if current is None or current.type != "oauth":
                return None  # 期间已登出
            if not _expires_soon(current):
                credential = current  # 另一请求/进程刚刷新过——复用（落到 to_auth）
            else:
                try:
                    # 15s 硬超时走 asyncio.timeout 独立通道（RequestTimeoutError），
                    # 不再借 timeout-signal 伪装成用户取消；用户 signal 原样透传
                    async with asyncio.timeout(DEFAULT_OAUTH_REFRESH_TIMEOUT_MS / 1000):
                        refreshed = await oauth.refresh(current, signal)
                except TimeoutError as error:
                    raise RequestTimeoutError(
                        f"OAuth refresh timed out for {provider_id}"
                    ) from error
                except Exception as error:
                    # 取消不落泛型兜底（codex 纪律：每层显式匹配取消分支）——
                    # AbortedError 直通，不包装成 ModelsError 误报
                    if isinstance(error, AbortedError):
                        raise
                    raise ModelsError(
                        "oauth", f"OAuth refresh failed for {provider_id}", error
                    )
                try:
                    credentials.write(provider_id, refreshed)
                except Exception as error:
                    raise ModelsError(
                        "auth",
                        f"Credential store write failed for {provider_id}",
                        error,
                    )
                credential = refreshed
    try:
        auth_result = await oauth.to_auth(credential)
        return AuthResult(auth=auth_result, source="OAuth")
    except Exception as error:
        if isinstance(error, AbortedError):
            raise
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
        if isinstance(error, AbortedError):
            raise
        raise ModelsError(
            "auth", f"API key auth failed for provider {provider_id}", error
        )


def _read_credential(
    credentials: CredentialStore,
    provider_id: str,
) -> Optional[Credential]:
    try:
        return credentials.read(provider_id)
    except Exception as error:
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
