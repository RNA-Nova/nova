"""OpenAI Completions 的请求参数构建与方言分派测试。"""

import pytest
from nova_ai.api_impls.openai_completions import OpenAICompletionsOptions, build_params
from nova_protocol import (
    AssistantMessage,
    ConstrainedSampling,
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
    UserMessage,
    VercelGatewayRouting,
)


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

    def test_vllm_priority_via_extra_body(self):
        """vllm_priority（对齐 pi vllmPriority）：顶层 priority 经 extra_body
        上线；缺省不发。"""
        from nova_protocol import OpenAICompletionsCompat

        model = Model(
            id="vllm-model",
            name="m",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="custom",
            base_url="https://vllm.example.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(vllm_priority=-1),
        )
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert (params.get("extra_body") or {}).get("priority") == -1
        # 缺省（未声明旗标）不发
        model_default = model.model_copy(update={"compat": None})
        params_default = build_params(model_default, ctx)
        assert "priority" not in (params_default.get("extra_body") or {})

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
            compat=OpenAICompletionsCompat(
                thinking_format=ThinkingFormat.STRING_THINKING
            ),
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
            compat=OpenAICompletionsCompat(
                thinking_format=ThinkingFormat.STRING_THINKING
            ),
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
            compat=OpenAICompletionsCompat(
                thinking_format=ThinkingFormat.STRING_THINKING
            ),
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


class TestOpenAIStrictTools:
    def _tool(self, strict: str) -> Tool:
        return Tool(
            name="write",
            description="write",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            constrained_sampling=ConstrainedSampling.model_validate(
                {"type": "json_schema", "strict": strict}
            ),
        )

    def test_prefer_enables_and_transforms_strict_schema(self):
        params = build_params(
            Model(
                id="gpt",
                name="gpt",
                api=KnownApi.OPENAI_COMPLETIONS,
                provider=KnownProvider.OPENAI,
                base_url="https://api.openai.com/v1",
                reasoning=False,
                input_types=["text"],
                cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
                context_window=128000,
                max_tokens=4096,
                compat=OpenAICompletionsCompat(supports_strict_mode=True),
            ),
            Context(messages=[UserMessage(content="hi")], tools=[self._tool("prefer")]),
            OpenAICompletionsOptions(),
        )
        function = params["tools"][0]["function"]
        assert function["strict"] is True
        assert function["parameters"]["required"] == ["path"]
        assert function["parameters"]["additionalProperties"] is False

    def test_require_rejects_unsupported_endpoint(self):
        with pytest.raises(ValueError, match="strict tools are unsupported"):
            build_params(
                Model(
                    id="k",
                    name="k",
                    api=KnownApi.OPENAI_COMPLETIONS,
                    provider="kimi-coding",
                    base_url="https://api.kimi.com/coding/v1",
                    reasoning=False,
                    input_types=["text"],
                    cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
                    context_window=128000,
                    max_tokens=4096,
                    compat=OpenAICompletionsCompat(supports_strict_mode=False),
                ),
                Context(
                    messages=[UserMessage(content="hi")], tools=[self._tool("require")]
                ),
                OpenAICompletionsOptions(),
            )


def _request_model(**updates) -> Model:
    fields = {
        "id": "gpt",
        "name": "gpt",
        "api": KnownApi.OPENAI_COMPLETIONS,
        "provider": KnownProvider.OPENAI,
        "base_url": "https://api.openai.com/v1",
        "reasoning": True,
        "input_types": ["text"],
        "cost": ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        "context_window": 128000,
        "max_tokens": 4096,
    }
    fields.update(updates)
    return Model(**fields)


def test_direct_params_merge_sampling_for_effective_thinking_level():
    params = build_params(
        _request_model(
            sampling_params={"temperature": 0.2, "top_k": 1},
            sampling_params_by_thinking_level={
                "high": {"temperature": 0.1, "top_k": 8}
            },
        ),
        Context(messages=[UserMessage(content="hi")]),
        OpenAICompletionsOptions(reasoning_effort="high", sampling_params={"top_k": 4}),
    )
    assert params["temperature"] == 0.1
    assert params["top_k"] == 4


def test_tool_choice_none_survives_without_tools():
    params = build_params(
        _request_model(reasoning=False),
        Context(messages=[UserMessage(content="hi")]),
        OpenAICompletionsOptions(tool_choice="none"),
    )
    assert params["tool_choice"] == "none"


@pytest.mark.parametrize(
    "schema_update",
    [
        {"anyOf": None},
        {"items": None},
        {"properties": None},
        {"required": None},
        {"additionalProperties": None},
    ],
)
def test_explicit_null_structural_schema_keys_cannot_enable_strict(schema_update):
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        **schema_update,
    }
    tool = Tool(
        name="write",
        description="write",
        parameters=parameters,
        constrained_sampling=ConstrainedSampling(type="json_schema", strict="require"),
    )
    with pytest.raises(ValueError, match="requires JSON-schema"):
        build_params(
            _request_model(
                reasoning=False,
                compat=OpenAICompletionsCompat(supports_strict_mode=True),
            ),
            Context(messages=[UserMessage(content="hi")], tools=[tool]),
            OpenAICompletionsOptions(),
        )


# ---------------------------------------------------------------------------
# thinking budget / chat template args / generic options
# ---------------------------------------------------------------------------


class TestThinkingBudget:
    def test_budget_table_and_custom_override(self):
        from nova_ai.api_impls._shared.simple_options import (
            clamp_thinking_budget_to_answer_room,
            thinking_budget_for_level,
        )
        from nova_ai.stream_options import ThinkingBudgets

        from .conftest import _ctx, _model

        assert thinking_budget_for_level("low") == 2048
        assert thinking_budget_for_level("xhigh") == 16384
        custom = ThinkingBudgets(low=100)
        assert thinking_budget_for_level("low", custom) == 100

    def test_answer_room_clamp(self):
        from nova_ai.api_impls._shared.simple_options import (
            clamp_thinking_budget_to_answer_room,
        )

        assert clamp_thinking_budget_to_answer_room(16384, 4096) == 3072
        assert clamp_thinking_budget_to_answer_room(100, 4096) == 100

    def test_budget_field_applied_for_vllm(self):
        """supports_thinking_token_budget 端点：顶层预算字段上线（经 extra_body）。"""
        from nova_ai.api_impls.openai_completions import OpenAICompletionsOptions

        from .conftest import _ctx, _model

        model = _model(
            reasoning=True,
            max_tokens=65536,
            compat=OpenAICompletionsCompat(
                supports_thinking_token_budget=True,
                thinking_format=ThinkingFormat.CHAT_TEMPLATE,
            ),
        )
        ctx = _ctx(UserMessage(content="hi"))
        params = build_params(
            model, ctx, OpenAICompletionsOptions(reasoning_effort="high")
        )
        assert params["extra_body"]["thinking_token_budget"] == 16384

    @pytest.mark.asyncio
    async def test_stream_simple_passes_thinking_budgets(self):
        """streamSimple 不再丢弃 thinking_budgets（旧移植的字段丢失 bug）。"""
        from nova_ai.api_impls.openai_completions import _stream as stream_module
        from nova_ai.stream_options import SimpleStreamOptions, ThinkingBudgets

        from .conftest import _ctx, _model

        captured = {}

        def _fake_stream(m, c, opts):
            captured["opts"] = opts

            class _Fake:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

                async def result(self):
                    return AssistantMessage(content=[])

            return _Fake()

        original = stream_module.stream
        stream_module.stream = _fake_stream
        try:
            from nova_ai.api_impls.openai_completions import stream_simple

            budgets = ThinkingBudgets(low=999)
            events = stream_simple(
                _model(reasoning=True),
                _ctx(UserMessage(content="hi")),
                SimpleStreamOptions(
                    api_key="sk-test",
                    reasoning=None,
                    thinking_budgets=budgets,
                ),
            )
            await events.result()
            assert captured["opts"].thinking_budgets == budgets
        finally:
            stream_module.stream = original


def test_baseten_chat_template_args_with_budget_var():
    """baseten 格式：chat_template_args 支持 $var: thinking.budget 变量替换。"""
    from nova_ai.api_impls.openai_completions import OpenAICompletionsOptions

    from .conftest import _ctx, _model

    model = _model(
        reasoning=True,
        max_tokens=65536,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.BASETEN,
            supports_thinking_token_budget=True,
            chat_template_args={
                "enable_thinking": {"$var": "thinking.enabled"},
                "budget": {"$var": "thinking.budget"},
            },
        ),
    )
    ctx = _ctx(UserMessage(content="hi"))
    params = build_params(
        model, ctx, OpenAICompletionsOptions(reasoning_effort="medium")
    )
    args = params["extra_body"]["chat_template_args"]
    assert args["enable_thinking"] is True
    assert args["budget"] == 8192


def test_use_max_tokens_for_deepseek_and_zai():
    """deepseek / zai 端点用 max_tokens 而非 max_completion_tokens。"""
    from nova_ai.api_impls.openai_completions import OpenAICompletionsOptions

    from .conftest import _ctx, _model

    deepseek = _model(provider="deepseek", base_url="https://api.deepseek.com/v1")
    params = build_params(
        deepseek,
        _ctx(UserMessage(content="hi")),
        OpenAICompletionsOptions(max_tokens=100),
    )
    assert params["max_tokens"] == 100

    zai = _model(provider="zai", base_url="https://api.z.ai/v1")
    params = build_params(
        zai, _ctx(UserMessage(content="hi")), OpenAICompletionsOptions(max_tokens=100)
    )
    assert params["max_tokens"] == 100


def test_build_params_with_generic_base_options():
    """通用基类选项进 build_params 不炸。"""
    from nova_ai.stream_options import SimpleStreamOptions, StreamOptions

    from .conftest import _ctx, _model

    model = _model(reasoning=True)
    ctx = _ctx(UserMessage(content="hi"))

    params = build_params(model, ctx, StreamOptions(api_key="sk-test"))
    assert params["model"] == "test-model"

    params = build_params(
        model,
        ctx,
        SimpleStreamOptions(api_key="sk-test", max_tokens=512),
    )
    assert params["max_completion_tokens"] == 512
    params = build_params(model, ctx, None)
    assert params["model"] == "test-model"


def test_tool_choice_is_explicit_even_without_tools():
    """显式 tool_choice 原样透传，不由本地工具列表反推。"""
    from nova_ai.api_impls.openai_completions import OpenAICompletionsOptions

    from .conftest import _ctx, _model

    model = _model()
    ctx = _ctx(UserMessage(content="hi"))
    params = build_params(model, ctx, OpenAICompletionsOptions(tool_choice="auto"))
    assert params["tool_choice"] == "auto"
