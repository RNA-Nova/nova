"""native effort 门控与级别表边界测试。"""

import pytest
from nova_catalog.reasoning import apply_models_dev_effort, effort_to_thinking_level_map


@pytest.mark.parametrize("values", [["default"], ["bogus"], [None, "default"], []])
def test_bogus_or_default_only_has_no_level_map(values):
    assert effort_to_thinking_level_map({"supported": True, "effort": values}) is None


def test_non_native_openai_dialect_does_not_receive_effort_map():
    fields = {
        "api": "openai-completions",
        "compat": {"thinking_format": "deepseek"},
    }
    apply_models_dev_effort(fields, {"supported": True, "effort": ["low", "high"]})
    assert "thinking_level_map" not in fields


def test_native_effort_merges_without_overriding_family_facts():
    fields = {
        "api": "openai-completions",
        "compat": {"thinking_format": "openai"},
        "thinking_level_map": {"off": None},
    }
    apply_models_dev_effort(
        fields, {"supported": True, "effort": ["none", "low", "high"]}
    )
    assert fields["thinking_level_map"]["off"] is None
    assert fields["thinking_level_map"]["high"] == "high"


def test_anthropic_legacy_budget_gets_disabled_even_without_native_effort():
    fields = {"api": "anthropic-messages", "compat": {}}
    apply_models_dev_effort(
        fields, {"supported": True, "effort": ["low"], "budget": {"min": 1024}}
    )
    assert fields["thinking_level_map"]["off"] == "disabled"


@pytest.mark.parametrize("compat", [{}, {"force_adaptive_thinking": True}])
def test_anthropic_explicit_null_off_survives_legacy_budget(compat):
    fields = {
        "api": "anthropic-messages",
        "compat": compat,
        "thinking_level_map": {"off": None},
    }
    apply_models_dev_effort(
        fields, {"supported": True, "effort": ["low"], "budget": {"min": 1024}}
    )
    assert fields["thinking_level_map"]["off"] is None
