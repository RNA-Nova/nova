"""estimate_context_tokens 测试（对齐 TS estimateContextTokens）。"""

from nova_ai.utils.estimate import estimate_context_tokens
from nova_protocol import (
    AssistantMessage,
    Context,
    KnownApi,
    Model,
    ModelCost,
    StopReason,
    TextContent,
    Tool,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def _model() -> Model:
    return Model(
        id="test",
        name="test",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider="test",
        base_url="https://example.com",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )


class TestEstimateContextTokens:
    def test_empty_context(self):
        ctx = Context(messages=[])
        estimate = estimate_context_tokens(ctx)
        assert estimate.tokens == 0
        assert estimate.usage_tokens == 0
        assert estimate.trailing_tokens == 0
        assert estimate.last_usage_index is None

    def test_system_prompt_and_tools(self):
        ctx = Context(
            system_prompt="You are helpful",
            messages=[],
            tools=[Tool(name="search", description="search", parameters={})],
        )
        estimate = estimate_context_tokens(ctx)
        assert estimate.tokens > 0
        assert estimate.usage_tokens == 0

    def test_usage_anchor(self):
        usage = Usage(
            input=1000,
            output=100,
            cache_read=0,
            cache_write=0,
            total_tokens=1100,
        )
        assistant = AssistantMessage(
            content=[TextContent(text="answer")],
            provider="test",
            model="test",
            usage=usage,
            stop_reason=StopReason.STOP,
            timestamp=100,
        )
        trailing = UserMessage(content="next", timestamp=200)
        ctx = Context(messages=[assistant, trailing])

        estimate = estimate_context_tokens(ctx)
        assert estimate.usage_tokens == 1100
        assert estimate.trailing_tokens > 0
        assert estimate.tokens == estimate.usage_tokens + estimate.trailing_tokens
        assert estimate.last_usage_index == 0

    def test_usage_anchor_with_mid_convo_tools_added(self):
        """usage 锚点之后的中途 system 消息 tools_added 计入尾量。"""
        from nova_protocol import SystemMessage

        usage = Usage(
            input=1000,
            output=100,
            cache_read=0,
            cache_write=0,
            total_tokens=1100,
        )
        assistant = AssistantMessage(
            content=[TextContent(text="answer")],
            provider="test",
            model="test",
            usage=usage,
            stop_reason=StopReason.STOP,
            timestamp=100,
        )
        dynamic_tool = Tool(name="dynamic_tool", description="dynamic", parameters={})
        tool_announce = SystemMessage(tools_added=[dynamic_tool], timestamp=200)
        ctx = Context(
            messages=[assistant, tool_announce],
            tools=[dynamic_tool],
        )

        estimate = estimate_context_tokens(ctx)
        # 应包含 tools_added 声明的工具定义 tokens
        assert estimate.trailing_tokens > 0

    def test_system_message_with_string_content_estimated(self):
        """SystemMessage（L2 词汇）字符串 content 不再炸估算循环，且按
        呈现口径计入（sections 值同样计）。"""
        from nova_protocol import SystemMessage

        ctx = Context(
            messages=[
                UserMessage(content="q"),
                SystemMessage(
                    content="追加指令", sections={"mode": "只读"}, timestamp=1
                ),
            ]
        )
        estimate = estimate_context_tokens(ctx)
        # 两条消息都被计入（修复前：SystemMessage 的 str content 让循环 AttributeError）
        assert estimate.tokens > 0

    def test_error_assistant_not_used_as_anchor(self):
        usage = Usage(
            input=1000,
            output=100,
            cache_read=0,
            cache_write=0,
            total_tokens=1100,
        )
        error_assistant = AssistantMessage(
            content=[TextContent(text="error")],
            provider="test",
            model="test",
            usage=usage,
            stop_reason=StopReason.ERROR,
            timestamp=100,
        )
        user = UserMessage(content="hi", timestamp=200)
        ctx = Context(messages=[error_assistant, user])

        estimate = estimate_context_tokens(ctx)
        assert estimate.usage_tokens == 0
        assert estimate.last_usage_index is None

    def test_aborted_assistant_not_used_as_anchor(self):
        usage = Usage(
            input=1000,
            output=100,
            cache_read=0,
            cache_write=0,
            total_tokens=1100,
        )
        aborted_assistant = AssistantMessage(
            content=[TextContent(text="aborted")],
            provider="test",
            model="test",
            usage=usage,
            stop_reason=StopReason.ABORTED,
            timestamp=100,
        )
        user = UserMessage(content="hi", timestamp=200)
        ctx = Context(messages=[aborted_assistant, user])

        estimate = estimate_context_tokens(ctx)
        assert estimate.usage_tokens == 0
        assert estimate.last_usage_index is None

    def test_newer_prefix_invalidates_anchor(self):
        """assistant 之后插入更新的前缀消息，其 usage 不再有效。"""
        usage = Usage(
            input=1000,
            output=100,
            cache_read=0,
            cache_write=0,
            total_tokens=1100,
        )
        assistant = AssistantMessage(
            content=[TextContent(text="answer")],
            provider="test",
            model="test",
            usage=usage,
            stop_reason=StopReason.STOP,
            timestamp=100,
        )
        # 更新的前缀消息（timestamp 更大，但在 assistant 之前）
        newer_prefix = UserMessage(content="prefix", timestamp=300)
        ctx = Context(messages=[newer_prefix, assistant])

        estimate = estimate_context_tokens(ctx)
        # assistant 的 timestamp (100) < latest_prefix_timestamp (300)，不作为锚点
        assert estimate.last_usage_index is None


def test_system_message_estimate_uses_rendered_separators():
    from nova_ai.utils.estimate import estimate_message_tokens, estimate_text_tokens
    from nova_protocol import SystemMessage

    message = SystemMessage(content="a", sections={"x": "bc"})
    assert estimate_message_tokens(message) == estimate_text_tokens("a\n\nbc")
