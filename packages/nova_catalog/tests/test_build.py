"""构建期目录流水线测试（nova_catalog/build.py：翻译/盖戳/筛选/钉住/别名源）。"""

import pytest
from nova_catalog.build import build_provider_models  # noqa: E402
from nova_catalog.define import ProviderDefinition  # noqa: E402
from nova_catalog.providers import PROVIDERS  # noqa: E402
from nova_catalog.providers.kimi_coding import DEF as KIMI_CODING_DEF  # noqa: E402
from nova_catalog.providers.moonshotai import DEF as MOONSHOTAI_DEF  # noqa: E402
from nova_catalog.sources.models_dev import ModelsDevSource  # noqa: E402
from nova_catalog.sources.models_dev import models_dev_to_nova  # noqa: E402
from nova_protocol import KnownApi  # noqa: E402


def _raw_entry(model_id="m1", **overrides):
    """v2 形状的原始条目（type/capabilities/cost.text/modalities）。"""
    entry = {
        "id": model_id,
        "name": model_id,
        "type": "chat",
        "capabilities": {
            "tools": {"supported": True},
            "reasoning": {"supported": True},
        },
        "limit": {"context": 128000, "output": 8192},
        "cost": {"text": {"input": {"price": 1}, "output": {"price": 2}}},
        "modalities": {"input": ["text", "image", "audio"]},
    }
    entry.update(overrides)
    return entry


def _full_fields(**overrides):
    """完整 Model 字段（静态/钉住夹具——构建期验收门要求全量必填）。"""
    fields = {
        "name": "M1",
        "reasoning": True,
        "input_types": ["text"],
        "cost": {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0},
        "context_window": 128000,
        "max_tokens": 8192,
    }
    fields.update(overrides)
    return fields


_DEF = ProviderDefinition(
    id="p1", name="P1", base_url="http://x/v1", api=KnownApi.OPENAI_COMPLETIONS
)


class TestBuildProviderModels:
    def test_raw_entries_translated_and_stamped(self):
        built = build_provider_models(_DEF, {"m1": _raw_entry()})
        fields = built["m1"]
        assert fields["id"] == "m1"
        assert fields["provider"] == "p1"
        assert fields["base_url"] == "http://x/v1"
        assert fields["api"] == "openai-completions"
        # models.dev 方言已翻译：limit/modalities → context_window/input_types
        assert fields["context_window"] == 128000
        assert fields["max_tokens"] == 8192
        assert fields["input_types"] == ["text", "image"]  # audio 被过滤

    def test_bad_entry_skipped_by_default(self):
        built = build_provider_models(
            _DEF, {"bad": _raw_entry("bad", name=None)}  # 缺 name
        )
        assert built == {}

    def test_strict_raises_on_bad_entry(self):
        with pytest.raises(ValueError, match="missing id/name"):
            build_provider_models(
                _DEF, {"bad": _raw_entry("bad", name=None)}, strict=True
            )

    def test_static_entries_passthrough_with_stamp(self):
        fields = _full_fields()
        built = build_provider_models(_DEF, {"m1": fields}, static=True)
        assert built["m1"]["context_window"] == 128000
        assert built["m1"]["provider"] == "p1"

    def test_ark_status_filters_bad_liveness_but_keeps_pinned(self):
        """ark 生命周期筛选经 volcengine 的 finalize_all 钩（ctx.ark_status
        驱动）；钉住模型豁免（数据源缺失但实测在售）。"""
        from nova_catalog.context import BuildContext
        from nova_catalog.providers.volcengine import DEF as VOLCENGINE_DEF
        from nova_catalog.sources.models_dev import ModelsDevSource

        ctx = BuildContext(
            {"volcengine": VOLCENGINE_DEF},
            ModelsDevSource(),
            ark_status={"deepseek-v4-flash-260425": "Shutdown", "m-x": "Retiring"},
        )
        ctx.set_raw(
            "volcengine",
            {
                "deepseek-v4-flash-260425": _raw_entry("deepseek-v4-flash-260425"),
                "m-x": _raw_entry("m-x"),
                "m-ok": _raw_entry("m-ok"),
            },
        )
        from nova_catalog.build import build_one

        models = build_one(VOLCENGINE_DEF, ctx)
        ids = {m["id"] for m in models}
        # 坏状态剔除（m-x）；钉住豁免（260425 虽在状态表也保留）；正常保留
        assert "m-x" not in ids
        assert "deepseek-v4-flash-260425" in ids
        assert "m-ok" in ids

    def test_pinned_models_merged_and_win_over_raw(self):
        definition = ProviderDefinition(
            id="p1",
            name="P1",
            base_url="http://x/v1",
            api=KnownApi.OPENAI_COMPLETIONS,
            pinned_models={"m1": _full_fields(name="Pinned")},
        )
        built = build_provider_models(
            definition, {"m1": _raw_entry("m1"), "m2": _raw_entry("m2")}
        )
        assert set(built) == {"m1", "m2"}
        # 构建期钉住优先（钉住条目是数据源缺失时的权威值）
        assert built["m1"]["name"] == "Pinned"
        assert built["m1"]["provider"] == "p1"

    def test_compat_defaults_sit_under_entry_compat(self):
        """compat 默认垫底：字段包自带 compat 赢（语义与运行期引擎一致）。"""
        definition = ProviderDefinition(
            id="p1",
            name="P1",
            base_url="http://x/v1",
            api=KnownApi.OPENAI_COMPLETIONS,
            compat_defaults={"a": 1, "b": 1},
        )
        # v2 源不携 compat——改走 static 自带 compat 路径验证"默认垫底"
        built = build_provider_models(
            definition,
            {"m1": _full_fields(compat={"b": 2})},
            static=True,
        )
        compat = built["m1"]["compat"]
        assert compat["a"] == 1 and compat["b"] == 2  # 默认垫底，字段包自带赢

    def test_model_corrections_glob_and_thinking_merge(self):
        """per-model 修正：compat 覆盖在上；TLM 按键合并（修正键逐键压，余留源）。"""
        definition = ProviderDefinition(
            id="p1",
            name="P1",
            base_url="http://x/v1",
            api=KnownApi.OPENAI_COMPLETIONS,
            model_corrections={
                "m*": {
                    "compat": {"flag": True},
                    "thinking_level_map": {"off": None},
                }
            },
        )
        built = build_provider_models(definition, {"m1": _raw_entry()})
        assert built["m1"]["compat"]["flag"] is True
        # 源无 effort → 无站推导 TLM，修正键 off:None 落位
        assert built["m1"]["thinking_level_map"] == {"off": None}
        # 按键合并：源自带级别表的键保留 + 修正键压入
        preset = _full_fields(thinking_level_map={"high": "high"})
        built2 = build_provider_models(definition, {"m1": preset}, static=True)
        assert built2["m1"]["thinking_level_map"] == {"off": None, "high": "high"}


@pytest.mark.asyncio
class TestSourceModelsFor:
    async def _ctx(self, monkeypatch, payload, providers):
        from nova_catalog.context import BuildContext

        async def fake_fetch_json(url, **kwargs):
            return payload

        monkeypatch.setattr(
            "nova_catalog.sources.models_dev.fetch_json", fake_fetch_json
        )
        ctx = BuildContext({p.id: p for p in providers}, ModelsDevSource())
        from nova_catalog.build import prepare_sources

        await prepare_sources(providers, ctx)
        return ctx

    async def test_provider_id_key_by_default(self, monkeypatch):
        ctx = await self._ctx(monkeypatch, {"p1": {"models": {"m1": {}}}}, [_DEF])
        assert ctx.raw_for(_DEF) == {"m1": {}}

    async def test_models_dev_key_alias(self, monkeypatch):
        """models_dev_key：provider id 与站键不同名时映射。"""
        ctx = await self._ctx(
            monkeypatch,
            {"kimi-code-plan-global": {"models": {"m1": {}}}},
            [KIMI_CODING_DEF],
        )
        assert ctx.raw_for(KIMI_CODING_DEF) == {"m1": {}}

    async def test_unknown_provider_returns_empty(self, monkeypatch):
        ctx = await self._ctx(monkeypatch, {}, [_DEF])
        assert ctx.raw_for(_DEF) == {}


def test_registry_sorted_ids_cover_all_data_shards():
    """注册表 id 集与数据分片一致（schema 对账由 mdata 校验覆盖）。"""
    ids = sorted(p.id for p in PROVIDERS)
    assert ids == [
        "anthropic",
        "kimi-coding",
        "moonshotai",
        "moonshotai-cn",
        "volcengine",
    ]


def test_k3_facts_come_from_family_layer():
    """K3 族事实（compat）经族层单点供给各家定义；级别表归站推导
    （models.dev v2 reasoning.effort——手写表已退役，定义里不再出现）。"""
    from nova_catalog.families.kimi import K3_OPENAI_COMPAT

    # 族 compat 仍归族层（deferred/mid-convo 旗标在各家 compat 引用同族）
    kimi_k3_compat = MOONSHOTAI_DEF.model_corrections["kimi-k3*"]["compat"]
    assert kimi_k3_compat["thinking_format"] == K3_OPENAI_COMPAT["thinking_format"]
    assert (
        kimi_k3_compat["supports_reasoning_effort"]
        == K3_OPENAI_COMPAT["supports_reasoning_effort"]
    )
    # 级别表已迁站推导：定义层不得再出现整表手写级别映射——
    # 唯一例外是宿主实证知识（ark v4 系，models.dev 列表不全、
    # 经活探验证为真并注明探测日期；详见 volcengine def 注释）
    for provider in PROVIDERS:
        for pattern, override in provider.model_corrections.items():
            if provider.id == "volcengine" and pattern == "deepseek-v4-*":
                continue  # 宿主实证 TLM（活探维护），合法住所
            tlm = override.get("thinking_level_map") or {}
            assert set(tlm.keys()) <= {
                "off"
            }, f"{provider.id}/{pattern} 手写级别表残留（应归站推导）"

    # moonshotai-cn 别名共享 moonshotai——族事实经别名同源
    from nova_catalog.providers.moonshotai_cn import DEF as MOONSHOTAI_CN_DEF

    assert MOONSHOTAI_CN_DEF.model_corrections is MOONSHOTAI_DEF.model_corrections


def test_kimi_coding_family_is_always_thinking():
    """kimi-coding 全家 always-thinking（pi 实测）——"*" 条目把 off 压 None。"""
    assert KIMI_CODING_DEF.model_corrections["*"]["thinking_level_map"] == {"off": None}


def test_anthropic_mid_convo_flag_rules():
    """anthropic mid-convo 旗标规则（对位 pi supportsClaudeMidConvo*）：
    system/toolChanges 覆盖 opus-4.8/5/5.5、sonnet-5.5、haiku-5.5、fable-5/5-1；
    effort 仅 opus-5 系、sonnet/haiku-5-5、fable-5-1（不含 opus-4.8/fable-5.0）。"""
    from nova_catalog.providers.anthropic import DEF

    sys_models = {
        pattern
        for pattern, corr in DEF.model_corrections.items()
        if (corr.get("compat") or {}).get("supports_mid_convo_system_messages")
    }
    assert sys_models == {
        "claude-opus-4-8*",
        "claude-opus-5*",
        "claude-sonnet-5-5*",
        "claude-haiku-5-5*",
        "claude-fable-5*",
        "claude-fable-5-1*",
    }
    # effort 组独立于 system 组：fable-5-1 同时在两组，fable-5 只在 system 组
    assert (
        DEF.model_corrections["claude-fable-5*"]["compat"].get(
            "supports_mid_convo_effort"
        )
        is None
    )
    assert (
        DEF.model_corrections["claude-fable-5-1*"]["compat"][
            "supports_mid_convo_effort"
        ]
        is True
    )
    assert (
        DEF.model_corrections["claude-opus-4-8*"]["compat"].get(
            "supports_mid_convo_effort"
        )
        is None
    )


class TestDeriveAndHooks:
    def test_derive_from_re_stamps_and_applies_own_corrections(self):
        """跨 provider 派生：取源家成品，换本家 api/base_url/provider。"""
        from nova_catalog.context import BuildContext
        from nova_catalog.sources.models_dev import ModelsDevSource

        parent = ProviderDefinition(
            id="parent",
            name="P",
            base_url="http://p/v1",
            api=KnownApi.OPENAI_COMPLETIONS,
        )
        child = ProviderDefinition(
            id="child",
            name="C",
            base_url="http://c/v1",
            api=KnownApi.ANTHROPIC_MESSAGES,
            derive_from="parent",
            compat_defaults={"x": 1},
        )
        ctx = BuildContext(
            {"parent": parent, "child": child},
            ModelsDevSource(),
        )
        ctx.set_raw("parent", {"m1": _raw_entry()})
        ctx.register_build_fn(
            lambda p: __import__(
                "nova_catalog.build", fromlist=["build_one"]
            ).build_one(p, ctx)
        )
        models = __import__("nova_catalog.build", fromlist=["build_one"]).build_one(
            child, ctx
        )
        assert len(models) == 1
        m = models[0]
        assert m["provider"] == "child"
        assert m["base_url"] == "http://c/v1"
        assert m["api"] == "anthropic-messages"
        assert m["compat"]["x"] == 1

    def test_derive_cycle_raises(self):
        from nova_catalog.context import BuildContext
        from nova_catalog.sources.models_dev import ModelsDevSource

        a = ProviderDefinition(
            id="a",
            name="a",
            base_url="x",
            api=KnownApi.OPENAI_COMPLETIONS,
            derive_from="a",
        )
        ctx = BuildContext({"a": a}, ModelsDevSource())
        ctx.register_build_fn(
            lambda p: __import__(
                "nova_catalog.build", fromlist=["build_one"]
            ).build_one(p, ctx)
        )
        import pytest as _pt

        with _pt.raises(ValueError, match="derive cycle"):
            ctx.provider_models("a")

    def test_finalize_and_finalize_all_hooks_run_in_order(self):
        """finalize（per-model）先于 finalize_all（跨模型）。"""
        calls = []

        def _finalize(model_id, fields, ctx):
            calls.append(("finalize", model_id))
            fields["custom"] = True

        def _finalize_all(models, ctx):
            calls.append(("finalize_all", len(models)))

        definition = ProviderDefinition(
            id="p1",
            name="P1",
            base_url="http://x/v1",
            api=KnownApi.OPENAI_COMPLETIONS,
            finalize=_finalize,
            finalize_all=_finalize_all,
        )
        from nova_catalog.context import BuildContext
        from nova_catalog.sources.models_dev import ModelsDevSource

        ctx = BuildContext({"p1": definition}, ModelsDevSource())
        ctx.set_raw("p1", {"m1": _raw_entry(), "m2": _raw_entry("m2")})
        from nova_catalog.build import build_one

        models = build_one(definition, ctx)
        assert [c[0] for c in calls] == ["finalize", "finalize", "finalize_all"]
        assert all(m.get("custom") is True for m in models)
