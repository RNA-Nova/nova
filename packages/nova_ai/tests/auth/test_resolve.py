"""Auth 解析测试。"""

import asyncio
import time

import pytest
from nova_protocol import (
    AbortController,
    AbortedError,
    ApiKeyCredential,
    AuthResult,
    ModelAuth,
    NovaErrorKind,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
)

from nova_ai.auth.context import default_provider_auth_context
from nova_ai.auth.credential_store import InMemoryCredentialStore
from nova_ai.auth.helpers import env_api_key_auth
from nova_ai.auth.resolve import (
    AuthResolutionOverrides,
    ModelsError,
    resolve_provider_auth,
)
from nova_ai.utils.errors import RequestTimeoutError


@pytest.mark.asyncio
async def test_resolve_uses_env_api_key_when_nothing_stored(monkeypatch):
    monkeypatch.setenv("TEST_RESOLVE_API_KEY", "env-key")
    auth = ProviderAuth(api_key=env_api_key_auth("test", ["TEST_RESOLVE_API_KEY"]))
    result = await resolve_provider_auth(
        "test", auth, InMemoryCredentialStore(), default_provider_auth_context()
    )
    assert result is not None
    assert result.auth["api_key"] == "env-key"
    assert result.source == "TEST_RESOLVE_API_KEY"


@pytest.mark.asyncio
async def test_resolve_override_api_key_takes_priority(monkeypatch):
    monkeypatch.setenv("TEST_RESOLVE_API_KEY", "env-key")
    auth = ProviderAuth(api_key=env_api_key_auth("test", ["TEST_RESOLVE_API_KEY"]))
    result = await resolve_provider_auth(
        "test",
        auth,
        InMemoryCredentialStore(),
        default_provider_auth_context(),
        AuthResolutionOverrides(api_key="override-key"),
    )
    assert result is not None
    assert result.auth["api_key"] == "override-key"


@pytest.mark.asyncio
async def test_resolve_stored_api_key_takes_priority_over_env():
    store = InMemoryCredentialStore()
    store.write("test", ApiKeyCredential(key="stored-key"))

    auth = ProviderAuth(api_key=env_api_key_auth("test", ["TEST_RESOLVE_API_KEY"]))
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth["api_key"] == "stored-key"
    assert result.source == "stored credential"


@pytest.mark.asyncio
async def test_resolve_stored_oauth_when_not_expired():
    """未进入 5 分钟提前刷新窗的 token 直接复用（对齐 TS expiresSoon 语义）。"""
    store = InMemoryCredentialStore()
    cred = OAuthCredential(
        access="access-token",
        refresh="refresh-token",
        expires=int(time.time() * 1000) + 10 * 60 * 1000,
    )
    store.write("test", cred)

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=None, to_auth=to_auth)
    )
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth["api_key"] == "access-token"
    assert result.source == "OAuth"


@pytest.mark.asyncio
async def test_resolve_stored_oauth_refreshes_when_expired():
    store = InMemoryCredentialStore()
    expired = OAuthCredential(
        access="old-access",
        refresh="refresh-token",
        expires=int(time.time() * 1000) - 1000,
    )
    store.write("test", expired)

    async def refresh(credential: OAuthCredential, _signal=None) -> OAuthCredential:
        return OAuthCredential(
            access="new-access",
            refresh=credential.refresh,
            expires=int(time.time() * 1000) + 60000,
        )

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=refresh, to_auth=to_auth)
    )
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth["api_key"] == "new-access"

    updated = store.read("test")
    assert updated is not None
    assert updated.access == "new-access"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refreshed_validity_ms", "succeeds"),
    [(60 * 60 * 1000, True), (10 * 60 * 1000, False)],
)
async def test_min_oauth_validity_refreshes_and_enforces_postcondition(
    refreshed_validity_ms, succeeds
):
    now = int(time.time() * 1000)
    store = InMemoryCredentialStore()
    store.write(
        "test",
        OAuthCredential(access="old", refresh="refresh", expires=now + 10 * 60 * 1000),
    )

    async def refresh(credential: OAuthCredential, _signal=None) -> OAuthCredential:
        return OAuthCredential(
            access="new",
            refresh=credential.refresh,
            expires=int(time.time() * 1000) + refreshed_validity_ms,
        )

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=refresh, to_auth=to_auth)
    )
    overrides = AuthResolutionOverrides(min_oauth_validity_ms=30 * 60 * 1000)
    if succeeds:
        result = await resolve_provider_auth(
            "test", auth, store, default_provider_auth_context(), overrides
        )
        assert result is not None and result.auth["api_key"] == "new"
    else:
        with pytest.raises(ModelsError, match="expires too soon"):
            await resolve_provider_auth(
                "test", auth, store, default_provider_auth_context(), overrides
            )


@pytest.mark.asyncio
async def test_refresh_aborted_inside_refresh_lock_propagates_aborted_error():
    """刷新在 per-provider 刷新锁内因 signal 抛 AbortedError：直通，不得包成 ModelsError
    （取消不落泛型兜底——否则 abort 会被误报成"OAuth 刷新失败"）。"""
    store = InMemoryCredentialStore()
    expired = OAuthCredential(
        access="old-access",
        refresh="refresh-token",
        expires=int(time.time() * 1000) - 1000,
    )
    store.write("test", expired)

    async def refresh(_credential: OAuthCredential, _signal=None) -> OAuthCredential:
        raise AbortedError("refresh aborted")

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=refresh, to_auth=to_auth)
    )
    with pytest.raises(AbortedError):
        await resolve_provider_auth(
            "test", auth, store, default_provider_auth_context()
        )


@pytest.mark.asyncio
async def test_refresh_timeout_raises_request_timeout_not_aborted(monkeypatch):
    """刷新端点挂死：超时帽经 asyncio.timeout 独立通道触发——死因
    RequestTimeoutError（REQUEST_TIMEOUT 自声明），绝不伪装成用户取消
    （对照：AbortSignal.timeout 时代它与 AbortedError 逐字节相同）。"""
    import nova_ai.auth.resolve as resolve_module

    monkeypatch.setattr(resolve_module, "DEFAULT_OAUTH_REFRESH_TIMEOUT_MS", 50)
    store = InMemoryCredentialStore()
    expired = OAuthCredential(
        access="old-access",
        refresh="refresh-token",
        expires=int(time.time() * 1000) - 1000,
    )
    store.write("test", expired)

    never = asyncio.Event()

    async def refresh(_credential: OAuthCredential, _signal=None) -> OAuthCredential:
        await never.wait()
        raise AssertionError("超时帽生效时不会走到")

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=refresh, to_auth=to_auth)
    )
    with pytest.raises(RequestTimeoutError) as exc_info:
        await resolve_provider_auth(
            "test", auth, store, default_provider_auth_context()
        )
    assert exc_info.value.nova_error_kind is NovaErrorKind.REQUEST_TIMEOUT


@pytest.mark.asyncio
async def test_started_refresh_does_not_receive_caller_signal():
    """轮转关键段只受超时约束，不再消费调用方取消信号。"""
    store = InMemoryCredentialStore()
    store.write(
        "test",
        OAuthCredential(access="old-access", refresh="refresh-token", expires=0),
    )
    seen = []

    async def refresh(credential: OAuthCredential, signal=None) -> OAuthCredential:
        seen.append(signal)
        return OAuthCredential(
            access="new-access", refresh=credential.refresh, expires=60_000
        )

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=refresh, to_auth=to_auth)
    )
    await resolve_provider_auth("test", auth, store, default_provider_auth_context())
    assert seen == [None]


@pytest.mark.asyncio
async def test_cancelled_request_still_persists_rotated_refresh_token():
    """调用方取消只取消等待；已开始的轮转结果必须继续写回。"""
    write_observed = asyncio.Event()

    class _Store(InMemoryCredentialStore):
        def write(self, provider_id, credential):
            super().write(provider_id, credential)
            if credential.type == "oauth" and credential.refresh == "new-refresh":
                write_observed.set()

    store = _Store()
    store.write("test", OAuthCredential(access="old", refresh="old-refresh", expires=0))
    started = asyncio.Event()
    finish = asyncio.Event()
    controller = AbortController()

    async def refresh(credential: OAuthCredential, _signal=None) -> OAuthCredential:
        started.set()
        await finish.wait()
        return OAuthCredential(access="new", refresh="new-refresh", expires=60_000)

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=refresh, to_auth=to_auth)
    )
    request = asyncio.create_task(
        resolve_provider_auth(
            "test",
            auth,
            store,
            default_provider_auth_context(),
            AuthResolutionOverrides(signal=controller.signal),
        )
    )
    await started.wait()
    controller.abort()
    with pytest.raises(AbortedError):
        await request
    assert store.read("test").refresh == "old-refresh"

    finish.set()
    await asyncio.wait_for(write_observed.wait(), timeout=1.0)
    assert store.read("test").refresh == "new-refresh"


@pytest.mark.asyncio
async def test_resolve_overlay_env_from_overrides():
    auth = ProviderAuth(api_key=env_api_key_auth("test", ["TEST_RESOLVE_OVERLAY"]))
    result = await resolve_provider_auth(
        "test",
        auth,
        InMemoryCredentialStore(),
        default_provider_auth_context(),
        AuthResolutionOverrides(env={"TEST_RESOLVE_OVERLAY": "overlay-key"}),
    )
    assert result is not None
    assert result.auth["api_key"] == "overlay-key"


@pytest.mark.asyncio
async def test_resolve_missing_auth_returns_none():
    auth = ProviderAuth()
    result = await resolve_provider_auth(
        "test", auth, InMemoryCredentialStore(), default_provider_auth_context()
    )
    assert result is None


@pytest.mark.asyncio
async def test_resolve_api_key_failure_raises_models_error():
    async def resolve(_input: dict) -> AuthResult:
        raise RuntimeError("boom")

    auth = ProviderAuth(
        api_key=env_api_key_auth("test", ["TEST_RESOLVE_API_KEY"]).__class__(
            name="bad", resolve=resolve
        )
    )
    with pytest.raises(ModelsError) as exc_info:
        await resolve_provider_auth(
            "test", auth, InMemoryCredentialStore(), default_provider_auth_context()
        )
    assert exc_info.value.code == "auth"


@pytest.mark.asyncio
async def test_resolve_stored_oauth_expires_soon_triggers_refresh():
    """剩余有效期不足 5 分钟即提前刷新（对齐 TS 默认窗口，不再等到过期）。"""
    store = InMemoryCredentialStore()
    soon = OAuthCredential(
        access="soon-expiring",
        refresh="refresh-token",
        expires=int(time.time() * 1000) + 60 * 1000,  # 1 分钟后过期
    )
    store.write("test", soon)
    refreshed = []

    async def refresh(credential: OAuthCredential, _signal=None) -> OAuthCredential:
        refreshed.append(credential.access)
        return OAuthCredential(
            access="new-access",
            refresh=credential.refresh,
            expires=int(time.time() * 1000) + 30 * 60 * 1000,
        )

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=refresh, to_auth=to_auth)
    )
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth["api_key"] == "new-access"
    assert refreshed == ["soon-expiring"]


@pytest.mark.asyncio
async def test_resolve_stored_oauth_reuses_concurrent_refresh():
    """双重检查锁：请求读到旧快照，锁内发现已被并发请求刷新——复用零二次刷新。"""
    store = InMemoryCredentialStore()
    soon = OAuthCredential(
        access="soon-expiring",
        refresh="refresh-token",
        expires=int(time.time() * 1000) + 60 * 1000,
    )
    fresh = OAuthCredential(
        access="fresh-access",
        refresh="refresh-token",
        expires=int(time.time() * 1000) + 30 * 60 * 1000,
    )

    class _StaleReadStore(InMemoryCredentialStore):
        """模拟请求 B 在 A 刷新之前读到的旧快照：仅**首次**读返回陈旧值
        （外层快照读），锁内复查读到真实新值。"""

        _served_stale = False

        def read(self, provider_id):
            if not self._served_stale:
                self._served_stale = True
                return soon
            return super().read(provider_id)

    stale_store = _StaleReadStore()
    # store 里已是并发请求 A 刚写入的新凭据
    stale_store.write("test", fresh)

    async def refresh(credential: OAuthCredential, _signal=None) -> OAuthCredential:
        raise AssertionError("锁内复查判定仍新鲜，不应触发刷新")

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=refresh, to_auth=to_auth)
    )
    result = await resolve_provider_auth(
        "test", auth, stale_store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth["api_key"] == "fresh-access"


def test_models_error_includes_cause_detail():
    """ModelsError 的 message 拼接底层原因（对齐 TS withCauseDetail）。"""
    cause = RuntimeError("boom detail")
    err = ModelsError("oauth", "OAuth refresh failed for p", cause)
    assert "boom detail" in str(err)
    # 原因已在 message 中时不重复拼接
    err2 = ModelsError("auth", "failed: boom detail", cause)
    assert str(err2).count("boom detail") == 1


def test_auth_overrides_repr_hides_key_and_env():
    overrides = AuthResolutionOverrides(
        api_key="sk-override-secret", env={"TOKEN": "sk-env-secret"}
    )
    text = repr(overrides)
    assert "sk-override-secret" not in text
    assert "sk-env-secret" not in text
