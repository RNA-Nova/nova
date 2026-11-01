"""anthropic_messages/messages.py 测试（消息转换/工具转换/deferred 拆分/id 规范化）。

Anthropic 形态不变量逐条锁：签名回放、redacted 透传、tool_result 分组、
tool_reference 兄弟块、cache_control 落点、schema 裁剪。
"""

from nova_protocol import (
    AnthropicMessagesCompat,
    AssistantMessage,
    Context,
    ImageContent,
    KnownApi,
    Model,
    ModelCost,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from nova_ai.api_impls.anthropic_messages.compat import get_compat
from nova_ai.api_impls.anthropic_messages.messages import (
    convert_messages,
    convert_tools,
    normalize_tool_call_id,
    split_deferred_tools,
)


def _model(compat=None) -> Model:
    return Model(
        id="claude-sonnet-4-5",
        name="m",
        api=KnownApi.ANTHROPIC_MESSAGES,
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input_types=["text", "image"],
        cost=ModelCost(input=3, output=15, cache_read=0.3, cache_write=3.75),
        context_window=200000,
        max_tokens=64000,
        compat=compat,
    )


def _assistant(content, stop_reason="stop") -> AssistantMessage:
    """构造与被测模型同源的 assistant 消息（transform_messages 的同模型
    回放判定要求 api/provider/model 三元组一致）。"""
    return AssistantMessage(
        content=content,
        api=KnownApi.ANTHROPIC_MESSAGES,
        provider="anthropic",
        model="claude-sonnet-4-5",
        usage=Usage(),
        stop_reason=stop_reason,
    )


def _tool(name: str) -> Tool:
    return Tool(
        name=name,
        description=f"{name} desc",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
    )


class TestNormalizeToolCallId:
    def test_illegal_chars_replaced(self):
        assert normalize_tool_call_id("call abc:1.2|x") == "call_abc_1_2_x"

    def test_truncates_to_64(self):
        assert len(normalize_tool_call_id("a" * 100)) == 64


class TestConvertMessagesUser:
    def test_string_content_passthrough(self):
        model = _model()
        ctx = Context(messages=[UserMessage(content="hello")])
        params = convert_messages(model, ctx, get_compat(model), None)
        assert params == [{"role": "user", "content": "hello"}]

    def test_empty_text_blocks_dropped(self):
        model = _model()
        ctx = Context(messages=[UserMessage(content=[TextContent(text="  ")])])
        params = convert_messages(model, ctx, get_compat(model), None)
        assert params == []

    def test_image_block_base64_source(self):
        model = _model()
        ctx = Context(
            messages=[
                UserMessage(
                    content=[
                        TextContent(text="看图"),
                        ImageContent(mime_type="image/png", data="QUJD"),
                    ]
                )
            ]
        )
        params = convert_messages(model, ctx, get_compat(model), None)
        assert params[0]["content"][1] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
        }


class TestConvertMessagesAssistant:
    def test_thinking_signature_replayed_verbatim(self):
        """签名原样回传（缺签名下一轮 400 的防线）。"""
        model = _model()
        assistant = _assistant(
            [
                ThinkingContent(thinking="想了想", thinking_signature="sig-xyz"),
                TextContent(text="答案"),
            ]
        )
        ctx = Context(messages=[UserMessage(content="q"), assistant])
        params = convert_messages(model, ctx, get_compat(model), None)
        assistant_param = params[1]
        assert assistant_param["content"][0] == {
            "type": "thinking",
            "thinking": "想了想",
            "signature": "sig-xyz",
        }
        assert assistant_param["content"][1] == {"type": "text", "text": "答案"}

    def test_thinking_without_signature_downgrades_to_text(self):
        """缺签名（如流被 abort）→ 降级纯文本（端点不收无签名 thinking）。"""
        model = _model()
        assistant = _assistant([ThinkingContent(thinking="半截思考")])
        ctx = Context(messages=[UserMessage(content="q"), assistant])
        params = convert_messages(model, ctx, get_compat(model), None)
        assert params[1]["content"] == [{"type": "text", "text": "半截思考"}]

    def test_allow_empty_signature_keeps_thinking_block(self):
        model = _model(compat=AnthropicMessagesCompat(allow_empty_signature=True))
        assistant = _assistant([ThinkingContent(thinking="半截思考")])
        ctx = Context(messages=[UserMessage(content="q"), assistant])
        params = convert_messages(model, ctx, get_compat(model), None)
        assert params[1]["content"] == [
            {"type": "thinking", "thinking": "半截思考", "signature": ""}
        ]

    def test_redacted_thinking_passthrough(self):
        """redacted：不透明载荷收 redacted_thinking.data 原样回传。"""
        model = _model()
        assistant = _assistant(
            [
                ThinkingContent(
                    thinking="[Reasoning redacted]",
                    thinking_signature="opaque-blob",
                    redacted=True,
                )
            ]
        )
        ctx = Context(messages=[UserMessage(content="q"), assistant])
        params = convert_messages(model, ctx, get_compat(model), None)
        assert params[1]["content"] == [
            {"type": "redacted_thinking", "data": "opaque-blob"}
        ]

    def test_tool_use_block(self):
        model = _model()
        assistant = _assistant(
            [ToolCall(id="toolu_1", name="get_weather", arguments={"city": "bj"})],
            stop_reason="toolUse",
        )
        ctx = Context(messages=[UserMessage(content="q"), assistant])
        params = convert_messages(model, ctx, get_compat(model), None)
        assert params[1]["content"] == [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "get_weather",
                "input": {"city": "bj"},
            }
        ]


class TestConvertMessagesToolResult:
    def test_consecutive_results_grouped_into_one_user_message(self):
        model = _model()
        ctx = Context(
            messages=[
                UserMessage(content="q"),
                ToolResultMessage(
                    tool_call_id="t1", tool_name="a", content=[TextContent(text="r1")]
                ),
                ToolResultMessage(
                    tool_call_id="t2",
                    tool_name="b",
                    content=[TextContent(text="r2")],
                    is_error=True,
                ),
            ]
        )
        params = convert_messages(model, ctx, get_compat(model), None)
        user = params[-1]
        assert user["role"] == "user"
        assert user["content"] == [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "r1",
                "is_error": False,
            },
            {
                "type": "tool_result",
                "tool_use_id": "t2",
                "content": "r2",
                "is_error": True,
            },
        ]

    def test_tool_reference_emitted_for_deferred_tool(self):
        """added_tool_names ∩ deferred → tool_reference 块，原内容降为兄弟块。"""
        model = _model()
        tools = [_tool("search")]
        result_msg = ToolResultMessage(
            tool_call_id="t1",
            tool_name="tool_search",
            content=[TextContent(text="found search")],
            added_tool_names=["search"],
        )
        ctx = Context(messages=[UserMessage(content="q"), result_msg], tools=tools)
        immediate, deferred = split_deferred_tools(ctx, enabled=True)
        assert immediate == [] and set(deferred) == {"search"}
        params = convert_messages(model, ctx, get_compat(model), None, set(deferred))
        user = params[-1]
        assert user["content"][0] == {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": [{"type": "tool_reference", "tool_name": "search"}],
            "is_error": False,
        }
        # 端点不收 reference 与普通内容的混合——原内容拆为兄弟块
        assert user["content"][1] == {"type": "text", "text": "found search"}

    def test_no_deference_when_unsupported(self):
        """端点不支持 tool_reference：全部即刻组、无 reference 块。"""
        model = _model()
        tools = [_tool("search")]
        result_msg = ToolResultMessage(
            tool_call_id="t1",
            tool_name="tool_search",
            content=[TextContent(text="r")],
            added_tool_names=["search"],
        )
        ctx = Context(messages=[UserMessage(content="q"), result_msg], tools=tools)
        immediate, deferred = split_deferred_tools(ctx, enabled=False)
        assert [t.name for t in immediate] == ["search"] and deferred == {}
        params = convert_messages(model, ctx, get_compat(model), None, set())
        assert params[-1]["content"][0]["content"] == "r"


class TestCacheControlPlacement:
    def test_last_user_block_gets_cache_control(self):
        model = _model()
        ctx = Context(
            messages=[
                UserMessage(content="first"),
                _assistant([TextContent(text="a")]),
                UserMessage(content="second"),
            ]
        )
        params = convert_messages(model, ctx, get_compat(model), {"type": "ephemeral"})
        assert params[0]["content"] == "first"
        last = params[-1]
        assert last["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_string_content_wrapped_to_block_for_cache_control(self):
        model = _model()
        ctx = Context(messages=[UserMessage(content="hi")])
        params = convert_messages(
            model, ctx, get_compat(model), {"type": "ephemeral", "ttl": "1h"}
        )
        assert params[0]["content"] == [
            {
                "type": "text",
                "text": "hi",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ]


class TestConvertTools:
    def test_legacy_schema_strips_extra_keys(self):
        """非 strict：input_schema 只留 type/properties/required（对齐 TS）。"""
        tool = _tool("search")
        tool.parameters = {
            **tool.parameters,
            "$schema": "http://x",
            "additionalProperties": False,
        }
        entries = convert_tools([tool], get_compat(_model()))
        assert entries[0]["input_schema"] == {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
        assert entries[0]["eager_input_streaming"] is True
        assert "strict" not in entries[0]

    def test_strict_keeps_full_schema_and_flag(self):
        model = _model(compat=AnthropicMessagesCompat(supports_strict_tools=True))
        tool = _tool("search")
        tool.parameters = {**tool.parameters, "additionalProperties": False}
        entries = convert_tools([tool], get_compat(model))
        assert entries[0]["strict"] is True
        assert entries[0]["input_schema"]["additionalProperties"] is False

    def test_defer_loading_and_cache_control_on_last(self):
        model = _model()
        tools = [_tool("a"), _tool("b")]
        entries = convert_tools(
            tools,
            get_compat(model),
            cache_control={"type": "ephemeral"},
            defer_loading=True,
        )
        assert all(e["defer_loading"] is True for e in entries)
        assert "cache_control" not in entries[0]
        assert entries[1]["cache_control"] == {"type": "ephemeral"}


class TestSplitDeferredTools:
    def test_used_tools_stay_immediate(self):
        """已被 toolCall 使用的工具不再延迟（对齐 TS usedNames 排除）。"""
        tools = [_tool("search"), _tool("read")]
        assistant = _assistant(
            [ToolCall(id="t1", name="search", arguments={})], stop_reason="toolUse"
        )
        result = ToolResultMessage(
            tool_call_id="t1",
            tool_name="search",
            content=[TextContent(text="r")],
            added_tool_names=["search"],
        )
        ctx = Context(
            messages=[UserMessage(content="q"), assistant, result], tools=tools
        )
        immediate, deferred = split_deferred_tools(ctx, enabled=True)
        assert {t.name for t in immediate} == {"search", "read"}
        assert deferred == {}
