"""anthropic_messages/messages.py 测试（消息转换/工具转换/id 规范化）。

Anthropic 形态不变量逐条锁：签名回放、redacted 透传、tool_result 分组、
INLINE_TOOLS 中途 system 块、cache_control 落点、schema 裁剪。
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
        params = convert_messages(model, ctx, get_compat(model), None).messages
        assert params == [{"role": "user", "content": "hello"}]

    def test_empty_text_blocks_dropped(self):
        model = _model()
        ctx = Context(messages=[UserMessage(content=[TextContent(text="  ")])])
        params = convert_messages(model, ctx, get_compat(model), None).messages
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
        params = convert_messages(model, ctx, get_compat(model), None).messages
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
        params = convert_messages(model, ctx, get_compat(model), None).messages
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
        params = convert_messages(model, ctx, get_compat(model), None).messages
        assert params[1]["content"] == [{"type": "text", "text": "半截思考"}]

    def test_allow_empty_signature_keeps_thinking_block(self):
        model = _model(compat=AnthropicMessagesCompat(allow_empty_signature=True))
        assistant = _assistant([ThinkingContent(thinking="半截思考")])
        ctx = Context(messages=[UserMessage(content="q"), assistant])
        params = convert_messages(model, ctx, get_compat(model), None).messages
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
        params = convert_messages(model, ctx, get_compat(model), None).messages
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
        params = convert_messages(model, ctx, get_compat(model), None).messages
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
        params = convert_messages(model, ctx, get_compat(model), None).messages
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

    def test_error_result_images_split_after_tool_results(self):
        """错误工具结果带图：图换占位进结果块，原图 + caption 随全部结果
        之后送达（对齐 pi #10794——Anthropic 拒收错误结果里的非文本内容）。"""
        model = _model()
        ctx = Context(
            messages=[
                UserMessage(content="q"),
                ToolResultMessage(
                    tool_call_id="t1",
                    tool_name="shot",
                    content=[
                        TextContent(text="截图失败"),
                        ImageContent(
                            type="image", mime_type="image/png", data="aGVsbG8="
                        ),
                    ],
                    is_error=True,
                ),
            ]
        )
        params = convert_messages(model, ctx, get_compat(model), None).messages
        user = params[-1]
        assert user["content"][0] == {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "截图失败\n(image attached after the tool results)",
            "is_error": True,
        }
        assert user["content"][1] == {
            "type": "text",
            "text": "Images from the error result of tool call t1 (shot):",
        }
        assert user["content"][2]["type"] == "image"
        assert user["content"][2]["source"]["data"] == "aGVsbG8="

    def test_success_result_images_stay_inline(self):
        """非错误结果的图片不拆（对齐 pi：成功结果原样）。"""
        model = _model()
        ctx = Context(
            messages=[
                UserMessage(content="q"),
                ToolResultMessage(
                    tool_call_id="t1",
                    tool_name="shot",
                    content=[
                        ImageContent(
                            type="image", mime_type="image/png", data="aGVsbG8="
                        ),
                    ],
                ),
            ]
        )
        params = convert_messages(model, ctx, get_compat(model), None).messages
        user = params[-1]
        assert user["content"][0]["type"] == "tool_result"
        blocks = user["content"][0]["content"]
        assert any(b.get("type") == "image" for b in blocks)

    def test_tool_result_plain_after_inline_tools_switch(self):
        """INLINE_TOOLS 切换后 toolResult 只有结果块（对齐 pi 当前
        convertMessages——工具变更只走 SystemMessage 通道）。"""
        model = _model()
        result_msg = ToolResultMessage(
            tool_call_id="t1",
            tool_name="tool_search",
            content=[TextContent(text="found search")],
        )
        ctx = Context(messages=[UserMessage(content="q"), result_msg])
        params = convert_messages(model, ctx, get_compat(model), None).messages
        assert params[-1]["content"][0] == {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "found search",
            "is_error": False,
        }


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
        params = convert_messages(
            model, ctx, get_compat(model), {"type": "ephemeral"}
        ).messages
        assert params[0]["content"] == "first"
        last = params[-1]
        assert last["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_string_content_wrapped_to_block_for_cache_control(self):
        model = _model()
        ctx = Context(messages=[UserMessage(content="hi")])
        params = convert_messages(
            model, ctx, get_compat(model), {"type": "ephemeral", "ttl": "1h"}
        ).messages
        assert params[0]["content"] == [
            {
                "type": "text",
                "text": "hi",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ]


class TestConvertTools:
    def test_legacy_schema_strips_extra_keys(self):
        """非 strict：input_schema 留 type/properties/required + $defs/definitions，
        其余根键（$schema/additionalProperties/anyOf 族）丢弃。"""
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

    def test_legacy_schema_keeps_defs_drops_root_combinators(self):
        """非 strict：$defs/definitions 保留（属性 $refs 可解析——对齐 TS
        4a6a58eb5）；根 anyOf/oneOf/allOf 丢弃（Anthropic 拒收 400）。"""
        tool = _tool("search")
        tool.parameters = {
            **tool.parameters,
            "properties": {
                "target": {"$ref": "#/$defs/entity"},
            },
            "$defs": {
                "entity": {"type": "object", "properties": {"id": {"type": "string"}}}
            },
            "definitions": {"legacy": {"type": "string"}},
            "anyOf": [{"type": "string"}],
            "oneOf": [{"type": "number"}],
            "allOf": [{"type": "boolean"}],
        }
        entries = convert_tools([tool], get_compat(_model()))
        schema = entries[0]["input_schema"]
        assert schema["$defs"] == {
            "entity": {"type": "object", "properties": {"id": {"type": "string"}}}
        }
        assert schema["definitions"] == {"legacy": {"type": "string"}}
        assert schema["properties"]["target"] == {"$ref": "#/$defs/entity"}
        for dropped in ("anyOf", "oneOf", "allOf"):
            assert dropped not in schema

    def test_strict_requires_tool_opt_in(self):
        """strict 是按工具裁决：模型支持但工具未声明 constrained_sampling →
        不 strict（对位 pi——strict 是工具级契约不是模型级开关）。"""
        model = _model(compat=AnthropicMessagesCompat(supports_strict_tools=True))
        tool = _tool("search")
        tool.parameters = {**tool.parameters, "additionalProperties": False}
        entries = convert_tools([tool], get_compat(model))
        assert "strict" not in entries[0]
        assert entries[0]["input_schema"]["type"] == "object"

    def test_strict_opt_in_viable_sanitized_schema(self):
        """prefer + 模型支持 + schema 可净化 → strict + 净化后 schema
        （required=全部属性、additionalProperties=false）。"""
        from nova_protocol import ConstrainedSampling

        model = _model(compat=AnthropicMessagesCompat(supports_strict_tools=True))
        tool = _tool("search")
        tool.constrained_sampling = ConstrainedSampling(
            type="json_schema", strict="prefer"
        )
        entries = convert_tools([tool], get_compat(model))
        assert entries[0]["strict"] is True
        assert entries[0]["input_schema"]["required"] == ["x"]
        assert entries[0]["input_schema"]["additionalProperties"] is False

    def test_strict_prefer_unviable_falls_back(self):
        """prefer + schema 含 strict 不支持的关键字（minimum）→ 回落非严格
        （pi viability 判定同款）。"""
        from nova_protocol import ConstrainedSampling

        model = _model(compat=AnthropicMessagesCompat(supports_strict_tools=True))
        tool = _tool("search")
        tool.parameters = {
            **tool.parameters,
            "properties": {"n": {"type": "integer", "minimum": 1}},
        }
        tool.constrained_sampling = ConstrainedSampling(
            type="json_schema", strict="prefer"
        )
        entries = convert_tools([tool], get_compat(model))
        assert "strict" not in entries[0]
        assert entries[0]["input_schema"]["type"] == "object"

    def test_strict_require_unviable_raises(self):
        """require + 不可净化 → 请求期报错（不给模型次优）。"""
        from nova_protocol import ConstrainedSampling

        model = _model(compat=AnthropicMessagesCompat(supports_strict_tools=True))
        tool = _tool("search")
        tool.parameters = {
            **tool.parameters,
            "properties": {"n": {"type": "integer", "minimum": 1}},
        }
        tool.constrained_sampling = ConstrainedSampling(
            type="json_schema", strict="require"
        )
        import pytest

        with pytest.raises(ValueError, match="constrained sampling"):
            convert_tools([tool], get_compat(model))

    def test_cache_control_on_last(self):
        model = _model()
        tools = [_tool("a"), _tool("b")]
        entries = convert_tools(
            tools,
            get_compat(model),
            cache_control={"type": "ephemeral"},
        )
        assert "cache_control" not in entries[0]
        assert entries[1]["cache_control"] == {"type": "ephemeral"}


class TestInlineToolChangeBlocks:
    """中途 system 消息的 INLINE_TOOLS 块（对齐 pi convertMessages 的
    convertToolDefinitions 分支）。"""

    def _system_with_tools(self):
        from nova_protocol import SystemMessage, ToolReference

        return SystemMessage(
            sections={"mode": "只读"},
            tools_added=[_tool("search"), _tool("read")],
            tools_removed=[ToolReference(name="write"), ToolReference(name="search")],
            timestamp=2,
        )

    def test_addition_removal_blocks(self):
        """文本 + removal（被同名新增覆盖的跳过）+ addition（完整定义）。"""
        model = _model()
        ctx = Context(
            messages=[
                UserMessage(content="q1"),
                self._system_with_tools(),
                _assistant([TextContent(text="a1")]),
            ]
        )
        params = convert_messages(
            model,
            ctx,
            get_compat(model),
            None,
            lambda tools: convert_tools(tools, get_compat(model)),
        ).messages
        system = next(m for m in params if m["role"] == "system")
        types = [b["type"] for b in system["content"]]
        assert types == ["text", "tool_removal", "tool_addition", "tool_addition"]
        assert system["content"][1]["tool"] == {
            "type": "tool_reference",
            "name": "write",
        }
        additions = [b["tool"]["definition"]["name"] for b in system["content"][2:]]
        assert additions == ["search", "read"]

    def test_no_definitions_converter_text_only(self):
        """非 INLINE 形态（无转换器）：只发文本块。"""
        model = _model()
        ctx = Context(
            messages=[
                UserMessage(content="q1"),
                self._system_with_tools(),
                _assistant([TextContent(text="a1")]),
            ]
        )
        params = convert_messages(model, ctx, get_compat(model), None).messages
        system = next(m for m in params if m["role"] == "system")
        assert [b["type"] for b in system["content"]] == ["text"]
