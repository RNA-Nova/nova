"""
模型工具函数单元测试。

思考级别相关函数（``clamp_thinking_level`` / ``get_supported_thinking_levels`` /
``to_thinking_level``）直接 re-export 自 ``nova_ai.utils.model_utils``，
此处只锁定 harness 侧依赖的关键语义（TS 吸附规则），完整用例见
``nova_ai/tests``。
"""

from nova_ai import Model, ModelCost, ModelThinkingLevel, ThinkingLevel

from nova_harness.core.utils.model_utils import (
    clamp_thinking_level,
    get_supported_thinking_levels,
    models_are_equal,
    to_thinking_level,
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
    assert get_supported_thinking_levels(m) == [ModelThinkingLevel.OFF]


def test_get_supported_thinking_levels_default():
    """未被显式禁用的级别默认受支持；xhigh 默认不支持。"""
    m = _model(thinking_level_map={"low": "low"})
    assert get_supported_thinking_levels(m) == [
        ModelThinkingLevel.OFF,
        ModelThinkingLevel.MINIMAL,
        ModelThinkingLevel.LOW,
        ModelThinkingLevel.MEDIUM,
        ModelThinkingLevel.HIGH,
    ]


def test_get_supported_thinking_levels_explicitly_disabled():
    """thinking_level_map 中映射为 None 的级别不受支持。"""
    m = _model(thinking_level_map={"off": None, "high": None})
    levels = get_supported_thinking_levels(m)
    assert ModelThinkingLevel.OFF not in levels
    assert ModelThinkingLevel.HIGH not in levels
    assert ModelThinkingLevel.MINIMAL in levels


def test_get_supported_thinking_levels_xhigh_default_not_supported():
    m = _model(thinking_level_map={"high": "high"})
    assert ModelThinkingLevel.XHIGH not in get_supported_thinking_levels(m)


def test_get_supported_thinking_levels_xhigh_explicitly_supported():
    m = _model(thinking_level_map={"xhigh": "xhigh"})
    assert ModelThinkingLevel.XHIGH in get_supported_thinking_levels(m)


def test_get_supported_thinking_levels_unknown_map_key_ignored():
    """不在扩展级别集合中的 map 键直接忽略。"""
    m = _model(thinking_level_map={"weird": "weird"})
    levels = get_supported_thinking_levels(m)
    assert all(isinstance(level, ModelThinkingLevel) for level in levels)


def test_clamp_thinking_level_already_supported():
    m = _model(thinking_level_map={"medium": "medium"})
    assert (
        clamp_thinking_level(m, ModelThinkingLevel.MEDIUM) == ModelThinkingLevel.MEDIUM
    )


def test_clamp_thinking_level_no_reasoning_returns_off():
    m = _model(reasoning=False)
    assert clamp_thinking_level(m, ModelThinkingLevel.HIGH) == ModelThinkingLevel.OFF


def test_clamp_thinking_level_prefers_upward():
    """请求的级别不受支持时，先向更高级别吸附。"""
    m = _model(
        thinking_level_map={
            "off": None,
            "minimal": None,
            "low": None,
            "medium": None,
            "high": "high",
        }
    )
    assert clamp_thinking_level(m, ModelThinkingLevel.LOW) == ModelThinkingLevel.HIGH


def test_clamp_thinking_level_snaps_down_to_nearest():
    """向上找不到时向更低级别吸附。"""
    m = _model(
        thinking_level_map={
            "off": None,
            "minimal": "minimal",
            "low": None,
            "medium": None,
            "high": None,
        }
    )
    assert (
        clamp_thinking_level(m, ModelThinkingLevel.HIGH) == ModelThinkingLevel.MINIMAL
    )


def test_clamp_thinking_level_all_disabled_returns_off():
    """所有级别都被显式禁用时回退到 OFF。"""
    m = _model(
        thinking_level_map={
            "off": None,
            "minimal": None,
            "low": None,
            "medium": None,
            "high": None,
        }
    )
    assert clamp_thinking_level(m, ModelThinkingLevel.HIGH) == ModelThinkingLevel.OFF


def test_to_thinking_level_off_maps_to_none():
    assert to_thinking_level(ModelThinkingLevel.OFF) is None
    assert to_thinking_level(None) is None
    assert to_thinking_level(ModelThinkingLevel.HIGH) == ThinkingLevel.HIGH
