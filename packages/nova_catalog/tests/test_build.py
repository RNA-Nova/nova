"""公开 build_catalog 的完整数据流测试。"""

import pytest
from nova_catalog.build import build_catalog
from nova_catalog.define import define_provider
from nova_protocol import KnownApi
from tests._helpers import FakeSource, model_fields, raw_model


def _definition(**overrides):
    fields = {
        "id": "test",
        "name": "Test",
        "base_url": "https://example.com/v1",
        "api": KnownApi.OPENAI_COMPLETIONS,
    }
    fields.update(overrides)
    return define_provider(**fields)


@pytest.mark.asyncio
async def test_translation_stamp_and_source_effort():
    provider = _definition()
    source = FakeSource({"test": {"models": {"model-a": raw_model()}}})
    result = await build_catalog([provider], source)
    model = result["test"][0]
    assert model["id"] == "model-a"
    assert model["provider"] == "test"
    assert model["base_url"] == "https://example.com/v1"
    assert model["api"] == "openai-completions"
    assert model["context_window"] == 128000
    assert model["thinking_level_map"]["high"] == "high"


@pytest.mark.asyncio
async def test_pinned_models_win_and_strict_rejects_bad_ids():
    pinned = model_fields(name="Pinned")
    provider = _definition(pinned_models={"model-a": pinned})
    source = FakeSource(
        {
            "test": {
                "models": {
                    "model-a": raw_model(name="Source"),
                    "bad": raw_model(id=None),
                }
            }
        }
    )
    result = await build_catalog([provider], source)
    assert result["test"][0]["name"] == "Pinned"
    with pytest.raises(ValueError, match="missing id"):
        await build_catalog([provider], source, strict=True)


@pytest.mark.asyncio
async def test_strict_missing_slice_and_transport_failure():
    provider = _definition()
    with pytest.raises(ValueError, match="missing a valid models slice"):
        await build_catalog([provider], FakeSource(), strict=True)
    with pytest.raises(RuntimeError, match="offline"):
        await build_catalog(
            [provider], FakeSource(error=RuntimeError("offline")), strict=True
        )


@pytest.mark.asyncio
async def test_non_strict_failed_source_keeps_pinned_models():
    provider = _definition(pinned_models={"model-a": model_fields()})
    result = await build_catalog([provider], FakeSource(error=RuntimeError("offline")))
    assert [model["id"] for model in result["test"]] == ["model-a"]


@pytest.mark.asyncio
async def test_compat_defaults_are_isolated_by_api():
    provider = _definition(
        compat_defaults={"shared": True},
        compat_defaults_by_api={
            "openai-completions": {"dialect": "openai"},
            "anthropic-messages": {"dialect": "anthropic"},
        },
        pinned_models={
            "chat": model_fields("chat", api="openai-completions"),
            "message": model_fields("message", api="anthropic-messages"),
        },
    )
    result = await build_catalog([provider], FakeSource())
    assert result["test"][0]["compat"] == {"shared": True, "dialect": "openai"}
    assert result["test"][1]["compat"] == {
        "shared": True,
        "dialect": "anthropic",
    }


@pytest.mark.asyncio
async def test_finalize_runs_after_shared_effort_and_before_finalize_all():
    calls = []

    def finalize(model_id, fields, _ctx):
        assert fields["thinking_level_map"]["high"] == "high"
        fields["custom"] = True
        calls.append(("finalize", model_id))

    def finalize_all(models, _ctx):
        assert all(model.get("custom") for model in models)
        calls.append(("finalize_all", len(models)))

    provider = _definition(finalize=finalize, finalize_all=finalize_all)
    source = FakeSource({"test": {"models": {"model-a": raw_model()}}})
    result = await build_catalog([provider], source)
    assert result["test"][0]["custom"] is True
    assert calls == [("finalize", "model-a"), ("finalize_all", 1)]


@pytest.mark.asyncio
async def test_derive_copies_models_and_preserves_source_provenance():
    parent = _definition(id="parent", name="Parent")
    child = _definition(
        id="child",
        name="Child",
        base_url="https://child.example.com/v1",
        api=KnownApi.ANTHROPIC_MESSAGES,
        derive_from="parent",
    )
    source = FakeSource(
        {"parent": {"models": {"claude-opus-5": raw_model("claude-opus-5")}}}
    )
    result = await build_catalog([parent, child], source)
    derived = result["child"][0]
    assert derived["id"] == "claude-opus-5"
    assert derived["provider"] == "child"
    assert derived["base_url"] == "https://child.example.com/v1"
    assert derived["api"] == "anthropic-messages"
    assert derived["thinking_level_map"]["high"] == "high"
    assert result["parent"][0]["api"] == "openai-completions"


@pytest.mark.asyncio
async def test_derive_cycle_and_unknown_parent_fail():
    first = _definition(id="a", name="A", derive_from="b")
    second = _definition(id="b", name="B", derive_from="a")
    with pytest.raises(ValueError, match="derive cycle"):
        await build_catalog([first, second], FakeSource())
    orphan = _definition(id="child", name="Child", derive_from="missing")
    with pytest.raises(ValueError, match="Unknown provider"):
        await build_catalog([orphan], FakeSource())


@pytest.mark.asyncio
async def test_image_defaults_apply_after_provider_finalize():
    def finalize(_model_id, fields, _ctx):
        assert "input_limits" not in fields

    provider = _definition(finalize=finalize)
    source = FakeSource(
        {
            "test": {
                "models": {
                    "vision": raw_model(
                        "vision", modalities={"input": ["text", "image"]}
                    )
                }
            }
        }
    )
    result = await build_catalog([provider], source)
    resize = result["test"][0]["input_limits"]["images"]["resize"]
    assert resize["max_width"] == 2000
    assert resize["max_bytes"] == int(4.5 * 1024 * 1024)


@pytest.mark.asyncio
async def test_finalize_all_cannot_mutate_stage_one_seen_by_other_providers():
    def clear_first(models, _ctx):
        models.clear()

    seen = []

    def observe_second(_model_id, _fields, ctx):
        seen.append(len(ctx.provider_models("first")))

    first = _definition(id="first", name="First", finalize_all=clear_first)
    second = _definition(id="second", name="Second", finalize=observe_second)
    source = FakeSource(
        {
            "first": {"models": {"model-a": raw_model()}},
            "second": {"models": {"model-b": raw_model("model-b")}},
        }
    )
    result = await build_catalog([first, second], source)
    assert result["first"] == []
    assert seen == [1]


def test_compat_defaults_by_api_rejects_unknown_api_key():
    with pytest.raises(ValueError, match="Unknown compat_defaults_by_api"):
        _definition(compat_defaults_by_api={"openai-completion": {}})
