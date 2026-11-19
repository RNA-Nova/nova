"""OpenRouter 图像源翻译、分层价与拉取边界测试。"""

import httpx
import pytest
import respx

from nova_catalog.sources.openrouter import (
    OPENROUTER_DECISIONS_URL,
    OPENROUTER_IMAGES_URL,
    OpenRouterDecisionSource,
    OpenRouterImageSource,
    openrouter_cost,
    openrouter_decisions_to_nova,
    openrouter_images_to_nova,
)
from tests._helpers import raw_openrouter_decision, raw_openrouter_image


def test_openrouter_image_translation_and_cost_tiers():
    entry = raw_openrouter_image(
        architecture={
            "input_modalities": ["text", "image"],
            "output_modalities": ["text", "image"],
        },
        pricing={
            "prompt": "0.000001",
            "completion": "0.000002",
            "input_cache_read": "0.0000001",
            "input_cache_write": "0",
            "overrides": [
                {
                    "min_prompt_tokens": 1000,
                    "prompt": "0.000003",
                    "completion": "0.000004",
                }
            ],
        },
    )
    text_only = raw_openrouter_image(
        "text-only",
        architecture={"input_modalities": ["text"], "output_modalities": ["text"]},
    )
    models = openrouter_images_to_nova("openrouter", [entry, text_only])
    assert len(models) == 1
    model = models[0]
    assert model.get("type") == "image"
    assert model.get("input_types") == ["text", "image"]
    assert model.get("output_types") == ["text", "image"]
    assert model.get("cost") == {
        "input": 1,
        "output": 2,
        "cache_read": 0.1,
        "cache_write": 0,
        "tiers": [
            {
                "inputTokensAbove": 1000,
                "input": 3,
                "output": 4,
                "cache_read": 0.1,
                "cache_write": 0,
            }
        ],
    }


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_image_source_payload():
    route = respx.get(OPENROUTER_IMAGES_URL).mock(
        return_value=httpx.Response(200, json={"data": [raw_openrouter_image()]})
    )
    result = await OpenRouterImageSource().payload()
    assert result == [raw_openrouter_image()]
    assert route.call_count == 1


def test_openrouter_overrides_skip_unknown_conditions_and_merge_same_threshold():
    pricing = {
        "prompt": "0.000001",
        "completion": "0.000002",
        "overrides": [
            {"min_prompt_tokens": 1000, "prompt": "0.000003"},
            {"min_prompt_tokens": 1000, "completion": "0.000005"},
            {"min_prompt_tokens": 1000, "prompt": "0.000007"},
            {"min_prompt_tokens": 500, "prompt": "0.000002"},
            {
                "min_prompt_tokens": 1000,
                "future_condition": "future",
                "prompt": "9",
            },
        ],
    }
    tiers = openrouter_cost(pricing)["tiers"]
    by_threshold = {tier["inputTokensAbove"]: tier for tier in tiers}
    assert set(by_threshold) == {500, 1000}
    assert by_threshold[500]["input"] == 2
    assert by_threshold[1000]["input"] == 7
    assert by_threshold[1000]["output"] == 5


def test_openrouter_strict_rejects_malformed_entry_even_with_valid_sibling():
    entries = [
        raw_openrouter_image(),
        raw_openrouter_image("bad", architecture=[]),
    ]
    with pytest.raises(ValueError, match="architecture"):
        openrouter_images_to_nova("openrouter", entries, strict=True)


def test_openrouter_boolean_price_uses_fallback():
    assert openrouter_cost({"prompt": True})["input"] == 0
    assert openrouter_cost({"prompt": False})["input"] == 0


def test_openrouter_negative_router_price_uses_fallback_in_strict_mode():
    # openrouter/auto 等路由器模型以 -1 表示价格要到选定业务模型后才知道。
    assert openrouter_cost({"prompt": "-1"}, strict=True)["input"] == 0


def test_openrouter_decision_translation_preserves_classifier_contract():
    decision = raw_openrouter_decision()
    ignored = raw_openrouter_decision(
        "chat-only", architecture={"output_modalities": ["text"]}
    )
    models = openrouter_decisions_to_nova("openrouter", [decision, ignored])
    assert len(models) == 1
    model = models[0]
    assert model.get("type") == "classifier"
    assert model.get("api") == "typesafe-system-one"
    assert model.get("context_window") == 32000
    assert model.get("cost", {}).get("input") == 0.042
    assert model.get("compat") == {
        "image_input": "state-image-urls",
        "send_image_opt_in_header": False,
    }


def test_openrouter_decision_strict_rejects_boolean_context_window():
    entry = raw_openrouter_decision(context_length=True)
    with pytest.raises(ValueError, match="positive integer"):
        openrouter_decisions_to_nova("openrouter", [entry], strict=True)
    unknown = openrouter_decisions_to_nova(
        "openrouter", [raw_openrouter_decision(context_length=0)], strict=True
    )
    assert unknown[0].get("context_window") == 4096


@pytest.mark.parametrize(
    ("top_context", "entry_context", "expected"),
    [
        (32000, 64000, 32000),
        (0, 64000, 64000),
        (None, 64000, 64000),
        (None, 0, 4096),
    ],
)
def test_openrouter_decision_context_uses_pi_truthy_fallback(
    top_context, entry_context, expected
):
    entry = raw_openrouter_decision(
        context_length=entry_context,
        top_provider={"context_length": top_context},
    )
    models = openrouter_decisions_to_nova("openrouter", [entry], strict=True)
    assert models[0].get("context_window") == expected


@pytest.mark.parametrize("kind", ["image", "decision"])
def test_strict_rejects_non_string_modality_members(kind):
    image = raw_openrouter_image(
        architecture={"input_modalities": ["text", 1], "output_modalities": ["image"]}
    )
    decision = raw_openrouter_decision(
        architecture={
            "input_modalities": ["text", True],
            "output_modalities": ["decisions"],
        }
    )
    with pytest.raises(ValueError, match="array of strings"):
        if kind == "image":
            openrouter_images_to_nova("openrouter", [image], strict=True)
        else:
            openrouter_decisions_to_nova("openrouter", [decision], strict=True)


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_decision_source_payload():
    route = respx.get(OPENROUTER_DECISIONS_URL).mock(
        return_value=httpx.Response(200, json={"data": [raw_openrouter_decision()]})
    )
    result = await OpenRouterDecisionSource().payload()
    assert result == [raw_openrouter_decision()]
    assert route.call_count == 1
