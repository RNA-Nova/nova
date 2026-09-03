"""远程目录物化包装测试（对齐 TS ``remote-catalog-provider.ts``）。"""

import asyncio

import pytest

from nova_ai.gateway.provider import ModelsPublication, Provider, RefreshModelsContext
from nova_ai.gateway.store import ModelsStoreEntry
from nova_ai.providers.remote_catalog import (
    merge_models,
    remote_models,
    with_remote_catalog,
)
from nova_ai.signal import AbortController
from nova_ai.types import (
    Context,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    UserMessage,
)

GENERATED_AT = 1000


def _model(model_id: str, provider: str = "moonshotai") -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=provider,
        base_url="https://api.example.com/v1",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )


def _provider() -> Provider:
    return Provider(
        id="moonshotai",
        name="Moonshot AI",
        models=[_model("kimi-k2.5")],
    )


class _Context:
    """最小 RefreshModelsContext 替身（publish 收集产物供断言）。"""

    def __init__(self, stored=None, allow_network=True, credential=None, force=False):
        self.stored = stored
        self.allow_network = allow_network
        self.signal = AbortController().signal
        self.credential = credential
        self.force = force
        self.publications: list = []

    async def publish(self, publication: ModelsPublication) -> bool:
        self.publications.append(publication)
        if publication.update is not None:
            publication.update()
        return True


def _wrap(fetch_catalog, local_generated_at=GENERATED_AT):
    return with_remote_catalog(_provider(), local_generated_at, fetch_catalog)


def _make_fetcher(status, models=None, etag=None, last_modified=None, calls=None):
    async def _fetch(signal, validator, credential):
        if calls is not None:
            calls.append({"validator": validator})
        if status != 200:
            return type(
                "O",
                (),
                {
                    "status": status,
                    "models_fields": [],
                    "etag": None,
                    "last_modified": None,
                },
            )()
        return type(
            "O",
            (),
            {
                "status": 200,
                "models_fields": [
                    {**m, "id": m.get("id", mid)} for mid, m in (models or {}).items()
                ]
                + [dict(m) for m in []],
                "etag": etag,
                "last_modified": last_modified,
            },
        )()

    return _fetch


class TestMergeModels:
    def test_same_id_override_and_new_add(self):
        baseline = [_model("a"), _model("b")]
        dynamic = [_model("b"), _model("c")]
        merged = merge_models(baseline, dynamic)
        assert [m.id for m in merged] == ["a", "b", "c"]
        assert merged[1] is dynamic[0]


class TestRemoteModelsRaceGuard:
    def test_none_entry_returns_empty(self):
        assert remote_models(None, GENERATED_AT) == []

    def test_missing_last_modified_ignored(self):
        """缓存缺 last_modified：早于基线的语义 → 忽略（新种子赢）。"""
        entry = ModelsStoreEntry(models=[_model("x")], checked_at=1)
        assert remote_models(entry, GENERATED_AT) == []

    def test_older_than_baseline_ignored(self):
        entry = ModelsStoreEntry(models=[_model("x")], checked_at=1, last_modified=999)
        assert remote_models(entry, GENERATED_AT) == []

    def test_newer_than_baseline_applied(self):
        entry = ModelsStoreEntry(models=[_model("x")], checked_at=1, last_modified=1001)
        assert len(remote_models(entry, GENERATED_AT)) == 1


class TestWithRemoteCatalog:
    def test_baseline_visible_before_refresh(self):
        provider = _wrap(_make_fetcher(200, models={"new": {"name": "new"}}))
        assert [m.id for m in provider.get_models()] == ["kimi-k2.5"]

    def test_restore_phase_applies_fresh_overlay(self):
        async def main():
            fresh_entry = ModelsStoreEntry(
                models=[_model("fresh-model")],
                checked_at=1,
                last_modified=GENERATED_AT + 1,
            )
            calls = []

            async def fetch(signal, validator, credential):
                calls.append(1)
                return _make_fetcher(200, models={})(signal, validator, credential)

            fetcher = fetch
            provider = _wrap(fetcher)
            context = _Context(stored=fresh_entry, allow_network=False)
            await provider.refresh_models(context)
            return calls, [m.id for m in provider.get_models()]

        calls, ids = asyncio.run(main())
        assert calls == []  # 离线：不拉网络
        assert "fresh-model" in ids  # overlay 已恢复

    def test_ttl_skip_within_window(self):
        """4 小时窗口内且已恢复：跳过网络拉取。"""
        checked_at = _now_ms_safe()
        fresh_entry = ModelsStoreEntry(
            models=[_model("cached-model")],
            checked_at=checked_at,
            last_modified=GENERATED_AT + 1,
        )
        calls = []

        async def fetch(signal, validator, credential):
            calls.append(1)
            return _make_fetcher(200, models={})(signal, validator, credential)

        provider = _wrap(fetch)

        async def main():
            context = _Context(stored=fresh_entry, allow_network=True)
            await provider.refresh_models(context)

        asyncio.run(main())
        assert calls == []  # TTL 窗口内不拉

    def test_success_merges_and_persists(self):
        async def main():
            calls = []
            fetch = _make_fetcher(
                200,
                models={
                    "kimi-k2.5": {
                        "id": "kimi-k2.5",
                        "name": "kimi-k2.5",
                        "api": "openai-completions",
                        "base_url": "https://api.example.com/v1",
                        "reasoning": False,
                        "input_types": ["text"],
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cache_read": 0,
                            "cache_write": 0,
                        },
                        "context_window": 262144,
                        "max_tokens": 8192,
                    },
                    "brand-new": {
                        "id": "brand-new",
                        "name": "brand-new",
                        "api": "openai-completions",
                        "base_url": "https://api.example.com/v1",
                        "reasoning": True,
                        "input_types": ["text"],
                        "cost": {
                            "input": 1,
                            "output": 2,
                            "cache_read": 0,
                            "cache_write": 0,
                        },
                        "context_window": 128000,
                        "max_tokens": 4096,
                    },
                },
                etag='"v2"',
                last_modified=GENERATED_AT + 5,
                calls=calls,
            )
            provider = _wrap(fetch)
            context = _Context()
            await provider.refresh_models(context)
            ids = [m.id for m in provider.get_models()]
            assert "kimi-k2.5" in ids and "brand-new" in ids
            assert context.publications[-1].persist.etag == '"v2"'
            assert calls[0]["validator"] is None  # 首次无缓存不 reflecting

        asyncio.run(main())

    def test_404_opts_out(self):
        async def main():
            fetch = _make_fetcher(404)
            provider = _wrap(fetch)
            context = _Context()
            await provider.refresh_models(context)
            persist = context.publications[-1].persist
            assert persist.last_modified == 0  # 主动退出，不再硬重试
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5"]

        asyncio.run(main())

    def test_transient_failure_keeps_cache_and_raises(self):
        async def main():
            cached = ModelsStoreEntry(
                models=[_model("cached")],
                checked_at=1,
                last_modified=GENERATED_AT + 1,
                etag='"v1"',
            )
            fetch = _make_fetcher(500)
            provider = _wrap(fetch)
            context = _Context(stored=cached)
            with pytest.raises(RuntimeError, match="500"):
                await provider.refresh_models(context)
            # 缓存体保持有效（保缓存语义），仅推进检查时间
            persist = context.publications[-1].persist
            assert persist.etag == '"v1"'
            assert persist.models[0].id == "cached"

        asyncio.run(main())

    def test_context_tools_param_unused_but_signature_stable(self):
        """refresh_models 契约面与 _DynamicProvider 一致（context 单参）。"""
        provider = _wrap(_make_fetcher(200))
        import inspect

        assert list(inspect.signature(provider.refresh_models).parameters) == [
            "context"
        ]


def _now_ms_safe():
    import time

    return int(time.time() * 1000)
