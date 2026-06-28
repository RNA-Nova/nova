"""
兼容性层测试
"""
from nova_ai.types import (
    OpenAICompletionsCompat, OpenAIResponsesCompat,
    OpenRouterRouting, VercelGatewayRouting,
    ThinkingFormat,
)
class TestOpenAICompletionsCompat:
    """OpenAI Completions 兼容性配置测试"""

    def test_default_values(self):
        compat = OpenAICompletionsCompat()
        assert compat.supports_store is None
        assert compat.supports_usage_in_streaming is None
        assert compat.supports_reasoning_effort is None
        assert compat.max_tokens_field is None
        assert compat.open_router_routing is None
        assert compat.vercel_gateway_routing is None

    def test_custom_values(self):
        compat = OpenAICompletionsCompat(
            supports_store=False,
            supports_reasoning_effort=False,
            max_tokens_field="max_tokens",
        )
        assert compat.supports_store is False
        assert compat.supports_reasoning_effort is False
        assert compat.max_tokens_field == "max_tokens"

    def test_model_dump(self):
        compat = OpenAICompletionsCompat(
            supports_store=False,
            open_router_routing=OpenRouterRouting(order=["anthropic"]),
        )
        data = compat.model_dump()
        assert data["supports_store"] is False
        assert data["open_router_routing"]["order"] == ["anthropic"]

    def test_thinking_format_enum(self):
        compat = OpenAICompletionsCompat(thinking_format=ThinkingFormat.DEEPSEEK)
        assert compat.thinking_format == ThinkingFormat.DEEPSEEK

    def test_supports_reasoning_effort_default(self):
        compat = OpenAICompletionsCompat()
        assert compat.supports_reasoning_effort is None

    def test_supports_strict_mode_default(self):
        compat = OpenAICompletionsCompat()
        assert compat.supports_strict_mode is None
class TestOpenAIResponsesCompat:
    """OpenAI Responses 兼容性配置测试"""

    def test_default_values(self):
        compat = OpenAIResponsesCompat()
        assert compat is not None

    def test_model_dump(self):
        compat = OpenAIResponsesCompat()
        data = compat.model_dump()
        assert isinstance(data, dict)
class TestOpenRouterRouting:
    """OpenRouter 路由配置测试"""

    def test_basic(self):
        routing = OpenRouterRouting(order=["anthropic", "openai"])
        assert routing.order == ["anthropic", "openai"]

    def test_only(self):
        routing = OpenRouterRouting(only=["anthropic"])
        assert routing.only == ["anthropic"]

    def test_model_dump(self):
        routing = OpenRouterRouting(order=["anthropic"])
        data = routing.model_dump()
        assert data["order"] == ["anthropic"]
class TestVercelGatewayRouting:
    """Vercel Gateway 路由配置测试"""

    def test_basic(self):
        routing = VercelGatewayRouting(only=["bedrock"])
        assert routing.only == ["bedrock"]

    def test_order(self):
        routing = VercelGatewayRouting(order=["bedrock", "openai"])
        assert routing.order == ["bedrock", "openai"]

    def test_model_dump(self):
        routing = VercelGatewayRouting(only=["bedrock"])
        data = routing.model_dump()
        assert data["only"] == ["bedrock"]
class TestThinkingFormat:
    """ThinkingFormat 枚举测试"""

    def test_values(self):
        assert ThinkingFormat.OPENAI == "openai"
        assert ThinkingFormat.DEEPSEEK == "deepseek"
        assert ThinkingFormat.OPENROUTER == "openrouter"
        assert ThinkingFormat.TOGETHER == "together"
        assert ThinkingFormat.ZAI == "zai"
        assert ThinkingFormat.QWEN == "qwen"
