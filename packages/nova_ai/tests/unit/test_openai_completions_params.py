"""
OpenAI Completions 协议参数构建测试（detect_compat / get_compat / build_params / convert_messages）
"""

from nova_ai.api_impls.openai_completions import (
    OpenAICompletionsOptions,
    build_params,
    convert_messages,
    detect_compat,
    get_compat,
)
from nova_ai.types import (
    AssistantMessage,
    Context,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    OpenRouterRouting,
    TextContent,
    ThinkingContent,
    ThinkingFormat,
    Tool,
    ToolResultMessage,
    UserMessage,
    VercelGatewayRouting,
)


class TestDetectCompat:
    """兼容性自动检测测试"""

    def test_openai_default(self):
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENAI
        assert compat.supports_store is True
        assert compat.supports_usage_in_streaming is True

    def test_deepseek(self):
        model = Model(
            id="deepseek-v3",
            name="DeepSeek",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="deepseek",
            base_url="https://api.deepseek.com",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.DEEPSEEK
        assert compat.requires_reasoning_content_on_assistant_messages is True

    def test_volcengine_deepseek(self):
        model = Model(
            id="deepseek-v3-2",
            name="DeepSeek",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE,
            base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.DEEPSEEK
        assert compat.requires_reasoning_content_on_assistant_messages is True

    def test_openrouter(self):
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENROUTER,
            base_url="https://openrouter.ai/api/v1",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENROUTER

    def test_zai(self):
        model = Model(
            id="zai-model",
            name="ZAI",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.ZAI,
            base_url="https://api.z.ai",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.ZAI

    def test_nvidia_detected(self):
        """NVIDIA：按非标准处理，不支持 reasoning_effort（对齐 TS）"""
        model = Model(
            id="nvidia-model",
            name="NVIDIA",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="nvidia",
            base_url="https://integrate.api.nvidia.com/v1",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENAI
        assert compat.supports_reasoning_effort is False
        assert compat.supports_store is False
        assert compat.max_tokens_field == "max_tokens"
        assert compat.supports_strict_mode is False
        assert compat.supports_long_cache_retention is False

    def test_openrouter_developer_role_only_for_prefixed_models(self):
        """OpenRouter：仅 anthropic//openai 前缀模型使用 developer role（对齐 TS）"""

        def _compat(model_id: str):
            return detect_compat(
                Model(
                    id=model_id,
                    name=model_id,
                    api=KnownApi.OPENAI_COMPLETIONS,
                    provider=KnownProvider.OPENROUTER,
                    base_url="https://openrouter.ai/api/v1",
                    reasoning=True,
                    input_types=["text"],
                    cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
                    context_window=128000,
                    max_tokens=4096,
                )
            )

        assert _compat("anthropic/claude-opus").supports_developer_role is True
        assert _compat("openai/gpt-5").supports_developer_role is True
        assert _compat("meta-llama/llama-4").supports_developer_role is False

    def test_mistral_uses_standard_defaults(self):
        """Mistral：不再做 provider 特判，按标准 OpenAI 检测（对齐 TS）"""
        model = Model(
            id="mistral-model",
            name="Mistral",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.MISTRAL,
            base_url="https://api.mistral.ai",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.requires_thinking_as_text is False
        assert compat.requires_tool_result_name is False
        assert compat.max_tokens_field == "max_completion_tokens"
        assert compat.supports_store is True
        assert compat.supports_developer_role is True

    def test_grok_no_reasoning_effort(self):
        model = Model(
            id="grok-2",
            name="Grok",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.XAI,
            base_url="https://api.x.ai",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.supports_reasoning_effort is False


class TestGetCompat:
    """get_compat 合并逻辑测试"""

    def test_model_compat_overrides(self):
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
            compat=OpenAICompletionsCompat(supports_store=False),
        )
        compat = get_compat(model)
        assert compat.supports_store is False
        assert compat.supports_usage_in_streaming is True  # 自动检测的值

    def test_openai_responses_compat_ignored(self):
        """OpenAIResponsesCompat 不适用于 completions API"""
        from nova_ai.types import OpenAIResponsesCompat

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
            compat=OpenAIResponsesCompat(),
        )
        compat = get_compat(model)
        assert isinstance(compat, OpenAICompletionsCompat)


class TestBuildParams:
    """参数构建测试"""

    def test_basic_params(self):
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["model"] == "gpt-4"
        assert params["stream"] is True
        assert "messages" in params

    def test_temperature(self):
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(temperature=0.7)
        params = build_params(model, ctx, options)

        assert params["temperature"] == 0.7

    def test_max_tokens_field(self):
        """测试 max_tokens 字段选择"""
        model = Model(
            id="mistral-model",
            name="Mistral",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.MISTRAL,
            base_url="https://api.mistral.ai",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
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
            id="deepseek-v3",
            name="DeepSeek",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE,
            base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["extra_body"]["thinking"]["type"] == "disabled"

    def test_deepseek_thinking_enabled(self):
        """DeepSeek 启用 thinking"""
        model = Model(
            id="deepseek-v3",
            name="DeepSeek",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE,
            base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="low")
        params = build_params(model, ctx, options)

        assert params["extra_body"]["thinking"]["type"] == "enabled"
        assert params["reasoning_effort"] == "low"

    def test_deepseek_thinking_level_map(self):
        """DeepSeek thinking_level_map 映射"""
        model = Model(
            id="deepseek-v3",
            name="DeepSeek",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE,
            base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True,
            thinking_level_map={"low": "medium", "high": "high"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="low")
        params = build_params(model, ctx, options)

        assert params["reasoning_effort"] == "medium"  # 被映射

    def test_deepseek_off_explicitly_null_skips_thinking(self):
        """DeepSeek：map.off 显式为 None（模型关不掉思考）时不发 thinking 参数"""
        model = Model(
            id="deepseek-v3",
            name="DeepSeek",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE,
            base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True,
            thinking_level_map={"off": None, "high": "high"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert "thinking" not in params.get("extra_body", {})

    def test_zai_thinking_enabled(self):
        """ZAI 使用 thinking: {type, clear_thinking} 参数（对齐 TS）"""
        model = Model(
            id="zai-model",
            name="ZAI",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.ZAI,
            base_url="https://api.z.ai",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="high")
        params = build_params(model, ctx, options)

        assert params["extra_body"]["thinking"] == {
            "type": "enabled",
            "clear_thinking": False,
        }
        assert "enable_thinking" not in params["extra_body"]

    def test_zai_thinking_disabled(self):
        """ZAI 未启用 thinking"""
        model = Model(
            id="zai-model",
            name="ZAI",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.ZAI,
            base_url="https://api.z.ai",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["extra_body"]["thinking"] == {"type": "disabled"}

    def test_zai_reasoning_effort_strict_null_skip(self):
        """zai：级别被显式映射为 None 时不发送 reasoning_effort"""
        model = Model(
            id="zai-model",
            name="ZAI",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.ZAI,
            base_url="https://api.z.ai",
            reasoning=True,
            thinking_level_map={"high": None, "max": "max"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(
                thinking_format=ThinkingFormat.ZAI,
                supports_reasoning_effort=True,
            ),
        )
        ctx = Context(messages=[UserMessage(content="hello")])

        high_params = build_params(
            model, ctx, OpenAICompletionsOptions(reasoning_effort="high")
        )
        assert "reasoning_effort" not in high_params

        max_params = build_params(
            model, ctx, OpenAICompletionsOptions(reasoning_effort="max")
        )
        assert max_params["reasoning_effort"] == "max"

    def test_reasoning_effort_off_not_sent_default_format(self):
        """默认格式下 reasoning_effort="off" 不应泄漏到请求参数"""
        model = Model(
            id="gpt-5",
            name="GPT-5",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(supports_reasoning_effort=True),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="off")
        params = build_params(model, ctx, options)

        assert "reasoning_effort" not in params

    def test_reasoning_effort_sent_when_enabled_default_format(self):
        """默认格式下启用推理时发送 reasoning_effort"""
        model = Model(
            id="gpt-5",
            name="GPT-5",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(supports_reasoning_effort=True),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="high")
        params = build_params(model, ctx, options)

        assert params["reasoning_effort"] == "high"

    def test_off_explicitly_sent_when_map_declares_off_string(self):
        """默认格式：map 声明 off→"none" 时，OFF 也要显式告知服务端（对齐 TS）"""
        model = Model(
            id="gpt-5",
            name="GPT-5",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={"off": "none", "high": "high"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(supports_reasoning_effort=True),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["reasoning_effort"] == "none"

    def test_off_not_sent_when_map_off_explicitly_null(self):
        """默认格式：map.off 显式为 None（模型关不掉思考）时不发送"""
        model = Model(
            id="gpt-5",
            name="GPT-5",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={"off": None, "high": "high"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(supports_reasoning_effort=True),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert "reasoning_effort" not in params

    def test_ant_ling_detected_from_url(self):
        """ant-ling 经 URL 检测，且不支持 reasoning_effort"""
        model = Model(
            id="ant-model",
            name="AntLing",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.ant-ling.com/v1",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)

        assert compat.thinking_format == "ant-ling"
        assert compat.supports_reasoning_effort is False

    def test_ant_ling_only_sends_explicitly_mapped_levels(self):
        """ant-ling：仅当级别有显式字符串映射时发送 reasoning.effort"""
        model = Model(
            id="ant-model",
            name="AntLing",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.ant-ling.com/v1",
            reasoning=True,
            thinking_level_map={"high": "max"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])

        mapped = build_params(
            model, ctx, OpenAICompletionsOptions(reasoning_effort="high")
        )
        assert mapped["extra_body"]["reasoning"] == {"effort": "max"}

        unmapped = build_params(
            model, ctx, OpenAICompletionsOptions(reasoning_effort="low")
        )
        assert "reasoning" not in unmapped.get("extra_body", {})

        off = build_params(model, ctx)
        assert "reasoning" not in off.get("extra_body", {})

    def test_chat_template_thinking_enabled_and_effort(self):
        """chat-template：变量被替换为当前启用状态与映射后级别"""
        model = Model(
            id="ct-model",
            name="ChatTemplate",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={"high": "max"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(
                thinking_format=ThinkingFormat.CHAT_TEMPLATE,
                chat_template_kwargs={
                    "enabled": {"$var": "thinking.enabled"},
                    "effort": {"$var": "thinking.effort"},
                    "literal": True,
                },
            ),
        )
        ctx = Context(messages=[UserMessage(content="hello")])

        on = build_params(model, ctx, OpenAICompletionsOptions(reasoning_effort="high"))
        assert on["extra_body"]["chat_template_kwargs"] == {
            "enabled": True,
            "effort": "max",
            "literal": True,
        }

    def test_chat_template_omit_when_off(self):
        """chat-template：omitWhenOff 为真且未启用思考时省略该键"""
        model = Model(
            id="ct-model",
            name="ChatTemplate",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={"off": "disabled"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(
                thinking_format=ThinkingFormat.CHAT_TEMPLATE,
                chat_template_kwargs={
                    "enabled": {"$var": "thinking.enabled"},
                    "effort": {
                        "$var": "thinking.effort",
                        "omitWhenOff": True,
                    },
                    "fixed": {"$var": "thinking.effort", "omitWhenOff": False},
                },
            ),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        kwargs = params["extra_body"]["chat_template_kwargs"]
        assert kwargs["enabled"] is False
        assert "effort" not in kwargs
        assert kwargs["fixed"] == "disabled"

    def test_string_thinking_enabled_uses_mapped_string(self):
        """string-thinking：启用时发送顶层 thinking 字符串（映射后）"""
        model = Model(
            id="st-model",
            name="StringThinking",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={"high": "max"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(thinking_format="string-thinking"),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(
            model, ctx, OpenAICompletionsOptions(reasoning_effort="high")
        )

        assert params["extra_body"]["thinking"] == "max"

    def test_string_thinking_off_defaults_to_none_string(self):
        """string-thinking：OFF 且未声明 off 映射时发送字符串 none"""
        model = Model(
            id="st-model",
            name="StringThinking",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(thinking_format="string-thinking"),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["extra_body"]["thinking"] == "none"

    def test_string_thinking_off_explicitly_null_not_sent(self):
        """string-thinking：map.off 显式为 None（模型关不掉思考）时不发送"""
        model = Model(
            id="st-model",
            name="StringThinking",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={"off": None},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(thinking_format="string-thinking"),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert "thinking" not in params.get("extra_body", {})

    def test_opencode_go_reasoning_signature_remapped_on_replay(self):
        """opencode-go：reasoning 签名回放更名为 reasoning_content（对齐 TS）"""
        model = Model(
            id="gpt-oss-120b",
            name="GPT OSS",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="opencode-go",
            base_url="https://opencode.ai/zen/v1",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(
            messages=[
                UserMessage(content="hi"),
                AssistantMessage(
                    content=[
                        ThinkingContent(
                            thinking="chain", thinking_signature="reasoning"
                        ),
                        TextContent(text="answer"),
                    ],
                    api=KnownApi.OPENAI_COMPLETIONS,
                    provider="opencode-go",
                    model="gpt-oss-120b",
                ),
            ]
        )
        params = build_params(model, ctx)
        assistant = [m for m in params["messages"] if m["role"] == "assistant"][0]

        assert assistant["reasoning_content"] == "chain"
        assert "reasoning" not in assistant

    def test_tools(self):
        """工具参数构建"""
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
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
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENROUTER,
            base_url="https://openrouter.ai/api/v1",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
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
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VERCEL_AI_GATEWAY,
            base_url="https://ai-gateway.vercel.sh",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
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
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        # 对于 OpenAI 官方，没有 extra_body 内容
        assert "extra_body" not in params

    def test_stream_options(self):
        """stream_options 默认包含 usage"""
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["stream_options"] == {"include_usage": True}

    def test_store_false(self):
        """store 默认 False"""
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["store"] is False


class TestConvertMessages:
    """消息转换测试"""

    def test_user_message_string(self):
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = get_compat(model)
        ctx = Context(messages=[UserMessage(content="hello")])
        messages = convert_messages(model, ctx, compat)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello"

    def test_user_message_list(self):
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = get_compat(model)
        ctx = Context(messages=[UserMessage(content=[TextContent(text="hello")])])
        messages = convert_messages(model, ctx, compat)

        assert messages[0]["role"] == "user"
        assert messages[0]["content"][0]["type"] == "text"

    def test_system_prompt(self):
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
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
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
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
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
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
