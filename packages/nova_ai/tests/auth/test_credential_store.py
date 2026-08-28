"""InMemoryCredentialStore 测试。"""

import pytest

from nova_ai.auth.credential_store import InMemoryCredentialStore
from nova_ai.types.auth import ApiKeyCredential, OAuthCredential


@pytest.mark.asyncio
async def test_read_write_api_key():
    store = InMemoryCredentialStore()
    cred = ApiKeyCredential(key="sk-test")
    await store.modify("openai", lambda _current: _just(cred))
    assert await store.read("openai") == cred


@pytest.mark.asyncio
async def test_read_missing_returns_none():
    store = InMemoryCredentialStore()
    assert await store.read("missing") is None


@pytest.mark.asyncio
async def test_list_returns_metadata():
    store = InMemoryCredentialStore()
    await store.modify("openai", lambda _current: _just(ApiKeyCredential(key="k1")))
    await store.modify(
        "codex",
        lambda _current: _just(OAuthCredential(access="a", refresh="r", expires=1)),
    )

    infos = await store.list()
    assert len(infos) == 2
    assert {info.provider_id for info in infos} == {"openai", "codex"}


@pytest.mark.asyncio
async def test_delete_removes_credential():
    store = InMemoryCredentialStore()
    await store.modify("openai", lambda _current: _just(ApiKeyCredential(key="k1")))
    assert await store.read("openai") is not None
    await store.delete("openai")
    assert await store.read("openai") is None


@pytest.mark.asyncio
async def test_modify_is_serialized_per_provider():
    store = InMemoryCredentialStore()
    results = []

    async def _slow_set(value):
        async def _fn(_current):
            results.append(value)
            return value

        return _fn

    await store.modify("openai", (await _slow_set(ApiKeyCredential(key="first"))))
    await store.modify("openai", (await _slow_set(ApiKeyCredential(key="second"))))

    cred = await store.read("openai")
    assert cred is not None
    assert cred.key == "second"
    assert results[0].key == "first"
    assert results[1].key == "second"


@pytest.mark.asyncio
async def test_modify_is_serialized_under_concurrency():
    """并发 modify 必须按 provider 串行执行（回归：链上曾存裸协程导致串行化失效）。"""
    import asyncio

    store = InMemoryCredentialStore()
    order = []

    async def _mod(name, delay):
        async def _fn(_current):
            order.append(f"{name}-start")
            await asyncio.sleep(delay)
            order.append(f"{name}-end")
            return ApiKeyCredential(key=name)

        return await store.modify("openai", _fn)

    await asyncio.gather(_mod("A", 0.05), _mod("B", 0.01))
    assert order == ["A-start", "A-end", "B-start", "B-end"]

    # 失败的任务不应毒化后续链
    async def _failing(_current):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await store.modify("openai", _failing)
    await _mod("C", 0)
    cred = await store.read("openai")
    assert cred is not None and cred.key == "C"


async def _just(value):
    return value
