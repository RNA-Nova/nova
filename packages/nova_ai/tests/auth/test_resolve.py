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
    auth = ProviderAuth(apiKey=env_api_key_auth("test", ["TEST_RESOLVE_API_KEY"]))
    result = await resolve_provider_auth(
        "test", auth, InMemoryCredentialStore(), default_provider_auth_context()
    )
    assert result is not None
    assert result.auth["apiKey"] == "env-key"
    assert result.source == "TEST_RESOLVE_API_KEY"


@pytest.mark.asyncio
async def test_resolve_override_api_key_takes_priority(monkeypatch):
    monkeypatch.setenv("TEST_RESOLVE_API_KEY", "env-key")
    auth = ProviderAuth(apiKey=env_api_key_auth("test", ["TEST_RESOLVE_API_KEY"]))
    result = await resolve_provider_auth(
        "test",
        auth,
        InMemoryCredentialStore(),
        default_provider_auth_context(),
        AuthResolutionOverrides(apiKey="override-key"),
    )
    assert result is not None
    assert result.auth["apiKey"] == "override-key"


@pytest.mark.asyncio
async def test_resolve_stored_api_key_takes_priority_over_env():
    store = InMemoryCredentialStore()
    await store.modify(
        "test", lambda _current: _just(ApiKeyCredential(key="stored-key"))
    )

    auth = ProviderAuth(apiKey=env_api_key_auth("test", ["TEST_RESOLVE_API_KEY"]))
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth["apiKey"] == "stored-key"
    assert result.source == "stored credential"


@pytest.mark.asyncio
async def test_resolve_stored_oauth_when_not_expired():
    store = InMemoryCredentialStore()
    cred = OAuthCredential(
        access="access-token",
        refresh="refresh-token",
        expires=int(time.time() * 1000) + 60000,
    )
    await store.modify("test", lambda _current: _just(cred))

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(apiKey=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=None, toAuth=to_auth)
    )
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth["apiKey"] == "access-token"
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
        return ModelAuth(apiKey=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(name="oauth", login=None, refresh=refresh, toAuth=to_auth)
    )
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth["apiKey"] == "new-access"

    updated = await store.read("test")
    assert updated is not None
    assert updated.access == "new-access"


@pytest.mark.asyncio
async def test_resolve_overlay_env_from_overrides():
    auth = ProviderAuth(apiKey=env_api_key_auth("test", ["TEST_RESOLVE_OVERLAY"]))
    result = await resolve_provider_auth(
        "test",
        auth,
        InMemoryCredentialStore(),
        default_provider_auth_context(),
        AuthResolutionOverrides(env={"TEST_RESOLVE_OVERLAY": "overlay-key"}),
    )
    assert result is not None
    assert result.auth["apiKey"] == "overlay-key"


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
        apiKey=env_api_key_auth("test", ["TEST_RESOLVE_API_KEY"]).__class__(
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
