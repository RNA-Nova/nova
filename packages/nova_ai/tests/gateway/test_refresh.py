"""动态刷新机器测试（三件套策略/两阶段/发布与持久化/supersede）。

存储契约本身（read/write/delete/拷贝隔离）归 tests/contracts/ 契约套件。
"""

import asyncio
from typing import List

import pytest
from nova_ai.gateway import InMemoryModelsStore, ModelsManager, RefreshModelsContext
from nova_ai.providers import create_provider
from nova_protocol import (
    AnyModel,
    Context,
    KnownApi,
    Model,
    ModelCost,
    ModelsStoreEntry,
    NovaDiagnostic,
    UserMessage,
    WarningKind,
)
from tests._helpers.callback_refresh import callback_refresh


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


def _resolving_auth(key: str = "sk-test"):
    """始终可解析的 apiKey auth 桩（网络阶段需要有效凭据）。"""
    from nova_ai.auth import ApiKeyAuth, ProviderAuth
    from nova_protocol import AuthResult

    async def _resolve(_ctx):
        return AuthResult(auth={"api_key": key}, env=None, source="test")

    return ProviderAuth(api_key=ApiKeyAuth(name="test", resolve=_resolve))


@pytest.mark.asyncio
class TestDynamicProvider:
    async def test_refresh_models_merges_dynamic_models(self):
        baseline = [_make_model("b1")]
        dynamic = [_make_model("d1"), _make_model("d2")]

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            return dynamic

        provider = create_provider(
            id="test",
            name="Test",
            models=baseline,
        )
        provider.refresh_models = callback_refresh(provider, fetch_models)

        # 初始只有 baseline
        assert len(provider.get_models()) == 1

        # refresh 后合并（直调需自带发布桩——等价 ModelsManager.publish 的内存更新）
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

    async def test_refresh_models_overwrites_baseline(self):
        baseline = [_make_model("m1")]
        dynamic = [_make_model("m1")]  # 同 id，覆盖 baseline

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            return dynamic

        provider = create_provider(
            id="test",
            name="Test",
            models=baseline,
        )
        provider.refresh_models = callback_refresh(provider, fetch_models)
        await provider.refresh_models(RefreshModelsContext())

        models = provider.get_models()
        assert len(models) == 1
        assert models[0].id == "m1"

    async def test_refresh_models_persists_to_store(self):
        store = InMemoryModelsStore()
        dynamic = [_make_model("d1")]

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            return dynamic

        provider = create_provider(
            id="test",
            name="Test",
            auth=_resolving_auth(),
        )
        provider.refresh_models = callback_refresh(provider, fetch_models)

        # 经 ModelsManager.refresh 走完整发布链（世代校验 + 持久化）
        models = ModelsManager(models_store=store)
        models.set_provider(provider)
        result = await models.refresh()
        assert result["errors"] == {}

        stored = await store.read("test")
        assert stored is not None
        assert len(stored.models) == 1
        assert stored.models[0].id == "d1"
        assert stored.checked_at is not None

    async def test_refresh_models_restores_from_store(self):
        store = InMemoryModelsStore()
        dynamic: List[AnyModel] = [_make_model("d1")]
        await store.write("test", ModelsStoreEntry(models=dynamic, checked_at=123))

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            return [_make_model("d2")]

        provider = create_provider(
            id="test",
            name="Test",
            auth=_resolving_auth(),
        )
        provider.refresh_models = callback_refresh(provider, fetch_models)

        # 两阶段刷新（对齐 TS）：先离线恢复缓存，再在线拉新
        models = ModelsManager(models_store=store)
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

    async def test_refresh_models_none_skips_publish_preserving_cache(self):
        """fetch_models 返回 None（无站降级）→ 本轮不发布：内存缓存与落盘
        都保持现状（"无站"≠"拉到空列表"——后者照常发布清空 overlay）。"""
        store = InMemoryModelsStore()
        await store.write(
            "test", ModelsStoreEntry(models=[_make_model("cached1")], checked_at=123)
        )

        async def fetch_models(context: RefreshModelsContext):
            return None

        provider = create_provider(
            id="test",
            name="Test",
            auth=_resolving_auth(),
        )
        provider.refresh_models = callback_refresh(provider, fetch_models)

        models = ModelsManager(models_store=store)
        models.set_provider(provider)
        result = await models.refresh()
        assert result["errors"] == {}
        # 内存：离线恢复读出的缓存继续服役（未被空 overlay 覆盖）
        assert [m.id for m in provider.get_models()] == ["cached1"]
        # 落盘：缓存条目原样保留（checked_at 不被推进——本轮压根无发布）
        stored = await store.read("test")
        assert stored is not None
        assert [m.id for m in stored.models] == ["cached1"]
        assert stored.checked_at == 123

    async def test_refresh_collects_diagnostics(self):
        """fetch_models 的诊断（warnings as data）经汇集进结果的 diagnostics 键。"""

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            context.diagnostics.append(
                NovaDiagnostic(
                    severity="warning",
                    kind=WarningKind.CAPABILITY,
                    message="测试降级",
                )
            )
            return [_make_model("d1")]

        provider = create_provider(
            id="test",
            name="Test",
            auth=_resolving_auth(),
        )
        provider.refresh_models = callback_refresh(provider, fetch_models)

        models = ModelsManager(models_store=InMemoryModelsStore())
        models.set_provider(provider)
        result = await models.refresh()
        assert result["errors"] == {}
        entries = result["diagnostics"]["test"]
        assert len(entries) == 1
        assert entries[0].kind is WarningKind.CAPABILITY
        assert entries[0].message == "测试降级"

    async def test_refresh_diagnostic_live_slot(self):
        """注入 on_diagnostic 的诊断实时缝：append 即外发，且结果仍携带。"""

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            context.diagnostics.append(
                NovaDiagnostic(
                    severity="warning",
                    kind=WarningKind.CAPABILITY,
                    message="降级警告",
                )
            )
            return [_make_model("d1")]

        provider = create_provider(
            id="test",
            name="Test",
            auth=_resolving_auth(),
        )
        provider.refresh_models = callback_refresh(provider, fetch_models)

        fired: List[NovaDiagnostic] = []
        models = ModelsManager(
            models_store=InMemoryModelsStore(), on_diagnostic=fired.append
        )
        models.set_provider(provider)
        result = await models.refresh()
        # 实时缝：刷新返回前已外发（同步触发，非批量延迟）
        assert [d.message for d in fired] == ["降级警告"]
        # 批通道不变：结果照常携带
        assert result["diagnostics"]["test"][0].message == "降级警告"

    async def test_refresh_models_concurrent_calls(self):
        call_count = 0
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def fetch_models(context: RefreshModelsContext) -> List[Model]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                first_started.set()
                await release_first.wait()
                return [_make_model("old")]
            return [_make_model("new")]

        provider = create_provider(
            id="test",
            name="Test",
            auth=_resolving_auth(),
        )
        provider.refresh_models = callback_refresh(provider, fetch_models)

        # 两轮返回不同目录：旧轮次即使最后才结束，也不能覆盖新轮次。
        from nova_ai.gateway import ModelsManager

        store = InMemoryModelsStore()
        models = ModelsManager(models_store=store)
        models.set_provider(provider)
        first = asyncio.create_task(models.refresh())
        await first_started.wait()
        await models.refresh()
        release_first.set()
        await first

        assert call_count == 2
        assert [model.id for model in provider.get_models()] == ["new"]
        persisted = await store.read("test")
        assert persisted is not None
        assert [model.id for model in persisted.models] == ["new"]


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


@pytest.mark.asyncio
async def test_offline_refresh_never_swallows_online_follower():
    """offline 在途调用不与 online 共享；follower 必须真实进入网络阶段。"""
    started = asyncio.Event()
    release = asyncio.Event()
    online_calls = 0
    provider = create_provider(id="test", name="Test", auth=_resolving_auth())

    async def refresh(context: RefreshModelsContext) -> None:
        nonlocal online_calls
        if not context.allow_network:
            # 每次 refresh 都先做离线恢复；只让首个 leader 卡在离线段。
            if not started.is_set():
                started.set()
                await release.wait()
        else:
            online_calls += 1

    provider.refresh_models = refresh
    models = ModelsManager(models_store=InMemoryModelsStore())
    models.set_provider(provider)

    leader = asyncio.ensure_future(models.refresh(allow_network=False))
    await started.wait()
    follower = models.refresh(allow_network=True)
    await follower
    assert online_calls == 1
    release.set()
    await leader


@pytest.mark.asyncio
async def test_refresh_deadline_covers_credential_resolution():
    """deadline 预算覆盖凭据解析段：解析挂起也在预算内处死
    （RequestTimeoutError——不悬挂、不冒名取消）。

    此前预算只包网络阶段，解析在预算外裸奔（内置 resolve 全读 env 才
    没炸过）——终态：整条链共享一个预算。
    """
    from nova_ai.auth import ApiKeyAuth, ProviderAuth
    from nova_ai.utils.errors import RequestTimeoutError

    async def _hanging_resolve(_ctx):
        await asyncio.Event().wait()  # 永不就绪

    async def fetch_models(context: RefreshModelsContext) -> List[Model]:
        return [_make_model("d1")]

    provider = create_provider(
        id="test",
        name="Test",
        auth=ProviderAuth(api_key=ApiKeyAuth(name="test", resolve=_hanging_resolve)),
    )
    provider.refresh_models = callback_refresh(provider, fetch_models)
    models = ModelsManager(models_store=InMemoryModelsStore())
    models.set_provider(provider)

    result = await models.refresh(deadline_ms=50)
    error = result["errors"]["test"]
    assert type(error) is RequestTimeoutError


@pytest.mark.asyncio
async def test_refresh_deadline_also_covers_offline_store_read():
    from nova_ai.utils.errors import RequestTimeoutError

    class _HangingReadStore(InMemoryModelsStore):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def read(self, provider_id):
            self.started.set()
            await asyncio.Event().wait()

    store = _HangingReadStore()
    provider = create_provider(id="test", name="Test")

    async def refresh(_context: RefreshModelsContext) -> None:
        return None

    provider.refresh_models = refresh
    models = ModelsManager(models_store=store)
    models.set_provider(provider)
    result = await models.refresh(allow_network=False, deadline_ms=20)
    assert store.started.is_set()
    assert type(result["errors"]["test"]) is RequestTimeoutError


@pytest.mark.asyncio
async def test_refresh_preserves_diagnostics_when_network_phase_raises():
    provider = create_provider(id="test", name="Test", auth=_resolving_auth())

    async def refresh(context: RefreshModelsContext) -> None:
        if not context.allow_network:
            return
        context.diagnostics.append(
            NovaDiagnostic(
                severity="warning", kind=WarningKind.CAPABILITY, message="degraded"
            )
        )
        raise RuntimeError("station failed")

    provider.refresh_models = refresh
    models = ModelsManager(models_store=InMemoryModelsStore())
    models.set_provider(provider)
    result = await models.refresh()
    assert isinstance(result["errors"]["test"], RuntimeError)
    assert [item.message for item in result["diagnostics"]["test"]] == ["degraded"]


@pytest.mark.asyncio
async def test_cancelled_previous_publication_does_not_poison_next_publish():
    """公开 refresh 链：取消一次发布后，下一代仍可正常发布。"""
    from nova_ai.gateway import ModelsPublication

    provider = create_provider(id="test", name="Test", auth=_resolving_auth())
    updated = []
    calls = 0

    async def refresh(context: RefreshModelsContext) -> None:
        nonlocal calls
        calls += 1
        publish = context.publish
        assert publish is not None
        if calls == 1:
            cancelled = asyncio.ensure_future(
                publish(ModelsPublication(update=lambda: updated.append("old")))
            )
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)
            return
        await publish(ModelsPublication(update=lambda: updated.append("current")))

    provider.refresh_models = refresh
    models = ModelsManager(models_store=InMemoryModelsStore())
    models.set_provider(provider)
    await models.refresh()
    result = await models.refresh()

    assert result["errors"] == {}
    assert updated and set(updated) == {"current"}
