"""判别联合的 roundtrip 契约（data-modeling §十/规则 6 的机械执法）。

核心承诺：经基类/TypeAdapter 重建（`validate(dump(x))`）必须**整体相等且
类型保持**——`model_validate` 按基类重建剥掉子类字段是这类代码的典型
静默腐坏（`SerializeAsAny` 只管序列化方向，校验方向必须显式可判别）。
"""

import pytest
from nova_protocol import (
    AnthropicMessagesCompat,
    AssistantMessage,
    AssistantMessageEvent,
    ContentUnion,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Message,
    Model,
    ModelCost,
    NovaErrorKind,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    StartEvent,
    StopReason,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultMessage,
    UserMessage,
)
from pydantic import TypeAdapter

_message_adapter = TypeAdapter(Message)
_event_adapter = TypeAdapter(AssistantMessageEvent)
_content_adapter = TypeAdapter(ContentUnion)


def _assistant() -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="hi"), ThinkingContent(thinking="hmm")],
        api="openai-completions",
        provider="openai",
        model="gpt-4",
    )


class TestMessageUnion:
    """`Message`（判别键 role）三成员。"""

    def test_user_message_string_content(self):
        m = UserMessage(content="hello")
        rebuilt = _message_adapter.validate_python(m.model_dump())
        assert rebuilt == m
        assert isinstance(rebuilt, UserMessage)

    def test_user_message_list_content(self):
        m = UserMessage(
            content=[
                TextContent(text="a"),
                ImageContent(mime_type="image/png", data="xx"),
            ]
        )
        rebuilt = _message_adapter.validate_python(m.model_dump())
        assert rebuilt == m
        assert isinstance(rebuilt.content[1], ImageContent)

    def test_assistant_message_with_tool_call_and_error_fields(self):
        """assistant 携带 toolCall + 错误字段：重建后子类字段一个不剥。"""
        m = AssistantMessage(
            content=[ToolCall(id="t1", name="bash", arguments={"cmd": "ls"})],
            api="openai-completions",
            provider="openai",
            model="gpt-4",
            stop_reason=StopReason.ERROR,
            error_message="boom",
            error_kind=NovaErrorKind.RATE_LIMITED,
        )
        rebuilt = _message_adapter.validate_python(m.model_dump())
        assert rebuilt == m
        assert isinstance(rebuilt, AssistantMessage)
        assert rebuilt.error_kind == "rateLimited"

    def test_tool_result_message(self):
        m = ToolResultMessage(
            tool_call_id="t1",
            tool_name="bash",
            content=[TextContent(text="ok")],
            is_error=False,
        )
        rebuilt = _message_adapter.validate_python(m.model_dump())
        assert rebuilt == m
        assert isinstance(rebuilt, ToolResultMessage)


class TestAssistantMessageEventUnion:
    """`AssistantMessageEvent`（判别键 type）十二成员全覆盖。"""

    def _members(self):
        msg = _assistant()
        tc = ToolCall(id="t1", name="bash", arguments={"cmd": "ls"})
        return [
            StartEvent(partial=msg),
            TextStartEvent(content_index=0, partial=msg),
            TextDeltaEvent(content_index=0, delta="he", partial=msg),
            TextEndEvent(content_index=0, content="hello", partial=msg),
            ThinkingStartEvent(content_index=1, partial=msg),
            ThinkingDeltaEvent(content_index=1, delta="hm", partial=msg),
            ThinkingEndEvent(content_index=1, content="hmm", partial=msg),
            ToolCallStartEvent(content_index=2, partial=msg),
            ToolCallDeltaEvent(content_index=2, delta='{"cmd"', partial=msg),
            ToolCallEndEvent(content_index=2, tool_call=tc, partial=msg),
            DoneEvent(reason=StopReason.STOP, message=msg),
            ErrorEvent(reason="error", error=msg),
        ]

    def test_all_members_roundtrip(self):
        for member in self._members():
            rebuilt = _event_adapter.validate_python(member.model_dump())
            assert rebuilt == member, member.type
            assert type(rebuilt) is type(member)

    def test_error_event_aborted_variant(self):
        m = ErrorEvent(reason="aborted", error=_assistant())
        rebuilt = _event_adapter.validate_python(m.model_dump())
        assert rebuilt == m


class TestContentUnion:
    """`ContentUnion`（判别键 type）四成员。"""

    @pytest.mark.parametrize(
        "member",
        [
            TextContent(text="hi", text_signature="sig"),
            ThinkingContent(thinking="hmm", redacted=True),
            ToolCall(id="t1", name="bash", arguments={"cmd": "ls"}),
            ImageContent(mime_type="image/png", data="xx"),
        ],
    )
    def test_member_roundtrip(self, member):
        rebuilt = _content_adapter.validate_python(member.model_dump())
        assert rebuilt == member
        assert type(rebuilt) is type(member)


def _model_dict(api: str, compat: dict) -> dict:
    return {
        "id": "m1",
        "name": "m1",
        "api": api,
        "provider": "openai",
        "base_url": "http://x",
        "reasoning": True,
        "input_types": ["text"],
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 8192,
        "max_tokens": 4096,
        "compat": compat,
    }


class TestModelCompatDiscrimination:
    """`Model.compat` 按 api 判别的自定义 before-validator——全仓最易静默
    腐坏的一段判别代码：roundtrip 后 compat 子类必须保持。"""

    def test_completions_compat_roundtrip(self):
        model = Model.model_validate(
            _model_dict("openai-completions", {"supports_store": False})
        )
        rebuilt = Model.model_validate(model.model_dump())
        assert isinstance(rebuilt.compat, OpenAICompletionsCompat)
        assert rebuilt.compat.supports_store is False
        assert rebuilt == model

    def test_responses_compat_roundtrip(self):
        """同名字段 + responses 独有字段：判别不滑向 completions。"""
        model = Model.model_validate(
            _model_dict(
                "openai-responses",
                {"supports_tool_search": True, "supports_developer_role": False},
            )
        )
        rebuilt = Model.model_validate(model.model_dump())
        assert isinstance(rebuilt.compat, OpenAIResponsesCompat)
        assert rebuilt.compat.supports_tool_search is True
        assert rebuilt == model

    def test_anthropic_compat_roundtrip(self):
        model = Model.model_validate(
            _model_dict("anthropic-messages", {"supports_temperature": False})
        )
        rebuilt = Model.model_validate(model.model_dump())
        assert isinstance(rebuilt.compat, AnthropicMessagesCompat)
        assert rebuilt == model

    def test_default_compat_survives_roundtrip(self):
        """未显式给 compat 时 Model 自动补默认实例——roundtrip 后仍在。"""
        model = Model.model_validate(
            {
                k: v
                for k, v in _model_dict("openai-completions", {}).items()
                if k != "compat"
            }
        )
        assert model.compat is not None
        rebuilt = Model.model_validate(model.model_dump())
        assert rebuilt.compat == model.compat

    @pytest.mark.parametrize(
        "api", ["openai-responses", "azure-openai-responses", "openai-codex-responses"]
    )
    @pytest.mark.parametrize("compat", [{}, {"supports_developer_role": False}])
    def test_all_responses_variants_share_explicit_compat_discrimination(
        self, api, compat
    ):
        model = Model.model_validate(_model_dict(api, compat))
        assert isinstance(model.compat, OpenAIResponsesCompat)
        rebuilt = Model.model_validate(model.model_dump())
        assert isinstance(rebuilt.compat, OpenAIResponsesCompat)
        assert rebuilt == model
