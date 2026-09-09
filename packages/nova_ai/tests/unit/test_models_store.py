"""ModelsStore / _DynamicProvider / filter_models 测试。"""

import asyncio
from typing import List

import pytest

from nova_ai.gateway import (
    InMemoryModelsStore,
    Models,
    ModelsStoreEntry,
    RefreshModelsContext,
)
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


def _resolving_auth(key: str = "sk-test"):
    """始终可解析的 apiKey auth 桩（网络阶段需要有效凭据）。"""
    from nova_ai.types import ApiKeyAuth, AuthResult, ProviderAuth

    async def _resolve(_ctx):
        return AuthResult(auth={"apiKey": key}, env=None, source="test")

    return ProviderAuth(api_key=ApiKeyAuth(name="test", resolve=_resolve))


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

        # refresh 后合并（直调需自带发布桩——等价 Models.publish 的内存更新）
        from nova_ai.gateway import ModelsPublication

        async def _publish(publication: ModelsPublication) -> bool:
            if publication.update is not None:
                publication.update()
            return True

        await provider.refresh_models(RefreshModelsContext(publish=_publish))
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
            auth=_resolving_auth(),
        )

        # 经 Models.refresh 走完整发布链（世代校验 + 持久化）
        models = Models(models_store=store)
        models.set_provider(provider)
        result = await models.refresh()
        assert result["errors"] == {}

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
            auth=_resolving_auth(),
        )

        # 两阶段刷新（对齐 TS）：先离线恢复缓存，再在线拉新
        models = Models(models_store=store)
        models.set_provider(provider)
        result = await models.refresh(allow_network=False)
        assert result["errors"] == {}
        restored = provider.get_models()
        assert len(restored) == 1
        assert restored[0].id == "d1"

        result = await models.refresh()
        assert result["errors"] == {}
        refreshed = provider.get_models()
        assert len(refreshed) == 1
        assert refreshed[0].id == "d2"

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
            auth=_resolving_auth(),
        )

        # 并发经 Models 刷新：supersede 语义（对齐 pi）——各轮独立运行，
        # 世代校验保证终态一致（后发布者胜出，不会出现目录混合）
        from nova_ai.gateway import Models

        store = InMemoryModelsStore()
        models = Models(models_store=store)
        models.set_provider(provider)
        await asyncio.gather(models.refresh(), models.refresh())

        # supersede 生效：后到的刷新废弃先到的（先者在网络阶段前即收场，
        # 不再 fetch），终态由后到者发布——不会出现目录混合
        assert call_count == 1
        final = provider.get_models()
        assert len(final) == 1
        assert final[0].id == "d1"


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
