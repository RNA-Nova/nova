"""Auth 解析测试。"""

import asyncio
import time

import pytest
from nova_protocol import (
    AbortController,
    AbortedError,
    ApiKeyAuth,
    ApiKeyAuthInput,
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
from tests._helpers.auth import (
    api_key_credential,
    never_login,
    never_refresh,
    oauth_credential,
)


@pytest.mark.asyncio
async def test_resolve_uses_env_api_key_when_nothing_stored(monkeypatch):
    monkeypatch.setenv("TEST_RESOLVE_API_KEY", "env-key")
    auth = ProviderAuth(api_key=env_api_key_auth("test", ["TEST_RESOLVE_API_KEY"]))
    result = await resolve_provider_auth(
        "test", auth, InMemoryCredentialStore(), default_provider_auth_context()
    )
    assert result is not None
    assert result.auth.get("api_key") == "env-key"
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
    assert result.auth.get("api_key") == "override-key"


@pytest.mark.asyncio
async def test_resolve_stored_api_key_takes_priority_over_env():
    store = InMemoryCredentialStore()
    store.write("test", api_key_credential(key="stored-key"))

    auth = ProviderAuth(api_key=env_api_key_auth("test", ["TEST_RESOLVE_API_KEY"]))
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth.get("api_key") == "stored-key"
    assert result.source == "stored credential"


@pytest.mark.asyncio
async def test_resolve_stored_oauth_when_not_expired():
    """未进入 5 分钟提前刷新窗的 token 直接复用（对齐 TS expiresSoon 语义）。"""
    store = InMemoryCredentialStore()
    cred = oauth_credential(
        access="access-token",
        refresh="refresh-token",
        expires=int(time.time() * 1000) + 10 * 60 * 1000,
    )
    store.write("test", cred)

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(
            name="oauth", login=never_login, refresh=never_refresh, to_auth=to_auth
        )
    )
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth.get("api_key") == "access-token"
    assert result.source == "OAuth"


@pytest.mark.asyncio
async def test_resolve_stored_oauth_refreshes_when_expired():
    store = InMemoryCredentialStore()
    expired = oauth_credential(
        access="old-access",
        refresh="refresh-token",
        expires=int(time.time() * 1000) - 1000,
    )
    store.write("test", expired)

    async def refresh(credential: OAuthCredential, _signal=None) -> OAuthCredential:
        return oauth_credential(
            access="new-access",
            refresh=credential.refresh,
            expires=int(time.time() * 1000) + 60000,
        )

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(
            name="oauth", login=never_login, refresh=refresh, to_auth=to_auth
        )
    )
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth.get("api_key") == "new-access"

    updated = store.read("test")
    assert isinstance(updated, OAuthCredential)
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
        oauth_credential(access="old", refresh="refresh", expires=now + 10 * 60 * 1000),
    )

    async def refresh(credential: OAuthCredential, _signal=None) -> OAuthCredential:
        return oauth_credential(
            access="new",
            refresh=credential.refresh,
            expires=int(time.time() * 1000) + refreshed_validity_ms,
        )

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(
            name="oauth", login=never_login, refresh=refresh, to_auth=to_auth
        )
    )
    overrides = AuthResolutionOverrides(min_oauth_validity_ms=30 * 60 * 1000)
    if succeeds:
        result = await resolve_provider_auth(
            "test", auth, store, default_provider_auth_context(), overrides
        )
        assert result is not None and result.auth.get("api_key") == "new"
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
    expired = oauth_credential(
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
        oauth=OAuthAuth(
            name="oauth", login=never_login, refresh=refresh, to_auth=to_auth
        )
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
    expired = oauth_credential(
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
        oauth=OAuthAuth(
            name="oauth", login=never_login, refresh=refresh, to_auth=to_auth
        )
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
        oauth_credential(access="old-access", refresh="refresh-token", expires=0),
    )
    seen = []

    async def refresh(credential: OAuthCredential, signal=None) -> OAuthCredential:
        seen.append(signal)
        return oauth_credential(
            access="new-access", refresh=credential.refresh, expires=60_000
        )

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(
            name="oauth", login=never_login, refresh=refresh, to_auth=to_auth
        )
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
    store.write(
        "test", oauth_credential(access="old", refresh="old-refresh", expires=0)
    )
    started = asyncio.Event()
    finish = asyncio.Event()
    controller = AbortController()

    async def refresh(credential: OAuthCredential, _signal=None) -> OAuthCredential:
        started.set()
        await finish.wait()
        return oauth_credential(access="new", refresh="new-refresh", expires=60_000)

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(
            name="oauth", login=never_login, refresh=refresh, to_auth=to_auth
        )
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
    after_abort = store.read("test")
    assert isinstance(after_abort, OAuthCredential)
    assert after_abort.refresh == "old-refresh"

    finish.set()
    await asyncio.wait_for(write_observed.wait(), timeout=1.0)
    after_write = store.read("test")
    assert isinstance(after_write, OAuthCredential)
    assert after_write.refresh == "new-refresh"


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
    assert result.auth.get("api_key") == "overlay-key"


@pytest.mark.asyncio
async def test_resolve_missing_auth_returns_none():
    auth = ProviderAuth()
    result = await resolve_provider_auth(
        "test", auth, InMemoryCredentialStore(), default_provider_auth_context()
    )
    assert result is None


@pytest.mark.asyncio
async def test_resolve_api_key_failure_raises_models_error():
    async def resolve(_input: ApiKeyAuthInput) -> AuthResult:
        raise RuntimeError("boom")

    auth = ProviderAuth(api_key=ApiKeyAuth(name="bad", resolve=resolve))
    with pytest.raises(ModelsError) as exc_info:
        await resolve_provider_auth(
            "test", auth, InMemoryCredentialStore(), default_provider_auth_context()
        )
    assert exc_info.value.code == "auth"


@pytest.mark.asyncio
async def test_resolve_stored_oauth_expires_soon_triggers_refresh():
    """剩余有效期不足 5 分钟即提前刷新（对齐 TS 默认窗口，不再等到过期）。"""
    store = InMemoryCredentialStore()
    soon = oauth_credential(
        access="soon-expiring",
        refresh="refresh-token",
        expires=int(time.time() * 1000) + 60 * 1000,  # 1 分钟后过期
    )
    store.write("test", soon)
    refreshed = []

    async def refresh(credential: OAuthCredential, _signal=None) -> OAuthCredential:
        refreshed.append(credential.access)
        return oauth_credential(
            access="new-access",
            refresh=credential.refresh,
            expires=int(time.time() * 1000) + 30 * 60 * 1000,
        )

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(
            name="oauth", login=never_login, refresh=refresh, to_auth=to_auth
        )
    )
    result = await resolve_provider_auth(
        "test", auth, store, default_provider_auth_context()
    )
    assert result is not None
    assert result.auth.get("api_key") == "new-access"
    assert refreshed == ["soon-expiring"]


@pytest.mark.asyncio
async def test_resolve_stored_oauth_reuses_concurrent_refresh():
    """两个真实并发请求共享一次刷新；锁内复查防止第二次重复刷新。"""
    store = InMemoryCredentialStore()
    soon = oauth_credential(
        access="soon-expiring",
        refresh="refresh-token",
        expires=int(time.time() * 1000) + 60 * 1000,
    )
    store.write("test", soon)
    started = asyncio.Event()
    release = asyncio.Event()
    refreshes = 0

    async def refresh(credential: OAuthCredential, _signal=None) -> OAuthCredential:
        nonlocal refreshes
        refreshes += 1
        started.set()
        await release.wait()
        return oauth_credential(
            access="fresh-access",
            refresh="rotated-refresh",
            expires=int(time.time() * 1000) + 30 * 60 * 1000,
        )

    async def to_auth(credential: OAuthCredential) -> ModelAuth:
        return ModelAuth(api_key=credential.access)

    auth = ProviderAuth(
        oauth=OAuthAuth(
            name="oauth", login=never_login, refresh=refresh, to_auth=to_auth
        )
    )
    first = asyncio.create_task(
        resolve_provider_auth("test", auth, store, default_provider_auth_context())
    )
    second = asyncio.create_task(
        resolve_provider_auth("test", auth, store, default_provider_auth_context())
    )
    await started.wait()
    release.set()
    results = await asyncio.gather(first, second)
    assert refreshes == 1
    assert [result.auth.get("api_key") for result in results if result] == [
        "fresh-access",
        "fresh-access",
    ]
    rotated = store.read("test")
    assert isinstance(rotated, OAuthCredential)
    assert rotated.refresh == "rotated-refresh"


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
