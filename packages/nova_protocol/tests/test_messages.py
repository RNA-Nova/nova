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
