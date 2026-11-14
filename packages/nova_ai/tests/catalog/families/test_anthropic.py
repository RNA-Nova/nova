"""Claude family 的中途能力判定与思考配置测试。"""

import pytest
from nova_ai.catalog.families.anthropic import (
    apply_claude_family_rules,
    enable_claude_mid_convo_effort,
    supports_claude_mid_convo_effort,
    supports_claude_mid_convo_system_messages,
)
from tests.catalog._helpers import model_fields


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
    compat = fields.get("compat")
    assert compat is not None
    assert compat.get("force_adaptive_thinking") is True
    assert "supports_temperature" not in compat


def test_claude_disabled_thinking_models_keep_existing_off_value():
    fields = model_fields(
        "claude-opus-5",
        api="anthropic-messages",
        thinking_level_map={"off": "none"},
    )
    enable_claude_mid_convo_effort(fields)
    levels = fields.get("thinking_level_map")
    compat = fields.get("compat")
    assert levels is not None
    assert compat is not None
    assert levels.get("off") == "none"
    assert compat.get("supports_mid_convo_effort") is True
