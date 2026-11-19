"""公开 build_catalog 的数据流与 provider 钩子测试。"""

import pytest
from nova_protocol import KnownApi, ModelFields

from nova_catalog.build import build_catalog
from nova_catalog.define import define_provider, models_dev_models
from nova_catalog.providers.openrouter import DEF as OPENROUTER
from tests._helpers import (
    catalog_sources,
    image_fields,
    model_fields,
    raw_model,
    raw_openrouter_image,
)


def _models(ctx, provider_id="test", base_url="https://example.com/v1", **kwargs):
    return models_dev_models(
        ctx,
        provider_id,
        api=kwargs.pop("api", KnownApi.OPENAI_COMPLETIONS),
        base_url=base_url,
        **kwargs,
    )


def _definition(**overrides):
    provider_id = overrides.pop("id", "test")
    base_url = overrides.pop("base_url", "https://example.com/v1")
    models = overrides.pop("models", None)
    if models is None:
        models = lambda ctx: _models(ctx, provider_id, base_url)
    return define_provider(id=provider_id, models=models, **overrides)


def test_translation_stamp_and_source_effort():
    result = build_catalog(
        [_definition()], catalog_sources({"test": {"models": {"model-a": raw_model()}}})
    )
    model = result["test"].chat[0]
    assert model.get("id") == "model-a"
    assert model.get("provider") == "test"
    assert model.get("base_url") == "https://example.com/v1"
    assert model.get("api") == "openai-completions"
    assert model.get("context_window") == 128000
    assert model.get("thinking_level_map", {}).get("high") == "high"


def test_pinned_models_win_and_strict_rejects_bad_ids():
    provider = _definition(
        models=lambda ctx: _models(
            ctx, pinned_models={"model-a": model_fields(name="Pinned")}
        )
    )
    source = catalog_sources(
        {
            "test": {
                "models": {
                    "model-a": raw_model(name="Source"),
                    "bad": raw_model(id=None),
                }
            }
        }
    )
    assert build_catalog([provider], source)["test"].chat[0].get("name") == "Pinned"
    with pytest.raises(ValueError, match="missing id"):
        build_catalog([provider], source, strict=True)


def test_strict_missing_slice_fails():
    with pytest.raises(ValueError, match="missing a valid models slice"):
        build_catalog([_definition()], catalog_sources(), strict=True)


def test_compat_defaults_are_isolated_by_api():
    provider = _definition(
        models=lambda ctx: models_dev_models(
            ctx,
            "unused",
            api=KnownApi.OPENAI_COMPLETIONS,
            base_url="https://example.com/v1",
            pinned_models={
                "chat": model_fields("chat", api="openai-completions"),
                "message": model_fields("message", api="anthropic-messages"),
            },
        ),
        compat={
            "openai-completions": {"dialect": "openai"},
            "anthropic-messages": {"dialect": "anthropic"},
        },
    )
    result = build_catalog([provider], catalog_sources())
    assert result["test"].chat[0].get("compat") == {"dialect": "openai"}
    assert result["test"].chat[1].get("compat") == {"dialect": "anthropic"}


def test_finalize_runs_after_shared_effort_and_before_finalize_all():
    calls = []

    def finalize(fields, _ctx):
        assert fields.get("thinking_level_map", {}).get("high") == "high"
        fields["name"] = "finalized"
        calls.append(("finalize", fields.get("id")))

    def finalize_all(models, _ctx):
        assert all(model.get("name") == "finalized" for model in models)
        calls.append(("finalize_all", len(models)))

    provider = _definition(finalize=finalize, finalize_all=finalize_all)
    result = build_catalog(
        [provider], catalog_sources({"test": {"models": {"model-a": raw_model()}}})
    )
    assert result["test"].chat[0].get("name") == "finalized"
    assert calls == [("finalize", "model-a"), ("finalize_all", 1)]


def test_image_defaults_apply_after_provider_finalize():
    def finalize(fields, _ctx):
        assert "input_limits" not in fields

    provider = _definition(finalize=finalize)
    source = catalog_sources(
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
    result = build_catalog([provider], source)
    resize = result["test"].chat[0].get("input_limits", {})["images"]["resize"]
    assert resize["max_width"] == 2000
    assert resize["max_bytes"] == int(4.5 * 1024 * 1024)


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("model_type", ["imgae", ""])
def test_unknown_pinned_model_type_fails_regardless_of_strictness(strict, model_type):
    provider = _definition(
        models=lambda ctx: _models(
            ctx, pinned_models={"typo": image_fields("typo", type=model_type)}
        )
    )
    with pytest.raises(ValueError, match="unknown pinned model type"):
        build_catalog([provider], catalog_sources(), strict=strict)


def test_image_only_provider_builds_separate_catalog():
    result = build_catalog(
        [OPENROUTER], catalog_sources(openrouter_images=[raw_openrouter_image()])
    )
    catalog = result["openrouter"]
    assert catalog.chat == []
    image = catalog.images[0]
    assert image.get("type") == "image"
    assert image.get("api") == "openrouter-images"
    assert image.get("output_types") == ["image"]
    assert image.get("cost", {}).get("input") == 1
    assert image.get("input_limits", {})["images"]["resize"]["max_width"] == 2000
    with pytest.raises(ValueError, match="no usable image models"):
        build_catalog([OPENROUTER], catalog_sources(), strict=True)


def test_map_model_rename_moves_source_provenance_to_final_id():
    observed = []

    def rename(fields, _source):
        result = ModelFields(**fields)
        result["id"] = "renamed"
        return result

    def finalize(fields, ctx):
        observed.append(ctx.models_dev_source(fields))

    raw = raw_model("source-id")
    provider = _definition(
        models=lambda ctx: _models(ctx, map_model=rename), finalize=finalize
    )
    result = build_catalog(
        [provider], catalog_sources({"test": {"models": {"source-id": raw}}})
    )
    assert result["test"].chat[0].get("id") == "renamed"
    assert observed == [raw]
    assert result["test"].chat[0].get("thinking_level_map", {}).get("high") == "high"


def test_entry_id_wins_over_outer_dictionary_key():
    result = build_catalog(
        [_definition()],
        catalog_sources({"test": {"models": {"alias-key": raw_model("canonical")}}}),
        strict=True,
    )
    assert result["test"].chat[0].get("id") == "canonical"


def test_mapped_id_collision_keeps_first_model_and_its_provenance():
    def rename(fields, _source):
        return ModelFields(**{**fields, "id": "same"})

    def raw_with_effort(model_id, name, effort):
        return raw_model(
            model_id,
            name=name,
            capabilities={
                "tools": {"supported": True},
                "reasoning": {"supported": True, "effort": [effort]},
            },
        )

    provider = _definition(models=lambda ctx: _models(ctx, map_model=rename))
    result = build_catalog(
        [provider],
        catalog_sources(
            {
                "test": {
                    "models": {
                        "a": raw_with_effort("a", "A", "low"),
                        "b": raw_with_effort("b", "B", "high"),
                    }
                }
            }
        ),
        strict=True,
    )
    assert len(result["test"].chat) == 1
    model = result["test"].chat[0]
    assert model.get("name") == "A"
    assert model.get("thinking_level_map", {}).get("low") == "low"
    assert model.get("thinking_level_map", {}).get("high") is None


def test_custom_models_hook_filters_and_rewrites_without_builder_special_cases():
    """NVIDIA 风格：provider 自行组合在线 ID 过滤与 ID 改写。"""

    def custom(ctx):
        assert "test" in ctx.sources.models_dev
        built = models_dev_models(
            ctx,
            "test",
            api=KnownApi.OPENAI_COMPLETIONS,
            base_url="https://example.com/v1",
        )
        result = []
        for fields in built:
            if fields.get("id") == "source-model":
                fields["id"] = "live-model"
                result.append(fields)
        return result

    source = catalog_sources(
        {
            "test": {
                "models": {
                    "offline-model": raw_model("offline-model"),
                    "source-model": raw_model("source-model"),
                }
            }
        }
    )
    provider = define_provider(id="test", models=custom)
    result = build_catalog([provider], source)
    assert [model.get("id") for model in result["test"].chat] == ["live-model"]


def test_provider_cannot_stamp_models_for_another_provider():
    provider = define_provider(
        id="test", models=lambda _ctx: [model_fields(provider="other")]
    )
    with pytest.raises(ValueError, match="another provider"):
        build_catalog([provider], catalog_sources())
