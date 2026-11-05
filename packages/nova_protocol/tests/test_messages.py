"""AssistantMessage.error_kind 字段（messages.py）测试——roundtrip 与旧数据兼容。"""

from nova_protocol import AssistantMessage, NovaErrorKind, StopReason


class TestErrorKind:
    def test_absent_defaults_none(self):
        """旧数据（无 error_kind 键）加载归零——可选字段 minor 演化。"""
        message = AssistantMessage.model_validate(
            {"stop_reason": "error", "error_message": "boom"}
        )
        assert message.error_kind is None

    def test_roundtrip(self):
        message = AssistantMessage(
            stop_reason=StopReason.ERROR,
            error_message="429 too many requests",
            error_kind=NovaErrorKind.RATE_LIMITED,
        )
        restored = AssistantMessage.model_validate(message.model_dump())
        assert restored == message
        assert restored.error_kind is NovaErrorKind.RATE_LIMITED

    def test_dump_wire_camel_alias(self):
        """线上 camelCase 键名（errorKind）。"""
        message = AssistantMessage(
            stop_reason=StopReason.ERROR, error_kind=NovaErrorKind.CONTEXT_OVERFLOW
        )
        dumped = message.dump_wire()
        assert dumped["errorKind"] == "contextOverflow"

    def test_dump_wire_absent_omitted(self):
        """无错误时线上不带 errorKind 键（absent 语义）。"""
        dumped = AssistantMessage().dump_wire(exclude_none=True)
        assert "errorKind" not in dumped


class TestRetryAfterMs:
    """AssistantMessage.retry_after_ms（messages.py）——429 退避指引的载体。"""

    def test_absent_defaults_none(self):
        """旧数据（无 retry_after_ms 键）加载归零——可选字段 minor 演化。"""
        message = AssistantMessage.model_validate(
            {"stop_reason": "error", "error_message": "boom"}
        )
        assert message.retry_after_ms is None

    def test_roundtrip(self):
        message = AssistantMessage(
            stop_reason=StopReason.ERROR,
            error_message="429",
            error_kind=NovaErrorKind.RATE_LIMITED,
            retry_after_ms=60000,
        )
        restored = AssistantMessage.model_validate(message.model_dump())
        assert restored == message
        assert restored.retry_after_ms == 60000

    def test_dump_wire_camel_alias(self):
        message = AssistantMessage(stop_reason=StopReason.ERROR, retry_after_ms=1500)
        assert message.dump_wire()["retryAfterMs"] == 1500

    def test_dump_wire_absent_omitted(self):
        dumped = AssistantMessage().dump_wire(exclude_none=True)
        assert "retryAfterMs" not in dumped


class TestMessageConstruction:
    """消息构造行为（三种 role 的内容形态）。"""

    def test_user_message_string_content(self):
        from nova_protocol import UserMessage

        m = UserMessage(content="hello")
        assert m.role == "user"
        assert m.content == "hello"

    def test_user_message_list_content(self):
        from nova_protocol import TextContent, UserMessage

        m = UserMessage(content=[TextContent(text="hello")])
        assert isinstance(m.content, list)
        assert m.content[0].text == "hello"

    def test_tool_result_message(self):
        from nova_protocol import TextContent, ToolResultMessage

        m = ToolResultMessage(
            tool_call_id="tc1",
            tool_name="get_weather",
            content=[TextContent(text="sunny")],
            is_error=False,
        )
        assert m.role == "toolResult"
        assert m.tool_call_id == "tc1"


class TestContext:
    def test_context(self):
        from nova_protocol import Context, Tool, UserMessage

        ctx = Context(
            system_prompt="You are helpful",
            messages=[UserMessage(content="hello")],
            tools=[Tool(name="search", description="search web", parameters={})],
        )
        assert ctx.system_prompt == "You are helpful"
        assert len(ctx.messages) == 1
        assert len(ctx.tools) == 1


class TestL2VocabularyRoundtrip:
    """L2 词汇的序列化往返（数据建模 §十：契约类型至少一例
    model_validate(model_dump())——判别联合成员是重点）。"""

    def test_system_message_roundtrip_and_union_discrimination(self):
        from nova_protocol import Message, SystemMessage, Tool, ToolReference
        from pydantic import TypeAdapter

        original = SystemMessage(
            content="追加指令",
            sections={"mode": "只读", "obsolete": None},
            tools_added=[
                Tool(name="search", description="d", parameters={"type": "object"})
            ],
            tools_removed=[ToolReference(name="write")],
            timestamp=123,
        )
        rebuilt = SystemMessage.model_validate(original.model_dump())
        assert rebuilt == original
        # 判别联合成员：经 Message 联合回读仍是 SystemMessage（role 判别）
        via_union = TypeAdapter(Message).validate_python(original.model_dump())
        assert isinstance(via_union, SystemMessage)

    def test_system_message_content_block_form(self):
        """content 的块形态（TextContent 列表）同样往返。"""
        from nova_protocol import SystemMessage, TextContent

        original = SystemMessage(
            content=[TextContent(text="块一"), TextContent(text="块二")],
            timestamp=1,
        )
        rebuilt = SystemMessage.model_validate(original.model_dump())
        assert rebuilt == original

    def test_constrained_sampling_roundtrip(self):
        from nova_protocol import ConstrainedSampling, Tool

        tool = Tool(
            name="read",
            description="d",
            parameters={"type": "object"},
            constrained_sampling=ConstrainedSampling(
                type="json_schema", strict="prefer"
            ),
        )
        rebuilt = Tool.model_validate(tool.model_dump())
        assert rebuilt.constrained_sampling == tool.constrained_sampling

    def test_allowed_fallback_model_roundtrip_and_camel_alias(self):
        """fallback 条目：cost 的 camelCase 别名（pi.dev 站形状）双收。"""
        from nova_protocol import AnthropicAllowedFallbackModel, ModelCost

        entry = AnthropicAllowedFallbackModel(
            provider="anthropic",
            model="claude-opus-4-8",
            cost=ModelCost(input=1, output=5, cache_read=0.1, cache_write=0),
        )
        rebuilt = AnthropicAllowedFallbackModel.model_validate(entry.model_dump())
        assert rebuilt == entry
        # 站形状（camel 键）反序列化同值（populate_by_name 双收）
        via_camel = AnthropicAllowedFallbackModel.model_validate(
            {
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "cost": {
                    "input": 1,
                    "output": 5,
                    "cacheRead": 0.1,
                    "cacheWrite": 0,
                },
            }
        )
        assert via_camel.cost.cache_read == 0.1

    def test_prompt_cache_key_mode_literal(self):
        from nova_protocol import OpenAICompletionsCompat

        compat = OpenAICompletionsCompat.model_validate(
            {"prompt_cache_key_mode": "enabled"}
        )
        assert compat.prompt_cache_key_mode == "enabled"
        assert OpenAICompletionsCompat.model_validate(compat.model_dump()) == compat
