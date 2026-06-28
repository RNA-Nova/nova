"""
模型工具函数单元测试。
"""

from unittest.mock import patch

from nova_ai import Model, ModelCost, ThinkingLevel

from nova_harness.core.utils.model_utils import (
    clamp_thinking_level,
    get_supported_thinking_levels,
    models_are_equal,
)


def _model(
    provider: str = "openai",
    model_id: str = "gpt-4",
    reasoning: bool = True,
    thinking_level_map=None,
) -> Model:
    cost = ModelCost(input=0, output=0)
    return Model(
        id=model_id,
        name=model_id,
        api="openai",
        provider=provider,
        base_url="",
        reasoning=reasoning,
        input_types=["text"],
        cost=cost,
        context_window=128000,
        max_tokens=4096,
        thinking_level_map=thinking_level_map,
    )


def test_models_are_equal_both_none():
    assert models_are_equal(None, None) is False


def test_models_are_equal_one_none():
    m = _model()
    assert models_are_equal(m, None) is False
    assert models_are_equal(None, m) is False


def test_models_are_equal_same():
    m = _model()
    assert models_are_equal(m, m) is True


def test_models_are_equal_different_id():
    a = _model(model_id="a")
    b = _model(model_id="b")
    assert models_are_equal(a, b) is False


def test_models_are_equal_different_provider():
    a = _model(provider="openai")
    b = _model(provider="anthropic")
    assert models_are_equal(a, b) is False


def test_get_supported_thinking_levels_no_reasoning():
    m = _model(reasoning=False)
    assert get_supported_thinking_levels(m) == [None]


def test_get_supported_thinking_levels_default_levels():
    """未显式禁用任何级别时，返回 off->None 与 minimal/low/medium/high。"""
    m = _model(thinking_level_map={"low": "low"})
    levels = get_supported_thinking_levels(m)
    assert None in levels
    assert ThinkingLevel.MINIMAL in levels
    assert ThinkingLevel.LOW in levels
    assert ThinkingLevel.MEDIUM in levels
    assert ThinkingLevel.HIGH in levels


def test_get_supported_thinking_levels_off_disabled():
    """off 显式映射为 None 时不在支持列表。"""
    m = _model(thinking_level_map={"off": None})
    levels = get_supported_thinking_levels(m)
    assert None not in levels


def test_get_supported_thinking_levels_xhigh_default_not_supported():
    m = _model(thinking_level_map={"high": "high"})
    levels = get_supported_thinking_levels(m)
    assert ThinkingLevel.XHIGH not in levels


def test_get_supported_thinking_levels_xhigh_explicitly_supported():
    m = _model(thinking_level_map={"xhigh": "xhigh"})
    levels = get_supported_thinking_levels(m)
    assert ThinkingLevel.XHIGH in levels


def test_get_supported_thinking_levels_unknown_level_not_in_extended_skipped():
    """thinking_level_map 中的键若不在 EXTENDED_THINKING_LEVELS 中则不会被返回。"""
    m = _model(thinking_level_map={"weird": "weird"})
    levels = get_supported_thinking_levels(m)
    assert all(level is None or isinstance(level, ThinkingLevel) for level in levels)
    assert "weird" not in [str(level) for level in levels]


def test_clamp_thinking_level_already_supported():
    m = _model(thinking_level_map={"medium": "medium"})
    assert clamp_thinking_level(m, ThinkingLevel.MEDIUM) == ThinkingLevel.MEDIUM


def test_clamp_thinking_level_falls_back_to_preferred():
    """禁用 medium/low/high，请求 low 时应回退到 minimal。"""
    m = _model(
        thinking_level_map={
            "off": None,
            "minimal": "minimal",
            "low": None,
            "medium": None,
            "high": None,
        }
    )
    assert clamp_thinking_level(m, ThinkingLevel.LOW) == ThinkingLevel.MINIMAL


def test_clamp_thinking_level_no_reasoning_returns_none():
    m = _model(reasoning=False)
    assert clamp_thinking_level(m, ThinkingLevel.HIGH) is None


def test_clamp_thinking_level_empty_supported():
    """所有思考级别都被显式禁用时返回 None。"""
    m = _model(
        thinking_level_map={
            "off": None,
            "minimal": None,
            "low": None,
            "medium": None,
            "high": None,
        }
    )
    assert clamp_thinking_level(m, ThinkingLevel.HIGH) is None


def test_clamp_thinking_level_falls_back_to_first():
    """仅 minimal 可用时，请求 high 回退到 minimal。"""
    m = _model(
        thinking_level_map={
            "off": None,
            "minimal": "minimal",
            "low": None,
            "medium": None,
            "high": None,
        }
    )
    assert clamp_thinking_level(m, ThinkingLevel.HIGH) == ThinkingLevel.MINIMAL


def test_get_supported_thinking_levels_skips_unmappable_strings():
    """底层返回无法映射为 ThinkingLevel 的字符串时跳过。"""
    from nova_harness.core.utils import model_utils

    with patch.object(
        model_utils,
        "_nova_get_supported_thinking_levels",
        return_value=["off", "unknown-level"],
    ):
        levels = get_supported_thinking_levels(_model())
    assert levels == [None]
