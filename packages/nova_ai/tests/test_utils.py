"""
工具函数测试
"""

import pytest
from nova_ai.types import (
    Model, ModelCost, Usage,
    KnownApi, KnownProvider,
    AssistantMessage, TextContent, ThinkingContent, ToolCall,
    SimpleStreamOptions, ThinkingLevel,
    UserMessage,
)
from nova_ai.utils import (
    calculate_cost,
    supports_xhigh_thinking,
    get_supported_thinking_levels,
    is_context_overflow,
    build_base_options,
    clamp_reasoning,
    transform_messages,
)


class TestCalculateCost:
    """成本计算测试"""

    def test_basic_cost(self):
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.0),
            context_window=128000, max_tokens=4096,
        )
        usage = Usage(input=1000000, output=500000, cache_read=200000, cache_write=0)
        cost = calculate_cost(model, usage)

        assert cost.input == pytest.approx(1.0)
        assert cost.output == pytest.approx(1.0)
        assert cost.cache_read == pytest.approx(0.1)
        assert cost.total == pytest.approx(2.1)

    def test_zero_usage(self):
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.0),
            context_window=128000, max_tokens=4096,
        )
        usage = Usage()
        cost = calculate_cost(model, usage)
        assert cost.total == 0.0


class TestSupportsXHighThinking:
    """xhigh 支持检测测试"""

    def test_gpt_52(self):
        model = Model(
            id="gpt-5.2-test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        assert supports_xhigh_thinking(model) is True

    def test_regular_model(self):
        model = Model(
            id="gpt-4", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        assert supports_xhigh_thinking(model) is False


class TestGetSupportedThinkingLevels:
    """支持的思考级别测试"""

    def test_no_reasoning(self):
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        assert get_supported_thinking_levels(model) == ["off"]

    def test_all_supported(self):
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=True, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        # 没有 thinking_level_map 时，xhigh 默认不支持
        assert get_supported_thinking_levels(model) == ["off", "minimal", "low", "medium", "high"]

    def test_with_thinking_level_map(self):
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={"minimal": None, "low": None, "medium": None, "high": "high", "xhigh": "max"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        levels = get_supported_thinking_levels(model)
        assert "minimal" not in levels
        assert "low" not in levels
        assert "medium" not in levels
        assert "high" in levels
        assert "xhigh" in levels

    def test_xhigh_default_not_supported(self):
        """xhigh 默认不支持，除非 thinking_level_map 中显式定义"""
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=True, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        assert "xhigh" not in get_supported_thinking_levels(model)


class TestIsContextOverflow:
    """上下文溢出检测测试"""

    def test_anthropic_overflow(self):
        msg = AssistantMessage(
            content=[TextContent(text="error")],
            stop_reason="error",
            error_message="prompt is too long: 213462 tokens > 200000 maximum",
        )
        assert is_context_overflow(msg) is True

    def test_openai_overflow(self):
        msg = AssistantMessage(
            content=[TextContent(text="error")],
            stop_reason="error",
            error_message="Your input exceeds the context window of this model",
        )
        assert is_context_overflow(msg) is True

    def test_not_overflow(self):
        msg = AssistantMessage(
            content=[TextContent(text="hello")],
            stop_reason="stop",
        )
        assert is_context_overflow(msg) is False

    def test_error_but_not_overflow(self):
        msg = AssistantMessage(
            content=[TextContent(text="error")],
            stop_reason="error",
            error_message="Invalid API key",
        )
        assert is_context_overflow(msg) is False

    def test_silent_overflow(self):
        """z.ai 风格的静默溢出（usage.input > context_window）"""
        msg = AssistantMessage(
            content=[TextContent(text="result")],
            stop_reason="stop",
            usage=Usage(input=200000),
        )
        assert is_context_overflow(msg, context_window=128000) is True

    def test_cerebras_400(self):
        msg = AssistantMessage(
            content=[TextContent(text="error")],
            stop_reason="error",
            error_message="400 status code (no body)",
        )
        assert is_context_overflow(msg) is True


class TestBuildBaseOptions:
    """基础选项构建测试"""

    def test_from_simple_options(self):
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=10000,
        )
        simple = SimpleStreamOptions(temperature=0.7, max_tokens=5000)
        opts = build_base_options(model, simple)

        assert opts.temperature == 0.7
        assert opts.max_tokens == 5000

    def test_default_max_tokens(self):
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=100000,
        )
        opts = build_base_options(model)
        # 默认上限 32000
        assert opts.max_tokens == 32000

    def test_api_key_override(self):
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        simple = SimpleStreamOptions(api_key="key1")
        opts = build_base_options(model, simple, api_key="key2")
        # 传入的 api_key 优先级更高
        assert opts.api_key == "key2"


class TestClampReasoning:
    """推理级别降级测试"""

    def test_xhigh_to_high(self):
        assert clamp_reasoning(ThinkingLevel.XHIGH) == ThinkingLevel.HIGH

    def test_high_unchanged(self):
        assert clamp_reasoning(ThinkingLevel.HIGH) == ThinkingLevel.HIGH

    def test_none_returns_none(self):
        assert clamp_reasoning(None) is None


class TestTransformMessages:
    """消息转换测试"""

    def test_user_message_unchanged(self):
        messages = [UserMessage(content="hello")]
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        result = transform_messages(messages, model)
        assert len(result) == 1
        assert result[0].role == "user"

    def test_thinking_block_same_model(self):
        """同一模型的 thinking 块保留"""
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=True, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        msg = AssistantMessage(
            role="assistant",
            content=[ThinkingContent(thinking="let me think")],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            model="test",
        )
        result = transform_messages([msg], model)
        assert result[0].content[0].type == "thinking"

    def test_thinking_block_cross_model(self):
        """跨模型的 thinking 块转换为 text"""
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=True, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        msg = AssistantMessage(
            role="assistant",
            content=[ThinkingContent(thinking="let me think")],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.ANTHROPIC,  # 不同 provider
            model="other-model",
        )
        result = transform_messages([msg], model)
        assert result[0].content[0].type == "text"
        assert result[0].content[0].text == "let me think"

    def test_skip_empty_thinking(self):
        """跳过空的 thinking 块"""
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=True, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        msg = AssistantMessage(
            role="assistant",
            content=[ThinkingContent(thinking="")],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.ANTHROPIC,
            model="other-model",
        )
        result = transform_messages([msg], model)
        assert len(result[0].content) == 0

    def test_error_message_skipped(self):
        """错误/中止的 assistant 消息被跳过"""
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        msg = AssistantMessage(
            role="assistant",
            content=[TextContent(text="partial")],
            stop_reason="error",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            model="test",
        )
        result = transform_messages([msg], model)
        assert len(result) == 0

    def test_orphan_tool_call(self):
        """孤立工具调用：当前实现保留 assistant 消息，不在末尾插入合成结果"""
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        msg = AssistantMessage(
            role="assistant",
            content=[ToolCall(id="tc1", name="search")],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            model="test",
        )
        result = transform_messages([msg], model)
        # 当前实现只在遇到后续消息时插入合成结果，末尾的 orphan tool call 保留原 assistant 消息
        assert len(result) == 1
        assert result[0].role == "assistant"
