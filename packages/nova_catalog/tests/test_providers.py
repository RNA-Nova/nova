"""内置 provider 注册与 pi 关键语义金标（锚定 e5c6182b）。"""

import pytest
from nova_catalog.build import build_catalog
from nova_catalog.families.anthropic import (
    apply_claude_family_rules,
    enable_claude_mid_convo_effort,
    supports_claude_mid_convo_effort,
    supports_claude_mid_convo_system_messages,
)
from nova_catalog.providers import PROVIDERS
from nova_catalog.providers.anthropic import DEF as ANTHROPIC
from nova_catalog.providers.kimi_coding import DEF as KIMI_CODING
from nova_catalog.providers.moonshotai import DEF as MOONSHOTAI
from tests._helpers import FakeSource, model_fields, raw_model


def test_registry_contains_current_builtin_providers():
    assert [provider.id for provider in PROVIDERS] == [
        "anthropic",
        "kimi-coding",
        "moonshotai",
        "moonshotai-cn",
        "volcengine",
    ]


@pytest.mark.parametrize(
    ("model_id", "effort", "system"),
    [
        ("claude-opus-4-8", False, True),
        ("claude-opus-5", True, True),
        ("claude-opus-5-5", True, True),
        ("claude-sonnet-5-5", True, True),
        ("claude-haiku-5-5", True, True),
        ("claude-fable-5", False, True),
        ("claude-fable-5-1", True, True),
        ("claude-opus-4-5", False, False),
    ],
)
def test_claude_mid_convo_predicates(model_id, effort, system):
    assert supports_claude_mid_convo_effort(model_id) is effort
    assert supports_claude_mid_convo_system_messages(model_id) is system


def test_sonnet_5_does_not_inherit_sonnet_55_temperature_restriction():
    fields = model_fields("claude-sonnet-5", api="anthropic-messages")
    apply_claude_family_rules(fields)
    assert fields["compat"]["force_adaptive_thinking"] is True
    assert "supports_temperature" not in fields["compat"]


def test_claude_disabled_thinking_models_keep_existing_off_value():
    fields = model_fields(
        "claude-opus-5",
        api="anthropic-messages",
        thinking_level_map={"off": "none"},
    )
    enable_claude_mid_convo_effort(fields)
    assert fields["thinking_level_map"]["off"] == "none"
    assert fields["compat"]["supports_mid_convo_effort"] is True


@pytest.mark.asyncio
async def test_anthropic_direct_finalize_uses_family_predicates():
    source = FakeSource(
        {"anthropic": {"models": {"claude-opus-5": raw_model("claude-opus-5")}}}
    )
    result = await build_catalog([ANTHROPIC], source)
    model = result["anthropic"][0]
    assert model["compat"]["supports_mid_convo_effort"] is True
    assert model["compat"]["supports_mid_convo_system_messages"] is True
    assert model["thinking_level_map"]["low"] == "low"
    assert model["thinking_level_map"]["high"] == "high"
    assert model["thinking_level_map"]["minimal"] is None
    assert model["prompt_cache"] == {"short": 300, "long": 3600}


@pytest.mark.asyncio
async def test_kimi_highspeed_does_not_receive_tool_system_additions():
    source = FakeSource(
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
    result = await build_catalog([KIMI_CODING], source)
    highspeed = next(
        model
        for model in result["kimi-coding"]
        if model["id"] == "kimi-for-coding-highspeed"
    )
    k3 = next(model for model in result["kimi-coding"] if model["id"] == "k3")
    assert "supports_mid_convo_tool_additions" not in highspeed["compat"]
    assert highspeed["thinking_level_map"]["off"] is None
    assert k3["compat"]["supports_mid_convo_tool_additions"] is True


@pytest.mark.asyncio
async def test_kimi_source_effort_can_promote_highspeed_to_k3_style():
    source = FakeSource(
        {
            "kimi-code-plan-global": {
                "models": {
                    "kimi-for-coding-highspeed": raw_model("kimi-for-coding-highspeed")
                }
            }
        }
    )
    result = await build_catalog([KIMI_CODING], source)
    model = result["kimi-coding"][0]
    assert model["compat"]["thinking_format"] == "openai"
    assert model["compat"]["supports_mid_convo_tool_additions"] is True
    assert model["thinking_level_map"]["high"] == "high"


@pytest.mark.asyncio
async def test_moonshot_k3_reasoning_fallback_and_narrow_family_match():
    def without_reasoning(model_id):
        return raw_model(
            model_id,
            capabilities={
                "tools": {"supported": True},
                "reasoning": {"supported": False},
            },
        )

    source = FakeSource(
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
    result = await build_catalog([MOONSHOTAI], source)
    by_id = {model["id"]: model for model in result["moonshotai"]}
    assert by_id["kimi-k3"]["reasoning"] is True
    assert by_id["kimi-k3"]["compat"]["supports_mid_convo_tool_additions"] is True
    assert "supports_mid_convo_tool_additions" not in by_id["kimi-k30"]["compat"]
    assert "supports_mid_convo_tool_additions" not in by_id["kimi-k3.1.2-pro"]["compat"]


@pytest.mark.asyncio
async def test_anthropic_fallback_wiring_uses_live_catalog_costs():
    ids = ["claude-fable-5", "claude-opus-4-8", "claude-opus-5"]
    source = FakeSource({"anthropic": {"models": {id: raw_model(id) for id in ids}}})
    result = await build_catalog([ANTHROPIC], source)
    by_id = {model["id"]: model for model in result["anthropic"]}
    fallback = by_id["claude-fable-5"]["compat"]["allowed_fallback_models"]
    assert [entry["model"] for entry in fallback] == [
        "claude-opus-4-8",
        "claude-opus-5",
    ]
    assert "allowed_fallback_models" not in by_id["claude-opus-5"]["compat"]
