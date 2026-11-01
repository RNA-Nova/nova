"""catalog_refresh_policy 六件套治理皮测试（对齐 pi remote-catalog-provider 语义）。

policy 住 harness 模型域；内容源经 ``context.catalog_fetch`` 注入
（manager 路由发放的公开站拉取函数，测试以假拉取器顶替）。
"""

import asyncio
import time

import pytest
from nova_harness.model.catalog import catalog_refresh_policy, remote_models
from nova_protocol import AbortController, KnownApi, Model, ModelCost, ModelsStoreEntry

from nova_ai.gateway.catalog import CatalogOutcome
from nova_ai.gateway.provider import ModelsPublication, Provider, merge_models

GENERATED_AT = 1000


def _fields(model_id: str, **overrides):
    """完整 ModelFields 假数据（Model(**fields) 的必填全集）。"""
    fields = {
        "id": model_id,
        "name": model_id,
        "api": "openai-completions",
        "base_url": "https://api.example.com/v1",
        "reasoning": False,
        "input_types": ["text"],
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 262144,
        "max_tokens": 8192,
    }
    fields.update(overrides)
    return fields


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
        base_url="https://api.example.com/v1",
        catalog_source="pi.dev",
        _baseline_models=[_model("kimi-k2.5")],
    )


class _Context:
    """最小 RefreshModelsContext 替身（publish 收集产物供断言）。"""

    def __init__(
        self,
        stored=None,
        allow_network=True,
        credential=None,
        force=False,
        catalog_fetch=None,
    ):
        self.stored = stored
        self.allow_network = allow_network
        self.signal = AbortController().signal
        self.credential = credential
        self.force = force
        self.catalog_fetch = catalog_fetch
        self.publications: list = []

    async def publish(self, publication: ModelsPublication) -> bool:
        self.publications.append(publication)
        if publication.update is not None:
            publication.update()
        return True


def _policy(provider: Provider, local_generated_at=GENERATED_AT):
    return catalog_refresh_policy(provider, local_generated_at)


def _make_fetcher(status, models=None, etag=None, last_modified=None, calls=None):
    async def _fetch(signal, validator):
        if calls is not None:
            calls.append({"validator": validator})
        return CatalogOutcome(
            status=status,
            models_fields=(
                [{**m, "id": m.get("id", mid)} for mid, m in (models or {}).items()]
                if status == 200
                else []
            ),
            etag=etag,
            last_modified=last_modified,
        )

    return _fetch


class TestMergeModels:
    def test_same_id_override_and_new_add(self):
        merged = merge_models(
            [_model("a"), _model("b")],
            [_model("b", provider="x"), _model("c")],
        )
        assert [m.id for m in merged] == ["a", "b", "c"]
        assert merged[1].provider == "x"


class TestRemoteModels:
    def test_none_entry_returns_empty(self):
        assert remote_models(None, GENERATED_AT) == []

    def test_missing_last_modified_ignored(self):
        entry = ModelsStoreEntry(models=[_model("x")], checked_at=1)
        assert remote_models(entry, GENERATED_AT) == []

    def test_older_than_baseline_ignored(self):
        entry = ModelsStoreEntry(models=[_model("x")], checked_at=1, last_modified=999)
        assert remote_models(entry, GENERATED_AT) == []

    def test_newer_than_baseline_applied(self):
        entry = ModelsStoreEntry(models=[_model("x")], checked_at=1, last_modified=1001)
        assert [m.id for m in remote_models(entry, GENERATED_AT)] == ["x"]


class TestCatalogRefreshPolicy:
    def test_baseline_visible_before_refresh(self):
        provider = _provider()
        assert [m.id for m in provider.get_models()] == ["kimi-k2.5"]

    def test_restore_phase_applies_fresh_overlay(self):
        async def main():
            cached = ModelsStoreEntry(
                models=[_model("kimi-k3")],
                checked_at=1,
                last_modified=GENERATED_AT + 1,
            )
            provider = _provider()
            policy = _policy(provider)
            await policy(_Context(stored=cached, catalog_fetch=_make_fetcher(404)))
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5", "kimi-k3"]

        asyncio.run(main())

    def test_no_catalog_fetch_skips_network(self):
        """ctx.catalog_fetch 缺席（未选配站）→ 恢复照常，网络阶段静默跳过。"""

        async def main():
            cached = ModelsStoreEntry(
                models=[_model("kimi-k3")],
                checked_at=1,
                last_modified=GENERATED_AT + 1,
            )
            provider = _provider()
            policy = _policy(provider)
            await policy(_Context(stored=cached, catalog_fetch=None))
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5", "kimi-k3"]

        asyncio.run(main())

    def test_ttl_skip_within_window(self):
        """checked_at 与 last_modified 同有且新鲜 → 短路不发拉取。"""

        async def main():
            cached = ModelsStoreEntry(
                models=[_model("kimi-k3")],
                checked_at=int(time.time() * 1000),
                last_modified=GENERATED_AT + 1,
            )
            calls = []
            provider = _provider()
            policy = _policy(provider)
            await policy(
                _Context(stored=cached, catalog_fetch=_make_fetcher(200, calls=calls))
            )
            assert calls == []

        asyncio.run(main())

    def test_ttl_requires_last_modified(self):
        """仅 checked_at 新鲜但无 last_modified（失败残留）→ 不短路，真实重试。"""

        async def main():
            cached = ModelsStoreEntry(
                models=[],
                checked_at=int(time.time() * 1000),
                last_modified=None,
            )
            calls = []
            provider = _provider()
            policy = _policy(provider)
            await policy(
                _Context(stored=cached, catalog_fetch=_make_fetcher(404, calls=calls))
            )
            assert len(calls) == 1

        asyncio.run(main())

    def test_success_merges_and_persists(self):
        async def main():
            calls = []
            provider = _provider()
            fetch = _make_fetcher(
                200,
                models={
                    "kimi-k2.5": _fields("kimi-k2.5", name="new"),
                    "brand-new": _fields("brand-new"),
                },
                etag='"v2"',
                last_modified=GENERATED_AT + 1,
                calls=calls,
            )
            context = _Context(catalog_fetch=fetch)
            policy = _policy(provider)
            await policy(context)
            ids = [m.id for m in provider.get_models()]
            assert "kimi-k2.5" in ids and "brand-new" in ids
            assert context.publications[-1].persist.etag == '"v2"'
            assert calls[0]["validator"] is None  # 首次无缓存不发 validator

        asyncio.run(main())

    def test_404_opts_out(self):
        async def main():
            provider = _provider()
            policy = _policy(provider)
            context = _Context(catalog_fetch=_make_fetcher(404))
            await policy(context)
            persist = context.publications[-1].persist
            assert persist.last_modified == 0  # 主动退出，不再硬重试
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5"]

        asyncio.run(main())

    def test_304_advances_checked_at_only(self):
        """304 未变：overlay 不动，仅推进新鲜度窗口；stored 有缓存体时
        validator 如实发出。"""
        cached = ModelsStoreEntry(
            models=[_model("cached")],
            checked_at=1,
            last_modified=GENERATED_AT + 1,
            etag='"v1"',
        )

        async def main():
            calls = []
            provider = _provider()
            policy = _policy(provider)
            context = _Context(
                stored=cached, catalog_fetch=_make_fetcher(304, calls=calls)
            )
            await policy(context)
            assert calls == [{"validator": '"v1"'}]
            persist = context.publications[-1].persist
            assert persist.checked_at != 1  # checked_at 推进
            assert [m.id for m in persist.models] == ["cached"]  # 缓存体不动
            assert persist.etag == '"v1"'
            assert persist.last_modified == GENERATED_AT + 1
            # overlay 已在恢复阶段进内存，304 不掏空
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5", "cached"]

        asyncio.run(main())

    def test_force_bypasses_ttl(self):
        """force=True：新鲜快照也强制重新拉取。"""
        cached = ModelsStoreEntry(
            models=[_model("cached")],
            checked_at=int(time.time() * 1000),
            last_modified=GENERATED_AT + 1,
        )

        async def main():
            calls = []
            provider = _provider()
            policy = _policy(provider)
            await policy(
                _Context(
                    stored=cached,
                    force=True,
                    catalog_fetch=_make_fetcher(304, calls=calls),
                )
            )
            assert len(calls) == 1

        asyncio.run(main())

    def test_transient_failure_keeps_cache_and_raises(self):
        """瞬时失败：保缓存 + 推进 checked_at（有缓存体的失败退避 4h）+ 抛错。"""

        async def main():
            cached = ModelsStoreEntry(
                models=[_model("cached")],
                checked_at=1,
                last_modified=GENERATED_AT + 1,
                etag='"v1"',
            )
            provider = _provider()
            policy = _policy(provider)
            context = _Context(stored=cached, catalog_fetch=_make_fetcher(500))
            with pytest.raises(RuntimeError, match="500"):
                await policy(context)
            # 保缓存：overlay 已从存量恢复到内存
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5", "cached"]
            # 推进 checked_at（pi 语义：有缓存体的失败退避）
            persist = context.publications[-1].persist
            assert persist.etag == '"v1"'
            assert persist.models[0].id == "cached"
            assert persist.checked_at != 1

        asyncio.run(main())

    def test_transient_failure_with_cache_suppresses_next_refresh(self):
        """有缓存体的失败：checked_at 被推进 + last_modified 保留 → 下次被 4h 闸短路。"""

        async def main():
            cached = ModelsStoreEntry(
                models=[_model("cached")],
                checked_at=1,
                last_modified=GENERATED_AT + 1,
                etag='"v1"',
            )
            calls = []
            provider = _provider()
            policy = _policy(provider)
            context = _Context(
                stored=cached, catalog_fetch=_make_fetcher(500, calls=calls)
            )
            with pytest.raises(RuntimeError):
                await policy(context)
            # 真实流程里 ModelsManager 下一轮会重读落盘快照——
            # 用刚持久化的条目（checked_at 已推进）作为第二轮的 stored
            persisted = context.publications[-1].persist
            await policy(
                _Context(
                    stored=persisted, catalog_fetch=_make_fetcher(500, calls=calls)
                )
            )
            assert len(calls) == 1

        asyncio.run(main())

    def test_transient_failure_without_cache_retries_next_refresh(self):
        """无缓存体的失败：last_modified 缺失 → 下次立即真实重试。"""

        async def main():
            calls = []
            provider = _provider()
            policy = _policy(provider)
            context = _Context(
                catalog_fetch=_make_fetcher(500, calls=calls)
            )  # 无 stored
            for _ in range(2):
                with pytest.raises(RuntimeError, match="500"):
                    await policy(context)
            assert len(calls) == 2

        asyncio.run(main())

    def test_refresh_signature_contract(self):
        """refresh_models 能力字段契约（context 单参）。"""
        import inspect

        provider = _provider()
        policy = _policy(provider)
        assert list(inspect.signature(policy).parameters) == ["context"]
