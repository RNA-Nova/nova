"""Auth 解析测试。"""

import time

import pytest

from nova_ai.auth.context import default_provider_auth_context
from nova_ai.auth.credential_store import InMemoryCredentialStore
from nova_ai.auth.helpers import env_api_key_auth
from nova_ai.auth.resolve import (
    AuthResolutionOverrides,
    ModelsError,
    resolve_provider_auth,
)
from nova_ai.types.auth import (
    ApiKeyCredential,
    AuthResult,
    ModelAuth,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
)


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
    await store.modify(
        "test", lambda _current: _just(ApiKeyCredential(key="stored-key"))
    )

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
    await store.modify("test", lambda _current: _just(cred))

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
    await store.modify("test", lambda _current: _just(expired))

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

    updated = await store.read("test")
    assert updated is not None
    assert updated.access == "new-access"


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


async def _just(value):
    return value


@pytest.mark.asyncio
async def test_resolve_stored_oauth_expires_soon_triggers_refresh():
    """剩余有效期不足 5 分钟即提前刷新（对齐 TS 默认窗口，不再等到过期）。"""
    store = InMemoryCredentialStore()
    soon = OAuthCredential(
        access="soon-expiring",
        refresh="refresh-token",
        expires=int(time.time() * 1000) + 60 * 1000,  # 1 分钟后过期
    )
    await store.modify("test", lambda _current: _just(soon))
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
        """模拟请求 B 在 A 刷新之前读到的旧快照（读 soon，锁内见 fresh）。"""

        async def read(self, provider_id):
            return soon

    stale_store = _StaleReadStore()
    # store 里已是并发请求 A 刚写入的新凭据
    await stale_store.modify("test", lambda _current: _just(fresh))

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
