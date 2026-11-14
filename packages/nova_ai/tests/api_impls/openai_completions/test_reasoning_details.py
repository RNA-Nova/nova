"""reasoning details 的识别、拼接、解析与回放护栏测试。"""

import json

from nova_ai.api_impls.openai_completions import (
    append_openai_reasoning_detail,
    build_params,
    is_openai_reasoning_detail,
    is_reasoning_field,
    parse_legacy_encrypted_reasoning_detail,
    parse_openai_reasoning_details,
)
from nova_protocol import (
    AssistantMessage,
    Context,
    KnownApi,
    KnownProvider,
    TextContent,
    ThinkingContent,
    ToolCall,
    UserMessage,
)

from .conftest import _ctx, _model


class TestReasoningDetails:
    def test_is_reasoning_field_whitelist(self):
        assert is_reasoning_field("reasoning")
        assert is_reasoning_field("reasoning_content")
        assert is_reasoning_field("reasoning_text")
        assert not is_reasoning_field("reasoning_details")
        assert not is_reasoning_field('{"type":"reasoning.text"}')
        assert not is_reasoning_field("")

    def test_detail_type_guards(self):
        assert is_openai_reasoning_detail({"type": "reasoning.text", "text": "t"})
        assert is_openai_reasoning_detail({"type": "reasoning.summary", "summary": "s"})
        assert is_openai_reasoning_detail({"type": "reasoning.encrypted", "data": "d"})
        assert not is_openai_reasoning_detail({"type": "reasoning.text"})
        assert not is_openai_reasoning_detail({"type": "other", "text": "t"})
        assert not is_openai_reasoning_detail("not-a-dict")

    def test_append_concatenates_same_type_deltas(self):
        """相邻同类型 text/summary delta 拼接为逻辑条目（OpenRouter 流式形态）。"""
        details = []
        append_openai_reasoning_detail(
            details, {"type": "reasoning.text", "text": "Hel", "id": "r1"}
        )
        append_openai_reasoning_detail(
            details, {"type": "reasoning.text", "text": "lo", "id": "r1"}
        )
        append_openai_reasoning_detail(
            details, {"type": "reasoning.summary", "summary": "sum", "id": "r1"}
        )
        assert len(details) == 2
        assert details[0]["text"] == "Hello"
        assert details[1]["summary"] == "sum"

    def test_append_encrypted_stays_discrete(self):
        details = []
        encrypted = {"type": "reasoning.encrypted", "id": "e1", "data": "x"}
        append_openai_reasoning_detail(details, dict(encrypted))
        append_openai_reasoning_detail(details, dict(encrypted))
        assert len(details) == 2

    def test_parse_details_roundtrip_and_rejects_garbage(self):
        details = [{"type": "reasoning.text", "text": "t"}]
        signature = json.dumps(details)
        assert parse_openai_reasoning_details(signature) == details
        assert parse_openai_reasoning_details("not json") is None
        assert parse_openai_reasoning_details(json.dumps({"bad": 1})) is None
        assert parse_openai_reasoning_details(None) is None

    def test_parse_legacy_encrypted_detail(self):
        legacy = {"type": "reasoning.encrypted", "id": "tc1", "data": "secret"}
        assert parse_legacy_encrypted_reasoning_detail(json.dumps(legacy)) == legacy
        assert (
            parse_legacy_encrypted_reasoning_detail(
                json.dumps({"type": "reasoning.text", "text": "t"})
            )
            is None
        )

    def test_replay_prefers_signed_details_with_legacy_fallback(self):
        """回放：thinking 签名优先；无签名时兜底读 toolCall 旧挂载。"""
        model = _model()
        signed_block = ThinkingContent(
            type="thinking",
            thinking="because",
            thinking_signature=json.dumps(
                [{"type": "reasoning.text", "text": "because"}]
            ),
        )
        legacy_call = ToolCall(
            type="toolCall",
            id="tc1",
            name="f",
            arguments={},
            thought_signature=json.dumps(
                {"type": "reasoning.encrypted", "id": "tc1", "data": "secret"}
            ),
        )
        same_model_msg = AssistantMessage(
            role="assistant",
            api=model.api,
            provider=model.provider,
            model=model.id,
            content=[signed_block, legacy_call],
        )
        ctx = Context(messages=[UserMessage(content="hi"), same_model_msg])
        params = build_params(model, ctx)
        assistant = next(m for m in params["messages"] if m.get("role") == "assistant")
        assert assistant["reasoning_details"] == [
            {"type": "reasoning.text", "text": "because"}
        ]
        assert "reasoning" not in assistant
        assert "reasoning_content" not in assistant

    def test_signature_whitelist_guard_blocks_arbitrary_field(self):
        """白名单护栏：非法签名值绝不作为请求体字段名发送。"""
        model = _model()
        bad_block = ThinkingContent(
            type="thinking",
            thinking="because",
            thinking_signature='[{"type": "reasoning.text"}]',
        )
        ctx = Context(
            messages=[
                UserMessage(content="hi"),
                AssistantMessage(
                    role="assistant",
                    api=KnownApi.OPENAI_COMPLETIONS,
                    provider=KnownProvider.OPENAI,
                    model="test-model",
                    content=[TextContent(type="text", text="ok"), bad_block],
                ),
            ]
        )
        params = build_params(model, ctx)
        assistant = next(m for m in params["messages"] if m.get("role") == "assistant")
        assert all(
            key
            in (
                "role",
                "content",
                "tool_calls",
                "reasoning_details",
                "reasoning",
                "reasoning_content",
                "reasoning_text",
            )
            for key in assistant
        )


def test_reasoning_detail_merge_does_not_invent_null_fields():
    details = [{"type": "reasoning.text", "text": "a"}]
    append_openai_reasoning_detail(details, {"type": "reasoning.text", "text": "b"})
    assert details == [{"type": "reasoning.text", "text": "ab"}]
