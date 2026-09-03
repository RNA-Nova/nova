"""
工具函数测试
"""

import pytest

from nova_ai.api_impls._shared import build_base_options, clamp_max_tokens_to_context
from nova_ai.types import (
    AssistantMessage,
    Context,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    ModelThinkingLevel,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    ThinkingLevel,
    ToolCall,
    Usage,
    UserMessage,
)
from nova_ai.utils import calculate_cost, get_supported_thinking_levels
from nova_ai.utils.model_utils import clamp_thinking_level, to_thinking_level


class TestCalculateCost:
    """成本计算测试"""

    def test_basic_cost(self):
        model = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.0),
            context_window=128000,
            max_tokens=4096,
        )
        usage = Usage(input=1000000, output=500000, cache_read=200000, cache_write=0)
        cost = calculate_cost(model, usage)

        assert cost.input == pytest.approx(1.0)
        assert cost.output == pytest.approx(1.0)
        assert cost.cache_read == pytest.approx(0.1)
        assert cost.total == pytest.approx(2.1)

    def test_zero_usage(self):
        model = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.0),
            context_window=128000,
            max_tokens=4096,
        )
        usage = Usage()
        cost = calculate_cost(model, usage)
        assert cost.total == 0.0


class TestGetSupportedThinkingLevels:
    """支持的思考级别测试"""

    def test_no_reasoning(self):
        model = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        assert get_supported_thinking_levels(model) == [ModelThinkingLevel.OFF]

    def test_all_supported(self):
        model = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        # 没有 thinking_level_map 时，xhigh 默认不支持
        assert get_supported_thinking_levels(model) == [
            ModelThinkingLevel.OFF,
            ModelThinkingLevel.MINIMAL,
            ModelThinkingLevel.LOW,
            ModelThinkingLevel.MEDIUM,
            ModelThinkingLevel.HIGH,
        ]

    def test_with_thinking_level_map(self):
        model = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={
                "minimal": None,
                "low": None,
                "medium": None,
                "high": "high",
                "xhigh": "max",
            },
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        levels = get_supported_thinking_levels(model)
        assert ModelThinkingLevel.MINIMAL not in levels
        assert ModelThinkingLevel.LOW not in levels
        assert ModelThinkingLevel.MEDIUM not in levels
        assert ModelThinkingLevel.HIGH in levels
        assert ModelThinkingLevel.XHIGH in levels

    def test_xhigh_default_not_supported(self):
        """xhigh 默认不支持，除非 thinking_level_map 中显式定义"""
        model = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        assert ModelThinkingLevel.XHIGH not in get_supported_thinking_levels(model)

    def test_max_default_not_supported(self):
        """max 默认不支持，除非 thinking_level_map 中显式定义"""
        model = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        assert ModelThinkingLevel.MAX not in get_supported_thinking_levels(model)

    def test_max_explicitly_supported(self):
        """map 显式声明 max 时受支持"""
        model = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={"max": "max"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        levels = get_supported_thinking_levels(model)
        assert ModelThinkingLevel.MAX in levels
        assert ModelThinkingLevel.XHIGH not in levels

    def test_max_hole_between_high_and_max(self):
        """high 与 max 之间可留 xhigh 空洞"""
        model = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={"xhigh": None, "max": "max"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        levels = get_supported_thinking_levels(model)
        assert ModelThinkingLevel.HIGH in levels
        assert ModelThinkingLevel.XHIGH not in levels
        assert ModelThinkingLevel.MAX in levels


class TestBuildBaseOptions:
    """基础选项构建测试"""

    _empty_ctx = Context(messages=[])

    def _model(self, max_tokens=10000, context_window=128000):
        return Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=context_window,
            max_tokens=max_tokens,
        )

    def test_from_simple_options(self):
        simple = SimpleStreamOptions(temperature=0.7, max_tokens=5000)
        opts = build_base_options(self._model(), self._empty_ctx, simple)

        assert opts.temperature == 0.7
        assert opts.max_tokens == 5000

    def test_default_max_tokens_uses_model_cap(self):
        """无显式上限时使用 model.max_tokens（对齐 TS，不再有 32000 魔数）"""
        model = self._model(max_tokens=100000, context_window=128000)
        opts = build_base_options(model, self._empty_ctx)
        # 空上下文：available = 128000 - 0 - 4096 > 100000，取模型上限
        assert opts.max_tokens == 100000

    def test_max_tokens_clamped_to_context_window(self):
        """max_tokens 钳制到上下文剩余窗口（对齐 TS clampMaxTokensToContext）"""
        model = self._model(max_tokens=100000, context_window=10000)
        ctx = Context(messages=[UserMessage(content="x" * 4000)])  # ≈1000 tokens
        opts = build_base_options(model, ctx)
        # available = 10000 - 1000 - 4096 = 4904
        assert opts.max_tokens == 4904

    def test_api_key_override(self):
        simple = SimpleStreamOptions(api_key="key1")
        opts = build_base_options(
            self._model(max_tokens=4096), self._empty_ctx, simple, api_key="key2"
        )
        # 传入的 api_key 优先级更高
        assert opts.api_key == "key2"

    def test_max_retry_delay_ms_passthrough(self):
        """build_base_options 不透传就会二次静默丢字段，必须保留"""
        model = self._model()
        simple = SimpleStreamOptions(max_retry_delay_ms=5000)
        assert (
            build_base_options(model, self._empty_ctx, simple).max_retry_delay_ms
            == 5000
        )
        assert build_base_options(model, self._empty_ctx).max_retry_delay_ms is None


class TestClampMaxTokensToContext:
    """max_tokens 上下文钳制测试（对齐 TS clampMaxTokensToContext）"""

    def _model(self, context_window):
        return Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=context_window,
            max_tokens=100000,
        )

    def test_invalid_context_window_only_floor(self):
        """context_window <= 0 时只做下限保护"""
        assert (
            clamp_max_tokens_to_context(self._model(0), Context(messages=[]), 500)
            == 500
        )
        assert (
            clamp_max_tokens_to_context(self._model(-1), Context(messages=[]), 0) == 1
        )

    def test_normal_clamp(self):
        model = self._model(10000)
        ctx = Context(messages=[UserMessage(content="x" * 4000)])  # ≈1000 tokens
        # available = 10000 - 1000 - 4096 = 4904
        assert clamp_max_tokens_to_context(model, ctx, 100000) == 4904

    def test_floor_when_window_exhausted(self):
        """剩余窗口不足时钳到下限 1，不为负"""
        model = self._model(1000)
        ctx = Context(messages=[UserMessage(content="x" * 40000)])
        assert clamp_max_tokens_to_context(model, ctx, 100000) == 1


class TestClampThinkingLevel:
    """思考级别吸附测试（对齐 pi 的 clampThinkingLevel）"""

    def _model(self, reasoning=True, thinking_level_map=None):
        return Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=reasoning,
            thinking_level_map=thinking_level_map,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )

    def test_supported_level_unchanged(self):
        m = self._model()
        assert (
            clamp_thinking_level(m, ModelThinkingLevel.HIGH) == ModelThinkingLevel.HIGH
        )

    def test_snap_up_to_nearest(self):
        # low 显式不支持 → 向上吸附到 medium
        m = self._model(thinking_level_map={"low": None})
        assert (
            clamp_thinking_level(m, ModelThinkingLevel.LOW) == ModelThinkingLevel.MEDIUM
        )

    def test_snap_down_when_no_higher(self):
        # xhigh 未显式声明支持 → 向下吸附到 high
        m = self._model()
        assert (
            clamp_thinking_level(m, ModelThinkingLevel.XHIGH) == ModelThinkingLevel.HIGH
        )

    def test_no_reasoning_returns_off(self):
        m = self._model(reasoning=False)
        assert (
            clamp_thinking_level(m, ModelThinkingLevel.HIGH) == ModelThinkingLevel.OFF
        )

    def test_xhigh_explicitly_supported(self):
        m = self._model(thinking_level_map={"xhigh": "max"})
        assert (
            clamp_thinking_level(m, ModelThinkingLevel.XHIGH)
            == ModelThinkingLevel.XHIGH
        )

    def test_max_explicitly_supported(self):
        m = self._model(thinking_level_map={"max": "max"})
        assert clamp_thinking_level(m, ModelThinkingLevel.MAX) == ModelThinkingLevel.MAX

    def test_xhigh_snaps_up_to_max_when_only_max_supported(self):
        """xhigh 未支持但 max 支持时，向上吸附到 max"""
        m = self._model(thinking_level_map={"xhigh": None, "max": "max"})
        assert (
            clamp_thinking_level(m, ModelThinkingLevel.XHIGH) == ModelThinkingLevel.MAX
        )

    def test_max_snaps_down_when_not_supported(self):
        """max 未支持时向下吸附到最近支持级"""
        m = self._model()
        assert (
            clamp_thinking_level(m, ModelThinkingLevel.MAX) == ModelThinkingLevel.HIGH
        )


class TestToThinkingLevel:
    """状态侧 → 请求侧级别转换测试"""

    def test_off_and_none_to_none(self):
        assert to_thinking_level(ModelThinkingLevel.OFF) is None
        assert to_thinking_level(None) is None

    def test_value_conversion(self):
        result = to_thinking_level(ModelThinkingLevel.HIGH)
        assert result is ThinkingLevel.HIGH
        assert to_thinking_level(ModelThinkingLevel.XHIGH) is ThinkingLevel.XHIGH

    def test_accepts_string_and_request_side_enum(self):
        # 边界宽容：字符串或 ThinkingLevel 输入按值归一
        assert to_thinking_level("high") is ThinkingLevel.HIGH
        assert to_thinking_level("off") is None
        assert to_thinking_level(ThinkingLevel.LOW) is ThinkingLevel.LOW
        assert to_thinking_level("max") is ThinkingLevel.MAX
        assert to_thinking_level(ModelThinkingLevel.MAX) is ThinkingLevel.MAX
