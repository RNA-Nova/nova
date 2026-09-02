"""
类型定义测试
"""

import pytest
from pydantic import ValidationError

from nova_ai.types import (
    AssistantMessage,
    CacheRetention,
    Context,
    Cost,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    ModelThinkingLevel,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    OpenRouterRouting,
    SimpleStreamOptions,
    StartEvent,
    StopReason,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingBudgets,
    ThinkingContent,
    ThinkingEndEvent,
    ThinkingFormat,
    ThinkingLevel,
    Tool,
    ToolCall,
    ToolCallEndEvent,
    ToolResultMessage,
    Transport,
    Usage,
    UserMessage,
    VercelGatewayRouting,
)


class TestEnums:
    """枚举类型测试"""

    def test_known_api_values(self):
        assert KnownApi.OPENAI_COMPLETIONS.value == "openai-completions"
        assert KnownApi.OPENAI_RESPONSES.value == "openai-responses"
        assert KnownApi.ANTHROPIC_MESSAGES.value == "anthropic-messages"

    def test_stop_reason_values(self):
        assert StopReason.STOP.value == "stop"
        assert StopReason.LENGTH.value == "length"
        assert StopReason.TOOL_USE.value == "toolUse"
        assert StopReason.ERROR.value == "error"
        assert StopReason.ABORTED.value == "aborted"

    def test_thinking_level_values(self):
        # 请求侧级别：不含 off（关闭思考用 reasoning=None 表达）
        assert ThinkingLevel.MINIMAL.value == "minimal"
        assert ThinkingLevel.LOW.value == "low"
        assert ThinkingLevel.MEDIUM.value == "medium"
        assert ThinkingLevel.HIGH.value == "high"
        assert ThinkingLevel.XHIGH.value == "xhigh"
        assert not hasattr(ThinkingLevel, "OFF")

    def test_model_thinking_level_values(self):
        # 模型/状态侧级别：含 off
        assert ModelThinkingLevel.OFF.value == "off"
        assert ModelThinkingLevel.MINIMAL.value == "minimal"
        assert ModelThinkingLevel.LOW.value == "low"
        assert ModelThinkingLevel.MEDIUM.value == "medium"
        assert ModelThinkingLevel.HIGH.value == "high"
        assert ModelThinkingLevel.XHIGH.value == "xhigh"

    def test_cache_retention_values(self):
        assert CacheRetention.NONE.value == "none"
        assert CacheRetention.SHORT.value == "short"
        assert CacheRetention.LONG.value == "long"

    def test_transport_values(self):
        assert Transport.SSE.value == "sse"
        assert Transport.WEBSOCKET.value == "websocket"

    def test_thinking_format_values(self):
        assert ThinkingFormat.OPENAI.value == "openai"
        assert ThinkingFormat.DEEPSEEK.value == "deepseek"
        assert ThinkingFormat.OPENROUTER.value == "openrouter"


class TestContentTypes:
    """内容类型测试"""

    def test_text_content_default(self):
        t = TextContent()
        assert t.type == "text"
        assert t.text == ""
        assert t.text_signature is None

    def test_text_content_serialization(self):
        t = TextContent(text="hello", text_signature="sig123")
        data = t.model_dump()
        assert data["text"] == "hello"
        assert data["text_signature"] == "sig123"

    def test_thinking_content(self):
        t = ThinkingContent(thinking="let me think", redacted=True)
        assert t.type == "thinking"
        assert t.thinking == "let me think"
        assert t.redacted is True

    def test_image_content(self):
        img = ImageContent(mime_type="image/png", data="base64data")
        assert img.type == "image"
        assert img.mime_type == "image/png"

    def test_tool_call(self):
        tc = ToolCall(id="tc1", name="get_weather", arguments={"city": "北京"})
        assert tc.type == "toolCall"
        assert tc.id == "tc1"
        assert tc.name == "get_weather"
        assert tc.arguments == {"city": "北京"}
        # partial_args 和 stream_index 不参与序列化
        data = tc.model_dump()
        assert "partial_args" not in data
        assert "stream_index" not in data


class TestUsageAndCost:
    """用量和成本测试"""

    def test_cost_default(self):
        c = Cost()
        assert c.input == 0.0
        assert c.output == 0.0
        assert c.cache_read == 0.0
        assert c.cache_write == 0.0
        assert c.total == 0.0

    def test_usage_default(self):
        u = Usage()
        assert u.input == 0
        assert u.output == 0
        assert u.cache_read == 0
        assert u.cache_write == 0
        assert u.total_tokens == 0
        assert isinstance(u.cost, Cost)

    def test_usage_with_cost(self):
        u = Usage(input=10, output=20, cost=Cost(input=0.01, output=0.02, total=0.03))
        assert u.input == 10
        assert u.cost.total == 0.03


class TestModel:
    """模型类型测试"""

    def test_model_creation(self):
        m = Model(
            id="test-model",
            name="Test Model",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.0),
            context_window=128000,
            max_tokens=4096,
        )
        assert m.id == "test-model"
        assert m.reasoning is False
        assert m.thinking_level_map is None

    def test_model_with_thinking_level_map(self):
        m = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=True,
            thinking_level_map={"high": "high", "xhigh": "max"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        assert m.thinking_level_map == {"high": "high", "xhigh": "max"}

    def test_model_auto_compat(self):
        """测试模型自动设置兼容性配置"""
        m = Model(
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
        assert m.compat is not None
        assert isinstance(m.compat, OpenAICompletionsCompat)


class TestMessages:
    """消息类型测试"""

    def test_user_message_string_content(self):
        m = UserMessage(content="hello")
        assert m.role == "user"
        assert m.content == "hello"

    def test_user_message_list_content(self):
        m = UserMessage(content=[TextContent(text="hello")])
        assert isinstance(m.content, list)
        assert m.content[0].text == "hello"

    def test_assistant_message(self):
        m = AssistantMessage(
            content=[TextContent(text="hi")],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            model="gpt-4",
        )
        assert m.role == "assistant"
        assert m.stop_reason == StopReason.STOP

    def test_tool_result_message(self):
        m = ToolResultMessage(
            tool_call_id="tc1",
            tool_name="get_weather",
            content=[TextContent(text="sunny")],
            is_error=False,
        )
        assert m.role == "toolResult"
        assert m.tool_call_id == "tc1"

    def test_context(self):
        ctx = Context(
            system_prompt="You are helpful",
            messages=[UserMessage(content="hello")],
            tools=[Tool(name="search", description="search web", parameters={})],
        )
        assert ctx.system_prompt == "You are helpful"
        assert len(ctx.messages) == 1
        assert len(ctx.tools) == 1


class TestStreamOptions:
    """流选项测试"""

    def test_stream_options_default(self):
        opts = StreamOptions()
        assert opts.temperature is None
        assert opts.max_tokens is None
        assert opts.transport is None
        assert opts.cache_retention is None

    def test_stream_options_with_values(self):
        opts = StreamOptions(
            temperature=0.7,
            max_tokens=1000,
            transport=Transport.SSE,
            cache_retention=CacheRetention.SHORT,
        )
        assert opts.temperature == 0.7
        assert opts.transport == Transport.SSE
        assert opts.cache_retention == CacheRetention.SHORT

    def test_simple_stream_options(self):
        opts = SimpleStreamOptions(
            temperature=0.5,
            reasoning=ThinkingLevel.HIGH,
        )
        assert opts.temperature == 0.5
        assert opts.reasoning == ThinkingLevel.HIGH

    def test_signal_and_callbacks_are_plain_fields(self):
        """signal/on_payload/on_response/transform_headers 是普通 dataclass 字段"""
        opts = StreamOptions(signal="dummy", on_payload=lambda x: x)
        assert opts.signal == "dummy"
        assert callable(opts.on_payload)
        assert opts.on_response is None
        assert opts.transform_headers is None

    def test_max_retry_delay_ms_preserved(self):
        """max_retry_delay_ms 是已声明字段，构造后保留原值"""
        opts = SimpleStreamOptions(max_retry_delay_ms=5000)
        assert opts.max_retry_delay_ms == 5000

    def test_extra_fields_forbidden(self):
        """选项类禁止未声明字段，写错字段立即抛错而非静默丢弃"""
        with pytest.raises(TypeError):
            StreamOptions(bogus_field=1)
        with pytest.raises(TypeError):
            SimpleStreamOptions(unknown_option=1)
        with pytest.raises(TypeError):
            ThinkingBudgets(loww=100)


class TestEvents:
    """事件类型测试"""

    def test_start_event(self):
        msg = AssistantMessage(content=[TextContent(text="start")])
        e = StartEvent(partial=msg)
        assert e.type == "start"
        assert e.partial == msg

    def test_text_delta_event(self):
        msg = AssistantMessage(content=[TextContent(text="hello")])
        e = TextDeltaEvent(content_index=0, delta="hello", partial=msg)
        assert e.type == "text_delta"
        assert e.delta == "hello"

    def test_text_end_event_content_is_str(self):
        """text_end 的 content 应该是字符串（对齐 TS）"""
        msg = AssistantMessage(content=[TextContent(text="hello world")])
        e = TextEndEvent(content_index=0, content="hello world", partial=msg)
        assert e.type == "text_end"
        assert e.content == "hello world"

    def test_thinking_end_event_content_is_str(self):
        """thinking_end 的 content 应该是字符串（对齐 TS）"""
        msg = AssistantMessage(content=[ThinkingContent(thinking="thinking...")])
        e = ThinkingEndEvent(content_index=0, content="thinking...", partial=msg)
        assert e.type == "thinking_end"
        assert e.content == "thinking..."

    def test_toolcall_end_event(self):
        tc = ToolCall(id="tc1", name="search")
        msg = AssistantMessage(content=[tc])
        e = ToolCallEndEvent(content_index=0, tool_call=tc, partial=msg)
        assert e.type == "toolcall_end"
        assert e.tool_call.name == "search"

    def test_done_event(self):
        msg = AssistantMessage(content=[TextContent(text="done")])
        e = DoneEvent(reason=StopReason.STOP, message=msg)
        assert e.type == "done"
        assert e.reason == StopReason.STOP

    def test_error_event(self):
        msg = AssistantMessage(error_message="something wrong")
        e = ErrorEvent(reason="error", error=msg)
        assert e.type == "error"


class TestCompat:
    """兼容性配置测试"""

    def test_openai_completions_compat_default(self):
        compat = OpenAICompletionsCompat()
        assert compat.thinking_format is None

    def test_openai_completions_compat_extra_allow(self):
        """OpenRouterRouting 应该允许额外字段"""
        routing = OpenRouterRouting(allow_fallbacks=False, order=["anthropic"])
        data = routing.model_dump()
        assert data["allow_fallbacks"] is False
        assert data["order"] == ["anthropic"]

    def test_openai_responses_compat(self):
        compat = OpenAIResponsesCompat(send_session_id_header=True)
        assert compat.send_session_id_header is True

    def test_vercel_gateway_routing(self):
        routing = VercelGatewayRouting(only=["bedrock"])
        assert routing.only == ["bedrock"]


class TestNovaBaseModel:
    """基础模型测试"""

    def test_enum_serialization(self):
        """Enum 序列化时使用 .value（字符串）"""
        m = Model(
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
        data = m.model_dump()
        assert data["api"] == "openai-completions"
        assert data["provider"] == "openai"

    def test_validate_assignment(self):
        """测试 validate_assignment（允许通过属性名赋值）"""
        t = TextContent(text="hello")
        t.text = "world"
        assert t.text == "world"
