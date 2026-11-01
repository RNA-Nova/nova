"""兼容配置词汇（compat.py）测试——构造/dump 行为 + Model.compat 判别。"""

from nova_protocol import (
    AnthropicMessagesCompat,
    Model,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    OpenRouterRouting,
    ThinkingFormat,
    VercelGatewayRouting,
)


class TestOpenAICompletionsCompat:
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

    def test_open_router_routing_allows_extra(self):
        """OpenRouterRouting 允许额外字段（线上网关加键不炸）。"""
        routing = OpenRouterRouting.model_validate(
            {"allow_fallbacks": False, "order": ["anthropic"], "future_field": 1}
        )
        assert routing.model_dump()["order"] == ["anthropic"]


class TestOpenAIResponsesCompat:
    def test_default_values(self):
        compat = OpenAIResponsesCompat()
        assert compat is not None

    def test_send_session_id_header(self):
        compat = OpenAIResponsesCompat(send_session_id_header=True)
        assert compat.send_session_id_header is True

    def test_model_dump(self):
        assert isinstance(OpenAIResponsesCompat().model_dump(), dict)


class TestOpenRouterRouting:
    def test_basic(self):
        routing = OpenRouterRouting(order=["anthropic", "openai"])
        assert routing.order == ["anthropic", "openai"]

    def test_only(self):
        routing = OpenRouterRouting(only=["anthropic"])
        assert routing.only == ["anthropic"]


class TestVercelGatewayRouting:
    def test_basic(self):
        routing = VercelGatewayRouting(only=["bedrock"])
        assert routing.only == ["bedrock"]

    def test_order(self):
        routing = VercelGatewayRouting(order=["bedrock", "openai"])
        assert routing.order == ["bedrock", "openai"]


def _make_model(api: str, compat: dict) -> Model:
    return Model.model_validate(
        {
            "id": "m1",
            "name": "m1",
            "api": api,
            "provider": "openai",
            "base_url": "http://x",
            "reasoning": True,
            "input_types": ["text"],
            "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
            "context_window": 8192,
            "max_tokens": 4096,
            "compat": compat,
        }
    )


class TestModelCompatResolution:
    """Model.compat union 按 api 显式判别，不依赖 smart-union 猜测。"""

    def test_responses_only_field_resolves_responses_compat(self):
        """responses 独有字段（supports_tool_search）应判别为 OpenAIResponsesCompat。"""
        model = _make_model("openai-responses", {"supports_tool_search": True})
        assert isinstance(model.compat, OpenAIResponsesCompat)
        assert model.compat.supports_tool_search is True

    def test_shared_field_resolves_by_api(self):
        """同名字段 supports_developer_role 两类都有：api=responses 时必须判别为 responses。"""
        model = _make_model("openai-responses", {"supports_developer_role": False})
        assert isinstance(model.compat, OpenAIResponsesCompat)
        assert model.compat.supports_developer_role is False

    def test_completions_field_resolves_completions_compat(self):
        model = _make_model("openai-completions", {"supports_store": False})
        assert isinstance(model.compat, OpenAICompletionsCompat)
        assert model.compat.supports_store is False

    def test_anthropic_field_resolves_anthropic_compat(self):
        model = _make_model("anthropic-messages", {"supports_temperature": False})
        assert isinstance(model.compat, AnthropicMessagesCompat)
        assert model.compat.supports_temperature is False
