"""OpenRouter 实际账单总额的归一与覆盖测试。"""

import pytest
from nova_ai.api_impls._shared.openrouter_cost import apply_openrouter_reported_cost
from nova_protocol import KnownApi, Model, ModelCost, Usage


def _model() -> Model:
    return Model(
        id="m",
        name="m",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=1, output=1, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )


@pytest.mark.parametrize("reported_cost", [0, 0.004])
def test_non_byok_uses_reported_total(reported_cost):
    usage = Usage()
    apply_openrouter_reported_cost(
        usage, {"cost": reported_cost, "is_byok": False}, _model()
    )
    assert usage.cost.total == reported_cost
    assert usage.cost.total_from_provider is True


def test_byok_adds_upstream_inference_cost():
    usage = Usage()
    apply_openrouter_reported_cost(
        usage,
        {
            "cost": 0.004,
            "is_byok": True,
            "cost_details": {"upstream_inference_cost": 0.003},
        },
        _model(),
    )
    assert usage.cost.total == pytest.approx(0.007)
    assert usage.cost.total_from_provider is True


def test_incomplete_byok_accounting_keeps_catalog_estimate():
    usage = Usage()
    usage.cost.total = 0.01
    apply_openrouter_reported_cost(
        usage,
        {"cost": 0.004, "is_byok": True, "cost_details": None},
        _model(),
    )
    assert usage.cost.total == 0.01
    assert usage.cost.total_from_provider is None
