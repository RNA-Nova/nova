"""Moonshot provider 的 K3 族识别与 reasoning 回退测试。"""

from nova_catalog.build import build_catalog
from nova_catalog.providers.moonshotai import DEF as MOONSHOTAI
from tests._helpers import catalog_sources, raw_model


def test_moonshot_k3_reasoning_fallback_and_narrow_family_match():
    def without_reasoning(model_id):
        return raw_model(
            model_id,
            capabilities={
                "tools": {"supported": True},
                "reasoning": {"supported": False},
            },
        )

    source = catalog_sources(
        {
            "moonshotai": {
                "models": {
                    "kimi-k3": without_reasoning("kimi-k3"),
                    "kimi-k30": without_reasoning("kimi-k30"),
                    "kimi-k3.1.2-pro": without_reasoning("kimi-k3.1.2-pro"),
                }
            }
        }
    )
    result = build_catalog([MOONSHOTAI], source)
    by_id = {model.get("id"): model for model in result["moonshotai"].chat}
    k3 = by_id["kimi-k3"]
    k30 = by_id["kimi-k30"]
    multi_dot = by_id["kimi-k3.1.2-pro"]
    k3_compat = k3.get("compat")
    k30_compat = k30.get("compat")
    multi_dot_compat = multi_dot.get("compat")
    assert k3_compat is not None
    assert k30_compat is not None
    assert multi_dot_compat is not None
    assert k3.get("reasoning") is True
    assert k3_compat.get("supports_mid_convo_tool_additions") is True
    assert "supports_mid_convo_tool_additions" not in k30_compat
    assert "supports_mid_convo_tool_additions" not in multi_dot_compat


def test_moonshot_k3_cost_corrections_do_not_leak_source_tiers():
    def with_tiers(model_id):
        return raw_model(
            model_id,
            cost={
                "text": {
                    "input": {
                        "price": 9,
                        "cache": {"write": 7},
                        "overrides": [
                            {
                                "when": {"context": {"gt": 1000}},
                                "price": 9,
                                "cache": {"write": 7},
                            }
                        ],
                    },
                    "output": {"price": 20},
                }
            },
        )

    source = catalog_sources(
        {
            "moonshotai": {
                "models": {
                    "kimi-k3": with_tiers("kimi-k3"),
                    "kimi-k3-256k": with_tiers("kimi-k3-256k"),
                }
            }
        }
    )
    result = build_catalog([MOONSHOTAI], source)
    by_id = {model.get("id"): model for model in result["moonshotai"].chat}
    assert by_id["kimi-k3"].get("cost") == {
        "input": 3,
        "output": 15,
        "cache_read": 0.3,
        "cache_write": 0,
    }
    tiers = by_id["kimi-k3-256k"].get("cost", {}).get("tiers")
    assert tiers is not None
    assert tiers[0].get("cache_write") == 0
