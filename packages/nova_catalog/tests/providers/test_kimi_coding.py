"""Kimi Coding provider 的能力与订阅折算价测试。"""

from nova_catalog.build import build_catalog
from nova_catalog.providers.kimi_coding import DEF as KIMI_CODING
from tests._helpers import catalog_sources, raw_model


def test_kimi_highspeed_does_not_receive_tool_system_additions():
    source = catalog_sources(
        {
            "kimi-code-plan-global": {
                "models": {
                    "kimi-for-coding-highspeed": raw_model(
                        "kimi-for-coding-highspeed",
                        capabilities={
                            "tools": {"supported": True},
                            "reasoning": {"supported": True, "toggle": True},
                        },
                    ),
                    "k3": raw_model("k3"),
                }
            }
        }
    )
    result = build_catalog([KIMI_CODING], source)
    highspeed = next(
        model
        for model in result["kimi-coding"].chat
        if model.get("id") == "kimi-for-coding-highspeed"
    )
    k3 = next(model for model in result["kimi-coding"].chat if model.get("id") == "k3")
    highspeed_compat = highspeed.get("compat")
    highspeed_levels = highspeed.get("thinking_level_map")
    k3_compat = k3.get("compat")
    assert highspeed_compat is not None
    assert highspeed_levels is not None
    assert k3_compat is not None
    assert "supports_mid_convo_tool_additions" not in highspeed_compat
    assert highspeed_levels.get("off") is None
    assert k3_compat.get("supports_mid_convo_tool_additions") is True


def test_kimi_source_effort_can_promote_highspeed_to_k3_style():
    source = catalog_sources(
        {
            "kimi-code-plan-global": {
                "models": {
                    "kimi-for-coding-highspeed": raw_model("kimi-for-coding-highspeed")
                }
            }
        }
    )
    result = build_catalog([KIMI_CODING], source)
    model = result["kimi-coding"].chat[0]
    compat = model.get("compat")
    levels = model.get("thinking_level_map")
    assert compat is not None
    assert levels is not None
    assert compat.get("thinking_format") == "openai"
    assert compat.get("supports_mid_convo_tool_additions") is True
    assert compat.get("supports_strict_mode") is False
    assert levels.get("high") == "high"


def test_kimi_unsupported_reasoning_does_not_enable_k3_style_from_stale_effort():
    source = catalog_sources(
        {
            "kimi-code-plan-global": {
                "models": {
                    "kimi-for-coding-highspeed": raw_model(
                        "kimi-for-coding-highspeed",
                        capabilities={
                            "tools": {"supported": True},
                            "reasoning": {"supported": False, "effort": ["low"]},
                        },
                    )
                }
            }
        }
    )
    result = build_catalog([KIMI_CODING], source)
    model = result["kimi-coding"].chat[0]
    compat = model.get("compat")
    assert model.get("reasoning") is False
    assert compat is not None
    assert compat.get("thinking_format") == "deepseek"
    assert "supports_mid_convo_tool_additions" not in compat


def test_kimi_implied_flat_cost_discards_source_zero_price_tiers():
    zero_cost = {
        "text": {
            "input": {
                "price": 0,
                "overrides": [
                    {"when": {"context": {"gt": 1000}}, "price": 0},
                ],
            },
            "output": {
                "price": 0,
                "overrides": [
                    {"when": {"context": {"gt": 1000}}, "price": 0},
                ],
            },
        }
    }
    source = catalog_sources(
        {"kimi-code-plan-global": {"models": {"k3": raw_model("k3", cost=zero_cost)}}}
    )
    result = build_catalog([KIMI_CODING], source)
    assert result["kimi-coding"].chat[0].get("cost") == {
        "input": 3,
        "output": 15,
        "cache_read": 0.3,
        "cache_write": 0,
    }
