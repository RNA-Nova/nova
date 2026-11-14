"""Anthropic provider 定义的构建期行为测试。"""

from dataclasses import replace

from nova_ai.catalog.build import build_catalog
from nova_ai.catalog.define import models_dev_models
from nova_ai.catalog.providers.anthropic import BASE_URL
from nova_ai.catalog.providers.anthropic import DEF as ANTHROPIC
from nova_protocol import KnownApi
from tests.catalog._helpers import catalog_sources, model_fields, raw_model


def test_anthropic_direct_finalize_uses_family_predicates():
    source = catalog_sources(
        {"anthropic": {"models": {"claude-opus-5": raw_model("claude-opus-5")}}}
    )
    result = build_catalog([ANTHROPIC], source)
    model = result["anthropic"].chat[0]
    compat = model.get("compat")
    levels = model.get("thinking_level_map")
    assert compat is not None
    assert levels is not None
    assert compat.get("supports_mid_convo_effort") is True
    assert compat.get("supports_mid_convo_system_messages") is True
    assert levels.get("low") == "low"
    assert levels.get("high") == "high"
    assert levels.get("minimal") is None
    assert model.get("prompt_cache") == {"short": 300, "long": 3600}


def test_anthropic_fallback_wiring_uses_live_catalog_costs():
    ids = ["claude-fable-5", "claude-opus-4-8", "claude-opus-5"]
    source = catalog_sources(
        {"anthropic": {"models": {id: raw_model(id) for id in ids}}}
    )
    result = build_catalog([ANTHROPIC], source)
    by_id = {model.get("id"): model for model in result["anthropic"].chat}
    fable_compat = by_id["claude-fable-5"].get("compat")
    opus_compat = by_id["claude-opus-5"].get("compat")
    assert fable_compat is not None
    assert opus_compat is not None
    fallback = fable_compat.get("allowed_fallback_models")
    assert fallback is not None
    assert [entry["model"] for entry in fallback] == [
        "claude-opus-4-8",
        "claude-opus-5",
    ]
    assert "allowed_fallback_models" not in opus_compat


def test_anthropic_input_limits_only_apply_to_images_and_preserve_narrowing():
    text = model_fields("claude-text", api="anthropic-messages", input_types=["text"])
    vision = model_fields(
        "claude-vision",
        api="anthropic-messages",
        input_types=["text", "image"],
        input_limits={"images": {"max_per_request": 2}},
    )
    provider = replace(
        ANTHROPIC,
        models=lambda ctx: models_dev_models(
            ctx,
            "anthropic",
            api=KnownApi.ANTHROPIC_MESSAGES,
            base_url=BASE_URL,
            pinned_models={"claude-text": text, "claude-vision": vision},
        ),
    )
    result = build_catalog([provider], catalog_sources())
    by_id = {model.get("id"): model for model in result["anthropic"].chat}
    assert "input_limits" not in by_id["claude-text"]
    limits = by_id["claude-vision"].get("input_limits")
    assert limits is not None
    assert limits["max_request_bytes"] == 32 * 1024 * 1024
    assert limits["images"]["max_per_request"] == 2
    assert limits["images"]["resize"]["max_width"] == 2000
