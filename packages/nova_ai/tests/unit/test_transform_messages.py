"""transform_messages 测试（对齐 TS transformMessages）。"""

from nova_ai.types import (
    AssistantMessage,
    ImageContent,
    KnownApi,
    Model,
    ModelCost,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from nova_ai.utils.message_transformer import transform_messages


def _model(
    model_id: str = "test",
    provider: str = "test",
    input_types=None,
) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=provider,
        base_url="https://example.com",
        reasoning=True,
        input_types=input_types or ["text", "image"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )


class TestNullContent:
    def test_null_content_becomes_empty(self):
        # 用 model_construct 绕过 Pydantic 验证，模拟 untyped caller 传入的 null content
        msg = UserMessage.model_construct(content=None)
        result = transform_messages([msg], _model())
        assert len(result) == 1
        assert result[0].content == []


class TestImageDowngrade:
    def test_user_image_downgraded_when_not_supported(self):
        msg = UserMessage(
            content=[
                TextContent(text="hello"),
                ImageContent(mime_type="image/png", data="abc"),
            ]
        )
        model = _model(input_types=["text"])
        result = transform_messages([msg], model)

        assert len(result) == 1
        content = result[0].content
        assert len(content) == 2
        assert content[0].text == "hello"
        assert content[1].text == "(image omitted: model does not support images)"

    def test_tool_result_image_downgraded_when_not_supported(self):
        msg = ToolResultMessage(
            tool_call_id="tc1",
            tool_name="t1",
            content=[
                TextContent(text="result"),
                ImageContent(mime_type="image/png", data="abc"),
            ],
        )
        model = _model(input_types=["text"])
        result = transform_messages([msg], model)

        content = result[0].content
        assert len(content) == 2
        assert content[0].text == "result"
        assert content[1].text == "(tool image omitted: model does not support images)"

    def test_image_kept_when_supported(self):
        msg = UserMessage(
            content=[
                TextContent(text="hello"),
                ImageContent(mime_type="image/png", data="abc"),
            ]
        )
        model = _model(input_types=["text", "image"])
        result = transform_messages([msg], model)

        content = result[0].content
        assert len(content) == 2
        assert content[1].type == "image"

    def test_consecutive_images_single_placeholder(self):
        msg = UserMessage(
            content=[
                ImageContent(mime_type="image/png", data="a"),
                ImageContent(mime_type="image/png", data="b"),
                ImageContent(mime_type="image/png", data="c"),
            ]
        )
        model = _model(input_types=["text"])
        result = transform_messages([msg], model)

        content = result[0].content
        assert len(content) == 1
        assert content[0].text == "(image omitted: model does not support images)"


class TestToolCallIdNormalization:
    def test_pipe_separated_id_normalized(self):
        assistant = AssistantMessage(
            content=[ToolCall(id="call_123|abc/def+ghi=", name="search", arguments={})],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="other",
            model="other",
        )
        tool_result = ToolResultMessage(
            tool_call_id="call_123|abc/def+ghi=",
            tool_name="search",
            content=[TextContent(text="result")],
        )

        def normalize(id, model, source):
            return id.split("|")[0]

        result = transform_messages([assistant, tool_result], _model(), normalize)

        assert result[0].content[0].id == "call_123"
        assert result[1].tool_call_id == "call_123"

    def test_tool_result_id_updated_via_map(self):
        assistant = AssistantMessage(
            content=[ToolCall(id="old_id", name="search", arguments={})],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="other",
            model="other",
        )
        tool_result = ToolResultMessage(
            tool_call_id="old_id",
            tool_name="search",
            content=[TextContent(text="result")],
        )

        def normalize(id, model, source):
            return "new_id"

        result = transform_messages([assistant, tool_result], _model(), normalize)

        assert result[0].content[0].id == "new_id"
        assert result[1].tool_call_id == "new_id"

    def test_tool_result_fields_preserved_on_id_normalization(self):
        """规范化 tool_call_id 重建消息时，added_tool_names / details 等字段不丢（回归）。"""
        assistant = AssistantMessage(
            content=[ToolCall(id="old_id", name="search", arguments={})],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="other",
            model="other",
        )
        tool_result = ToolResultMessage(
            tool_call_id="old_id",
            tool_name="search",
            content=[TextContent(text="result")],
            details={"k": "v"},
            is_error=True,
            added_tool_names=["new_tool_a", "new_tool_b"],
        )

        def normalize(id, model, source):
            return "new_id"

        result = transform_messages([assistant, tool_result], _model(), normalize)

        rebuilt = result[1]
        assert rebuilt.tool_call_id == "new_id"
        assert rebuilt.added_tool_names == ["new_tool_a", "new_tool_b"]
        assert rebuilt.details == {"k": "v"}
        assert rebuilt.is_error is True

    def test_assistant_diagnostics_preserved_on_rebuild(self):
        """跨模型重建 assistant 消息时，diagnostics / response_id 等字段不丢（回归）。"""
        assistant = AssistantMessage(
            content=[TextContent(text="hello")],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="other",
            model="other",
            response_id="resp-1",
            response_model="other-concrete",
            diagnostics=[{"type": "retry", "details": {"attempt": 2}}],
        )

        result = transform_messages([assistant], _model())

        rebuilt = result[0]
        assert rebuilt.diagnostics == [{"type": "retry", "details": {"attempt": 2}}]
        assert rebuilt.response_id == "resp-1"
        assert rebuilt.response_model == "other-concrete"


class TestOrphanedToolCalls:
    def test_orphaned_tool_call_gets_synthetic_result(self):
        assistant = AssistantMessage(
            content=[ToolCall(id="tc1", name="search", arguments={})],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="test",
            model="test",
        )
        result = transform_messages([assistant], _model())

        assert len(result) == 2
        assert result[0] == assistant
        assert result[1].role == "toolResult"
        assert result[1].tool_call_id == "tc1"
        assert result[1].is_error is True
        assert "No result provided" in result[1].content[0].text

    def test_user_message_breaks_tool_flow(self):
        assistant1 = AssistantMessage(
            content=[ToolCall(id="tc1", name="search", arguments={})],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="test",
            model="test",
        )
        user = UserMessage(content="next")
        assistant2 = AssistantMessage(
            content=[TextContent(text="answer")],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="test",
            model="test",
        )

        result = transform_messages([assistant1, user, assistant2], _model())

        # assistant1 + synthetic result + user + assistant2
        assert len(result) == 4
        assert result[1].role == "toolResult"
        assert result[2] == user

    def test_existing_tool_result_not_duplicated(self):
        assistant = AssistantMessage(
            content=[ToolCall(id="tc1", name="search", arguments={})],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="test",
            model="test",
        )
        tool_result = ToolResultMessage(
            tool_call_id="tc1",
            tool_name="search",
            content=[TextContent(text="ok")],
        )

        result = transform_messages([assistant, tool_result], _model())
        assert len(result) == 2
        assert result[1] == tool_result


class TestThinkingBlocks:
    def test_thinking_kept_for_same_model(self):
        thinking = ThinkingContent(thinking="reasoning", thinking_signature="sig")
        assistant = AssistantMessage(
            content=[thinking],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="test",
            model="test",
        )
        result = transform_messages([assistant], _model())
        assert result[0].content[0] == thinking

    def test_thinking_converted_to_text_for_other_model(self):
        thinking = ThinkingContent(thinking="reasoning", thinking_signature="sig")
        assistant = AssistantMessage(
            content=[thinking],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="other",
            model="other",
        )
        result = transform_messages([assistant], _model())
        assert result[0].content[0].type == "text"
        assert result[0].content[0].text == "reasoning"

    def test_redacted_thinking_removed_for_other_model(self):
        thinking = ThinkingContent(
            thinking="reasoning", thinking_signature="sig", redacted=True
        )
        assistant = AssistantMessage(
            content=[thinking],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="other",
            model="other",
        )
        result = transform_messages([assistant], _model())
        assert len(result[0].content) == 0

    def test_empty_thinking_removed(self):
        thinking = ThinkingContent(thinking="")
        assistant = AssistantMessage(
            content=[thinking],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="test",
            model="test",
        )
        result = transform_messages([assistant], _model())
        assert len(result[0].content) == 0


class TestErrorMessages:
    def test_error_assistant_removed(self):
        from nova_ai.types import StopReason

        assistant = AssistantMessage(
            content=[TextContent(text="partial")],
            provider="test",
            model="test",
            stop_reason=StopReason.ERROR,
        )
        result = transform_messages([assistant], _model())
        assert len(result) == 0

    def test_aborted_assistant_removed(self):
        from nova_ai.types import StopReason

        assistant = AssistantMessage(
            content=[TextContent(text="partial")],
            provider="test",
            model="test",
            stop_reason=StopReason.ABORTED,
        )
        result = transform_messages([assistant], _model())
        assert len(result) == 0
