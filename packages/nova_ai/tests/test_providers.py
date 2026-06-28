"""
Provider 测试
"""

from nova_ai.types import (
    Model, ModelCost, Context, UserMessage, TextContent,
    AssistantMessage, Tool, ToolResultMessage,
    KnownApi, KnownProvider, ThinkingFormat,
    OpenAICompletionsCompat, OpenRouterRouting, VercelGatewayRouting,
)
from nova_ai.api_impls.openai_completions import (
    build_params, convert_messages, detect_compat, get_compat,
    OpenAICompletionsOptions,
)


class TestDetectCompat:
    """兼容性自动检测测试"""

    def test_openai_default(self):
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENAI
        assert compat.supports_store is True
        assert compat.supports_usage_in_streaming is True

    def test_deepseek(self):
        model = Model(
            id="deepseek-v3", name="DeepSeek", api=KnownApi.OPENAI_COMPLETIONS,
            provider="deepseek", base_url="https://api.deepseek.com",
            reasoning=True, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.DEEPSEEK
        assert compat.requires_reasoning_content_on_assistant_messages is True

    def test_volcengine_deepseek(self):
        model = Model(
            id="deepseek-v3-2", name="DeepSeek", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE, base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.DEEPSEEK
        assert compat.requires_reasoning_content_on_assistant_messages is True

    def test_openrouter(self):
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENROUTER, base_url="https://openrouter.ai/api/v1",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENROUTER

    def test_zai(self):
        model = Model(
            id="zai-model", name="ZAI", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.ZAI, base_url="https://api.z.ai",
            reasoning=True, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.ZAI

    def test_grok_no_reasoning_effort(self):
        model = Model(
            id="grok-2", name="Grok", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.XAI, base_url="https://api.x.ai",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.supports_reasoning_effort is False


class TestGetCompat:
    """get_compat 合并逻辑测试"""

    def test_model_compat_overrides(self):
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
            compat=OpenAICompletionsCompat(supports_store=False),
        )
        compat = get_compat(model)
        assert compat.supports_store is False
        assert compat.supports_usage_in_streaming is True  # 自动检测的值

    def test_openai_responses_compat_ignored(self):
        """OpenAIResponsesCompat 不适用于 completions API"""
        from nova_ai.types import OpenAIResponsesCompat
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
            compat=OpenAIResponsesCompat(),
        )
        compat = get_compat(model)
        assert isinstance(compat, OpenAICompletionsCompat)


class TestBuildParams:
    """参数构建测试"""

    def test_basic_params(self):
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["model"] == "gpt-4"
        assert params["stream"] is True
        assert "messages" in params

    def test_temperature(self):
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(temperature=0.7)
        params = build_params(model, ctx, options)

        assert params["temperature"] == 0.7

    def test_max_tokens_field(self):
        """测试 max_tokens 字段选择"""
        model = Model(
            id="mistral-model", name="Mistral", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.MISTRAL, base_url="https://api.mistral.ai",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
            compat=OpenAICompletionsCompat(max_tokens_field="max_tokens"),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(max_tokens=100)
        params = build_params(model, ctx, options)

        assert "max_tokens" in params
        assert params["max_tokens"] == 100
        assert "max_completion_tokens" not in params

    def test_deepseek_thinking_disabled(self):
        """DeepSeek 未启用 thinking"""
        model = Model(
            id="deepseek-v3", name="DeepSeek", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE, base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["extra_body"]["thinking"]["type"] == "disabled"

    def test_deepseek_thinking_enabled(self):
        """DeepSeek 启用 thinking"""
        model = Model(
            id="deepseek-v3", name="DeepSeek", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE, base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="low")
        params = build_params(model, ctx, options)

        assert params["extra_body"]["thinking"]["type"] == "enabled"
        assert params["reasoning_effort"] == "low"

    def test_deepseek_thinking_level_map(self):
        """DeepSeek thinking_level_map 映射"""
        model = Model(
            id="deepseek-v3", name="DeepSeek", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE, base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True,
            thinking_level_map={"low": "medium", "high": "high"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="low")
        params = build_params(model, ctx, options)

        assert params["reasoning_effort"] == "medium"  # 被映射

    def test_tools(self):
        """工具参数构建"""
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        ctx = Context(
            messages=[UserMessage(content="hello")],
            tools=[Tool(name="search", description="search web", parameters={})],
        )
        params = build_params(model, ctx)

        assert "tools" in params
        assert len(params["tools"]) == 1
        assert params["tools"][0]["function"]["name"] == "search"

    def test_openrouter_routing(self):
        """OpenRouter 路由配置"""
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENROUTER, base_url="https://openrouter.ai/api/v1",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
            compat=OpenAICompletionsCompat(
                open_router_routing=OpenRouterRouting(order=["anthropic", "openai"]),
            ),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["extra_body"]["provider"]["order"] == ["anthropic", "openai"]

    def test_vercel_gateway_routing(self):
        """Vercel Gateway 路由配置"""
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VERCEL_AI_GATEWAY, base_url="https://ai-gateway.vercel.sh",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
            compat=OpenAICompletionsCompat(
                vercel_gateway_routing=VercelGatewayRouting(only=["bedrock"]),
            ),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["extra_body"]["providerOptions"]["gateway"]["only"] == ["bedrock"]

    def test_no_extra_body_when_empty(self):
        """没有 extra_body 内容时不应包含 extra_body 字段"""
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        # 对于 OpenAI 官方，没有 extra_body 内容
        assert "extra_body" not in params or params.get("extra_body") == {}

    def test_stream_options(self):
        """stream_options 默认包含 usage"""
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["stream_options"] == {"include_usage": True}

    def test_store_false(self):
        """store 默认 False"""
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["store"] is False


class TestConvertMessages:
    """消息转换测试"""

    def test_user_message_string(self):
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = get_compat(model)
        ctx = Context(messages=[UserMessage(content="hello")])
        messages = convert_messages(model, ctx, compat)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello"

    def test_user_message_list(self):
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = get_compat(model)
        ctx = Context(messages=[UserMessage(content=[TextContent(text="hello")])])
        messages = convert_messages(model, ctx, compat)

        assert messages[0]["role"] == "user"
        assert messages[0]["content"][0]["type"] == "text"

    def test_system_prompt(self):
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = get_compat(model)
        ctx = Context(
            system_prompt="You are helpful",
            messages=[UserMessage(content="hello")],
        )
        messages = convert_messages(model, ctx, compat)

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful"

    def test_assistant_message(self):
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = get_compat(model)
        ctx = Context(
            messages=[
                UserMessage(content="hello"),
                AssistantMessage(content=[TextContent(text="hi")]),
            ],
        )
        messages = convert_messages(model, ctx, compat)

        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "hi"

    def test_tool_result_message(self):
        model = Model(
            id="gpt-4", name="GPT-4", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        compat = get_compat(model)
        ctx = Context(
            messages=[
                ToolResultMessage(
                    tool_call_id="tc1",
                    tool_name="search",
                    content=[TextContent(text="result")],
                ),
            ],
        )
        messages = convert_messages(model, ctx, compat)

        assert messages[0]["role"] == "tool"
        assert messages[0]["tool_call_id"] == "tc1"
