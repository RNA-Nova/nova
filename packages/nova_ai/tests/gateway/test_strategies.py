"""刷新策略测试（gateway/strategies.py——两形态；对齐 pi remote-catalog-provider 语义）。

治理只有这一份（六件套上移后的唯一家）：overlay（纯站六件套）与
personalized（站+账户钩子）。内容源经 ``context.catalog_fetch`` 注入
（manager 路由发放的公开站拉取函数，测试以假拉取器顶替）。
"""

import time

import pytest
from nova_protocol import (
    AbortController,
    KnownApi,
    MaterializedCatalog,
    Model,
    ModelCost,
    ModelFields,
    ModelsStoreEntry,
)

from nova_ai.gateway.catalog import CatalogError, CatalogOutcome
from nova_ai.gateway.provider import (
    ModelsPublication,
    Provider,
    RefreshModelsContext,
    merge_models,
)
from nova_ai.gateway.strategies import (
    overlay_refresh_policy,
    personalized_refresh_policy,
    remote_models,
)

pytestmark = pytest.mark.asyncio

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
    return ModelFields(**fields)


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


def _personal_provider() -> Provider:
    """personalized 形态的 provider（replace 读取位装配时置）。"""
    return Provider(
        id="kimi-coding",
        name="Kimi Coding",
        base_url="https://api.example.com/v1",
        catalog_source="pi.dev",
        _baseline_models=[_model("kimi-k2.5", provider="kimi-coding")],
        _dynamic_replaces_baseline=True,
    )


class _Context(RefreshModelsContext):
    """真实刷新上下文；publish 回调同时记录并执行发布动作。"""

    def __init__(
        self,
        stored=None,
        allow_network=True,
        credential=None,
        force=False,
        catalog_fetch=None,
    ):
        self.publications: list[ModelsPublication] = []
        super().__init__(
            stored=stored,
            allow_network=allow_network,
            credential=credential,
            force=force,
            catalog_fetch=catalog_fetch,
            signal=AbortController().signal,
            publish=self._record,
        )

    async def _record(self, publication: ModelsPublication) -> bool:
        self.publications.append(publication)
        if publication.update is not None:
            publication.update()
        return True


def _persisted(context: _Context) -> ModelsStoreEntry:
    """取最近一次实际持久化条目；UNSET/None 都是测试失败。"""
    publication = context.publications[-1]
    persist = publication.persist
    assert isinstance(persist, ModelsStoreEntry)
    return persist


def _policy(provider: Provider, local_generated_at=GENERATED_AT):
    return overlay_refresh_policy(provider, local_generated_at=local_generated_at)


def _make_fetcher(status, models=None, etag=None, last_modified=None, calls=None):
    async def _fetch(signal, validator):
        if calls is not None:
            calls.append({"validator": validator})
        return CatalogOutcome(
            status=status,
            models_fields=(
                [
                    ModelFields(**{**m, "id": m.get("id", mid)})
                    for mid, m in (models or {}).items()
                ]
                if status == 200
                else []
            ),
            etag=etag,
            last_modified=last_modified,
        )

    return _fetch


async def test_overlay_refresh_publishes_dynamic_image_models():
    image = _fields(
        "image-model",
        type="image",
        api="openrouter-images",
        output_types=["image"],
    )
    image.pop("reasoning")
    image.pop("context_window")
    image.pop("max_tokens")
    provider = _provider()
    context = _Context(
        catalog_fetch=_make_fetcher(
            200, models={"image-model": image}, last_modified=GENERATED_AT + 1
        )
    )
    await _policy(provider)(context)

    model = provider.get_model_of_type("image", "image-model")
    assert model is not None
    assert model.type == "image"
    persisted = _persisted(context)
    assert isinstance(persisted, ModelsStoreEntry)
    assert [(entry.type, entry.id) for entry in persisted.models] == [
        ("image", "image-model")
    ]


async def test_overlay_refresh_publishes_and_restores_dynamic_classifier_models():
    classifier = _fields(
        "decision-model",
        type="classifier",
        api="typesafe-system-one",
    )
    classifier.pop("reasoning")
    classifier.pop("max_tokens")
    provider = _provider()
    context = _Context(
        catalog_fetch=_make_fetcher(
            200,
            models={"decision-model": classifier},
            last_modified=GENERATED_AT + 1,
        )
    )
    await _policy(provider)(context)

    model = provider.get_model_of_type("classifier", "decision-model")
    assert model is not None and model.type == "classifier"
    persisted = _persisted(context)
    assert [(entry.type, entry.id) for entry in persisted.models] == [
        ("classifier", "decision-model")
    ]

    restored = _provider()
    await _policy(restored)(_Context(stored=persisted, allow_network=False))
    restored_model = restored.get_model_of_type("classifier", "decision-model")
    assert restored_model is not None
    assert restored_model.type == "classifier"


async def test_overlay_materialize_callback_is_captured_by_refresh_closure():
    seen = []

    def materialize(fields, source_status):
        assert source_status == {}
        seen.append([field.get("id") for field in fields])
        return MaterializedCatalog(
            models_fields=[
                ModelFields(**{**field, "name": "materialized"}) for field in fields
            ]
        )

    provider = _provider()
    context = _Context(
        catalog_fetch=_make_fetcher(
            200, models={"fresh": _fields("fresh")}, last_modified=GENERATED_AT + 1
        )
    )
    policy = overlay_refresh_policy(
        provider, local_generated_at=GENERATED_AT, materialize=materialize
    )
    await policy(context)
    assert seen == [["fresh"]]
    model = provider.get_model("fresh")
    assert model is not None
    assert model.name == "materialized"


class TestMergeModels:
    async def test_same_id_override_and_new_add(self):
        merged = merge_models(
            [_model("a"), _model("b")],
            [_model("b", provider="x"), _model("c")],
        )
        assert [m.id for m in merged] == ["a", "b", "c"]
        assert merged[1].provider == "x"


class TestRemoteModels:
    async def test_none_entry_returns_empty(self):
        assert remote_models(None, GENERATED_AT) == []

    async def test_missing_last_modified_ignored(self):
        entry = ModelsStoreEntry(models=[_model("x")], checked_at=1)
        assert remote_models(entry, GENERATED_AT) == []

    async def test_older_than_baseline_ignored(self):
        entry = ModelsStoreEntry(models=[_model("x")], checked_at=1, last_modified=999)
        assert remote_models(entry, GENERATED_AT) == []

    async def test_newer_than_baseline_applied(self):
        entry = ModelsStoreEntry(models=[_model("x")], checked_at=1, last_modified=1001)
        assert [m.id for m in remote_models(entry, GENERATED_AT)] == ["x"]


class TestOverlayRefreshPolicy:
    async def test_baseline_visible_before_refresh(self):
        provider = _provider()
        assert [m.id for m in provider.get_models()] == ["kimi-k2.5"]

    async def test_restore_phase_applies_fresh_overlay(self):
        async def main():
            cached = ModelsStoreEntry(
                models=[_model("kimi-k3")],
                checked_at=1,
                last_modified=GENERATED_AT + 1,
            )
            provider = _provider()
            policy = _policy(provider)
            await policy(_Context(stored=cached, allow_network=False))
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5", "kimi-k3"]

        await main()

    async def test_404_clears_restored_overlay_in_memory(self):
        cached = ModelsStoreEntry(
            models=[_model("kimi-k3")],
            checked_at=1,
            last_modified=GENERATED_AT + 1,
        )
        provider = _provider()
        await _policy(provider)(
            _Context(stored=cached, catalog_fetch=_make_fetcher(404))
        )
        assert [m.id for m in provider.get_models()] == ["kimi-k2.5"]

    async def test_no_catalog_fetch_skips_network_with_diagnostic(self):
        """ctx.catalog_fetch 缺席（未选配站）→ 恢复照常，网络阶段跳过 + 降级诊断。"""

        async def main():
            cached = ModelsStoreEntry(
                models=[_model("kimi-k3")],
                checked_at=1,
                last_modified=GENERATED_AT + 1,
            )
            provider = _provider()
            policy = _policy(provider)
            context = _Context(stored=cached, catalog_fetch=None)
            await policy(context)
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5", "kimi-k3"]
            assert len(context.diagnostics) == 1

        await main()

    async def test_ttl_skip_within_window(self):
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

        await main()

    async def test_ttl_requires_last_modified(self):
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

        await main()

    async def test_success_merges_and_persists(self):
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
            assert _persisted(context).etag == '"v2"'
            assert calls[0]["validator"] is None  # 首次无缓存不发 validator

        await main()

    async def test_404_opts_out(self):
        async def main():
            provider = _provider()
            policy = _policy(provider)
            context = _Context(catalog_fetch=_make_fetcher(404))
            await policy(context)
            persist = _persisted(context)
            assert persist.last_modified == 0  # 主动退出，不再硬重试
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5"]

        await main()

    async def test_304_advances_checked_at_only(self):
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
            persist = _persisted(context)
            assert persist.checked_at != 1  # checked_at 推进
            assert [m.id for m in persist.models] == ["cached"]  # 缓存体不动
            assert persist.etag == '"v1"'
            assert persist.last_modified == GENERATED_AT + 1
            # overlay 已在恢复阶段进内存，304 不掏空
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5", "cached"]

        await main()

    async def test_force_bypasses_ttl(self):
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

        await main()

    async def test_transient_failure_keeps_cache_and_raises(self):
        """瞬时失败：保缓存 + 推进 checked_at（有缓存体的失败退避 4h）+ 抛错
        （CatalogError——归一按 status_code 分类）。"""

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
            with pytest.raises(CatalogError, match="500"):
                await policy(context)
            # 保缓存：overlay 已从存量恢复到内存
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5", "cached"]
            # 推进 checked_at（pi 语义：有缓存体的失败退避）
            persist = _persisted(context)
            assert persist.etag == '"v1"'
            assert persist.models[0].id == "cached"
            assert persist.checked_at != 1

        await main()

    async def test_transient_failure_with_cache_suppresses_next_refresh(self):
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
            with pytest.raises(CatalogError):
                await policy(context)
            # 真实流程里 ModelsManager 下一轮会重读落盘快照——
            # 用刚持久化的条目（checked_at 已推进）作为第二轮的 stored
            persisted = _persisted(context)
            await policy(
                _Context(
                    stored=persisted, catalog_fetch=_make_fetcher(500, calls=calls)
                )
            )
            assert len(calls) == 1

        await main()

    async def test_transient_failure_without_cache_retries_next_refresh(self):
        """无缓存体的失败：last_modified 缺失 → 下次立即真实重试。"""

        async def main():
            calls = []
            provider = _provider()
            policy = _policy(provider)
            context = _Context(
                catalog_fetch=_make_fetcher(500, calls=calls)
            )  # 无 stored
            for _ in range(2):
                with pytest.raises(CatalogError, match="500"):
                    await policy(context)
            assert len(calls) == 2

        await main()

    async def test_refresh_signature_contract(self):
        """refresh_models 能力字段契约（context 单参）。"""
        import inspect

        provider = _provider()
        policy = _policy(provider)
        assert list(inspect.signature(policy).parameters) == ["context"]


class TestPersonalizedRefreshPolicy:
    """personalized 形态（站+钩子；跳 TTL/ETag；replace 读取）。"""

    def _policy(self, provider, hook, local_generated_at=GENERATED_AT):
        return personalized_refresh_policy(
            provider, hook, local_generated_at=local_generated_at
        )

    async def test_restore_publishes_stored_without_is_newer_guard(self):
        """恢复段无 isNewer 守卫（pi personalized 同款）——旧缓存也恢复。
        （用 allow_network=False 隔离恢复段——网络阶段的 404 会按 pi 语义
        以 personalize(基线) 覆盖 persisted。）"""

        async def main():
            cached = ModelsStoreEntry(
                models=[_model("old", provider="kimi-coding")],
                checked_at=1,
                last_modified=1,  # 比基线还旧——overlay 会丢，personalized 照恢复
            )
            provider = _personal_provider()

            async def hook(base, context):
                return base  # allow_network=False——钩子在恢复段不被调用

            policy = self._policy(provider, hook)
            await policy(_Context(stored=cached, allow_network=False))
            assert [m.id for m in provider.get_models()] == ["old"]

        await main()

    async def test_hook_called_with_merged_base_and_result_persisted(self):
        """200：钩子收到 基线∪站 的合并基底；持久化的是钩子返回的成品。"""

        async def main():
            provider = _personal_provider()

            async def hook(base, context):
                # 收窄：只留 kimi-k2.5（账户筛选语义）
                return [m for m in base if m.id == "kimi-k2.5"]

            policy = self._policy(provider, hook)
            context = _Context(
                catalog_fetch=_make_fetcher(
                    200,
                    models={"station-only": _fields("station-only")},
                    last_modified=GENERATED_AT + 1,
                )
            )
            await policy(context)
            # replace 读取：dynamic（筛选后）整取代——baseline 不补回
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5"]
            persist = _persisted(context)
            assert [m.id for m in persist.models] == ["kimi-k2.5"]

        await main()

    async def test_personalized_refresh_preserves_classifier_models(self):
        classifier = _fields(
            "decision-model",
            type="classifier",
            api="typesafe-system-one",
        )
        classifier.pop("reasoning")
        classifier.pop("max_tokens")
        provider = _personal_provider()

        async def hook(base, _context):
            return base

        policy = self._policy(provider, hook)
        context = _Context(
            catalog_fetch=_make_fetcher(
                200,
                models={"decision-model": classifier},
                last_modified=GENERATED_AT + 1,
            )
        )
        await policy(context)
        model = provider.get_model_of_type("classifier", "decision-model")
        assert model is not None and model.type == "classifier"
        persisted = _persisted(context)
        assert any(
            entry.type == "classifier" and entry.id == "decision-model"
            for entry in persisted.models
        )

    async def test_transient_failure_raises_and_keeps_stored(self):
        """瞬时失败：抛 CatalogError，stored 成品继续服役。"""

        async def main():
            cached = ModelsStoreEntry(
                models=[_model("cached", provider="kimi-coding")],
                checked_at=1,
            )
            provider = _personal_provider()

            async def hook(base, context):
                raise AssertionError("不应到达——拉站失败不调钩子")

            policy = self._policy(provider, hook)
            with pytest.raises(CatalogError, match="500"):
                await policy(_Context(stored=cached, catalog_fetch=_make_fetcher(500)))
            assert [m.id for m in provider.get_models()] == ["cached"]

        await main()

    async def test_personalize_failure_preserves_stored_and_skips_publish(self):
        async def main():
            cached = ModelsStoreEntry(models=[_model("cached", provider="kimi-coding")])
            provider = _personal_provider()

            async def hook(base, context):
                raise CatalogError("private source unavailable")

            policy = self._policy(provider, hook)
            context = _Context(
                stored=cached, catalog_fetch=_make_fetcher(200, models={})
            )
            with pytest.raises(CatalogError, match="private source"):
                await policy(context)
            assert [m.id for m in provider.get_models()] == ["cached"]
            # 只有恢复动作，没有任何新成品写入。
            assert len(context.publications) == 1

        await main()

    async def test_personalized_empty_list_is_published_as_empty(self):
        async def main():
            provider = _personal_provider()

            async def hook(base, context):
                return []

            policy = self._policy(provider, hook)
            context = _Context(catalog_fetch=_make_fetcher(200, models={}))
            await policy(context)
            assert provider.get_models() == []
            assert _persisted(context).models == []

        await main()

    async def test_materialized_removals_apply_after_merge_before_hook(self):
        provider = _personal_provider()
        seen = []

        def materialize(fields, _source_status):
            return MaterializedCatalog(
                models_fields=fields, remove_ids=frozenset({"kimi-k2.5"})
            )

        async def hook(base, _context):
            seen.extend(model.id for model in base)
            return base

        policy = personalized_refresh_policy(
            provider,
            hook,
            local_generated_at=GENERATED_AT,
            materialize=materialize,
        )
        context = _Context(
            catalog_fetch=_make_fetcher(
                200,
                models={"fresh": _fields("fresh")},
                last_modified=GENERATED_AT + 1,
            )
        )
        await policy(context)
        assert seen == ["fresh"]
        assert [model.id for model in provider.get_models()] == ["fresh"]
        assert [model.id for model in _persisted(context).models] == ["fresh"]

    async def test_404_runs_hook_over_baseline(self):
        """404/501（站无此目录）：remote 为空——钩子作用于纯基线（pi 同款）。"""

        async def main():
            provider = _personal_provider()
            seen = []

            async def hook(base, context):
                seen.extend(m.id for m in base)
                return base

            policy = self._policy(provider, hook)
            await policy(_Context(catalog_fetch=_make_fetcher(404)))
            assert seen == ["kimi-k2.5"]  # 只见到基线
            assert [m.id for m in provider.get_models()] == ["kimi-k2.5"]

        await main()

    async def test_stale_remote_ignored_runs_hook_over_baseline(self):
        """远端 last_modified 不新于基线 → remote 为空（isNewer 竞速守卫）。"""

        async def main():
            provider = _personal_provider()
            seen = []

            async def hook(base, context):
                seen.extend(m.id for m in base)
                return base

            policy = self._policy(provider, hook)
            await policy(
                _Context(
                    catalog_fetch=_make_fetcher(
                        200,
                        models={"station-new": _fields("station-new")},
                        last_modified=GENERATED_AT - 1,  # 比基线旧
                    )
                )
            )
            assert seen == ["kimi-k2.5"]

        await main()

    async def test_no_ttl_every_refresh_fetches(self):
        """跳 TTL：上一轮刚成功，本轮仍真实拉取（凭据易变——pi personalized 同款）。"""

        async def main():
            calls = []
            cached = ModelsStoreEntry(
                models=[_model("cached", provider="kimi-coding")],
                checked_at=int(time.time() * 1000),
                last_modified=GENERATED_AT + 1,
            )
            provider = _personal_provider()

            async def hook(base, context):
                return base

            policy = self._policy(provider, hook)
            await policy(
                _Context(
                    stored=cached,
                    catalog_fetch=_make_fetcher(200, models={}, calls=calls),
                )
            )
            assert len(calls) == 1  # overlay 会被 4h 闸短路，personalized 不短

        await main()

    async def test_replace_read_mode_and_empty_dynamic_fallback(self):
        """replace 以发布状态而非列表长度裁决；合法空集同样整取代。"""
        provider = _personal_provider()
        assert [m.id for m in provider.get_models()] == ["kimi-k2.5"]

        async def hook(base, context):
            return base

        policy = self._policy(provider, hook)
        managed = _model("managed-only", provider="kimi-coding")
        await policy(
            _Context(
                stored=ModelsStoreEntry(models=[managed], checked_at=1),
                allow_network=False,
            )
        )
        assert [m.id for m in provider.get_models()] == ["managed-only"]

        await policy(
            _Context(
                stored=ModelsStoreEntry(models=[], checked_at=2),
                allow_network=False,
            )
        )
        assert provider.get_models() == []

        overlay = _provider()
        overlay_policy = _policy(overlay)
        await overlay_policy(
            _Context(
                stored=ModelsStoreEntry(
                    models=[_model("kimi-k3")],
                    checked_at=3,
                    last_modified=GENERATED_AT + 1,
                ),
                allow_network=False,
            )
        )
        assert [m.id for m in overlay.get_models()] == ["kimi-k2.5", "kimi-k3"]
