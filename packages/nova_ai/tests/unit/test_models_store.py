"""ModelsStore / _DynamicProvider / filter_models 测试。"""

import asyncio
from typing import List

import pytest

from nova_ai.gateway import InMemoryModelsStore, ModelsStoreEntry, RefreshModelsContext
from nova_ai.providers import create_provider
from nova_ai.types import Context, KnownApi, Model, ModelCost, UserMessage


def _make_model(model_id: str = "test", provider: str = "test") -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=provider,
        base_url="https://example.com",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )


class TestInMemoryModelsStore:
    @pytest.mark.asyncio
    async def test_read_write_delete(self):
        store = InMemoryModelsStore()
        entry = ModelsStoreEntry(models=[_make_model()], checked_at=123)

        # 初始为空
        assert await store.read("p1") is None

        # 写入
        await store.write("p1", entry)
        read = await store.read("p1")
        assert read is not None
        assert len(read.models) == 1
        assert read.checked_at == 123

        # 删除
        await store.delete("p1")
        assert await store.read("p1") is None

    @pytest.mark.asyncio
    async def test_read_returns_copy(self):
        store = InMemoryModelsStore()
        model = _make_model()
        entry = ModelsStoreEntry(models=[model], checked_at=123)
        await store.write("p1", entry)

        read1 = await store.read("p1")
        read2 = await store.read("p1")
        assert read1 is not read2
        assert read1.models is not read2.models


class TestDynamicProvider:
    @pytest.mark.asyncio
    async def test_refresh_models_merges_dynamic_models(self):
        baseline = [_make_model("b1")]
        dynamic = [_make_model("d1"), _make_model("d2")]

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            return dynamic

        provider = create_provider(
            id="test",
            name="Test",
            models=baseline,
            fetch_models=fetch_models,
        )

        # 初始只有 baseline
        assert len(provider.get_models()) == 1

        # refresh 后合并
        await provider.refresh_models(RefreshModelsContext())
        models = provider.get_models()
        assert len(models) == 3
        ids = {m.id for m in models}
        assert ids == {"b1", "d1", "d2"}

    @pytest.mark.asyncio
    async def test_refresh_models_overwrites_baseline(self):
        baseline = [_make_model("m1")]
        dynamic = [_make_model("m1")]  # 同 id，覆盖 baseline

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            return dynamic

        provider = create_provider(
            id="test",
            name="Test",
            models=baseline,
            fetch_models=fetch_models,
        )
        await provider.refresh_models(RefreshModelsContext())

        models = provider.get_models()
        assert len(models) == 1
        assert models[0].id == "m1"

    @pytest.mark.asyncio
    async def test_refresh_models_persists_to_store(self):
        store = InMemoryModelsStore()
        dynamic = [_make_model("d1")]

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            return dynamic

        provider = create_provider(
            id="test",
            name="Test",
            fetch_models=fetch_models,
        )

        from nova_ai.gateway import _ProviderModelsStoreAdapter

        adapter = _ProviderModelsStoreAdapter(store, "test")
        await provider.refresh_models(RefreshModelsContext(store=adapter))

        stored = await store.read("test")
        assert stored is not None
        assert len(stored.models) == 1
        assert stored.models[0].id == "d1"
        assert stored.checked_at is not None

    @pytest.mark.asyncio
    async def test_refresh_models_restores_from_store(self):
        store = InMemoryModelsStore()
        dynamic = [_make_model("d1")]
        await store.write("test", ModelsStoreEntry(models=dynamic, checked_at=123))

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            return [_make_model("d2")]

        provider = create_provider(
            id="test",
            name="Test",
            fetch_models=fetch_models,
        )

        from nova_ai.gateway import _ProviderModelsStoreAdapter

        adapter = _ProviderModelsStoreAdapter(store, "test")

        # 先离线恢复
        await provider.refresh_models(
            RefreshModelsContext(store=adapter, allow_network=False)
        )
        models = provider.get_models()
        assert len(models) == 1
        assert models[0].id == "d1"

        # 再在线刷新
        await provider.refresh_models(RefreshModelsContext(store=adapter))
        models = provider.get_models()
        assert len(models) == 1
        assert models[0].id == "d2"

    @pytest.mark.asyncio
    async def test_refresh_models_concurrent_calls(self):
        call_count = 0

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return [_make_model("d1")]

        provider = create_provider(
            id="test",
            name="Test",
            fetch_models=fetch_models,
        )

        # 并发调用应合并为一个任务
        await asyncio.gather(
            provider.refresh_models(RefreshModelsContext()),
            provider.refresh_models(RefreshModelsContext()),
            provider.refresh_models(RefreshModelsContext()),
        )

        assert call_count == 1
        assert len(provider.get_models()) == 1


class TestProviderFilterModels:
    def test_filter_models_applied(self):
        m1 = _make_model("m1")
        m2 = _make_model("m2")

        def filter_models(models, credential):
            return [m for m in models if m.id == "m1"]

        provider = create_provider(
            id="test",
            name="Test",
            models=[m1, m2],
            filter_models=filter_models,
        )

        assert provider.filter_models is not None
        filtered = provider.filter_models(provider.get_models(), None)
        assert filtered == [m1]

    def test_filter_models_none_by_default(self):
        provider = create_provider(id="test", name="Test")
        assert provider.filter_models is None
