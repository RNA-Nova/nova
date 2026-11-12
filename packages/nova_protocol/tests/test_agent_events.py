"""WarningEvent（agent_events.py）测试——默认值/roundtrip/线形态。"""

import pytest
from nova_protocol import WarningEvent, WarningKind


class TestWarningEvent:
    def test_defaults(self):
        event = WarningEvent(message="x")
        assert event.type == "warning"
        assert event.kind is WarningKind.GENERAL

    def test_roundtrip(self):
        event = WarningEvent(
            kind=WarningKind.CAPABILITY, message="fd 缺失，brew install fd"
        )
        restored = WarningEvent.model_validate(event.model_dump())
        assert restored == event
        # 内存中保持枚举成员（不依赖 use_enum_values）
        assert restored.kind is WarningKind.CAPABILITY

    def test_dump_wire(self):
        event = WarningEvent(kind=WarningKind.CONFIG, message="配置已迁移")
        assert event.dump_wire() == {
            "type": "warning",
            "kind": "config",
            "message": "配置已迁移",
        }


def test_agent_event_nested_messages_use_the_selected_serialization_profile():
    from nova_protocol import AgentEndEvent, ToolResultMessage

    event = AgentEndEvent(
        messages=[ToolResultMessage(tool_call_id="call-1", content=[], is_error=True)]
    )
    persisted = event.model_dump()["messages"][0]
    wire = event.dump_wire()["messages"][0]
    assert persisted["tool_call_id"] == "call-1"
    assert persisted["is_error"] is True
    assert "toolCallId" not in persisted
    assert wire["toolCallId"] == "call-1"
    assert wire["isError"] is True


def test_agent_event_nested_serialization_propagates_options():
    from nova_protocol import (
        AgentEndEvent,
        AssistantMessage,
        StopReason,
        ToolResultMessage,
    )

    event = AgentEndEvent(
        messages=[AssistantMessage(), ToolResultMessage(content=[], details=None)]
    )
    for dumped in (
        event.model_dump(exclude_none=True),
        event.dump_wire(exclude_none=True),
    ):
        tool_result = dumped["messages"][1]
        assert "details" not in tool_result
    python_message = event.model_dump(mode="python")["messages"][0]
    assert python_message["stop_reason"] is StopReason.STOP


def _agent_event_members():
    from nova_protocol import (
        AgentEndEvent,
        AgentStartEvent,
        AssistantMessage,
        MessageEndEvent,
        MessageStartEvent,
        MessageUpdateEvent,
        StartEvent,
        ToolExecutionEndEvent,
        ToolExecutionStartEvent,
        ToolExecutionUpdateEvent,
        TurnEndEvent,
        TurnStartEvent,
    )

    message = AssistantMessage()
    return [
        AgentStartEvent(),
        AgentEndEvent(messages=[]),
        TurnStartEvent(),
        TurnEndEvent(message=message, tool_results=[]),
        MessageStartEvent(message=message),
        MessageUpdateEvent(
            message=message, assistant_message_event=StartEvent(partial=message)
        ),
        MessageEndEvent(message=message),
        ToolExecutionStartEvent(tool_call_id="t", tool_name="tool", args={}),
        ToolExecutionUpdateEvent(
            tool_call_id="t", tool_name="tool", args={}, partial_result={}
        ),
        ToolExecutionEndEvent(
            tool_call_id="t", tool_name="tool", result={}, is_error=False
        ),
        WarningEvent(message="warning"),
    ]


@pytest.mark.parametrize("event", _agent_event_members())
def test_agent_event_union_roundtrip_covers_every_member(event):
    from nova_protocol import AgentEvent
    from pydantic import TypeAdapter

    adapter = TypeAdapter(AgentEvent)
    rebuilt = adapter.validate_python(event.model_dump())
    assert rebuilt == event
    assert type(rebuilt) is type(event)
    assert adapter.validate_python(event.dump_wire()) == event


def test_custom_agent_message_survives_event_and_union_roundtrip():
    from nova_protocol import AgentEndEvent, AgentMessage, CustomAgentMessage
    from pydantic import TypeAdapter

    class PackageMessage(CustomAgentMessage):
        role: str = "package.event"
        payload: str = "kept"
        optional_value: str | None = None

    event = AgentEndEvent(messages=[PackageMessage()])
    dumped = event.model_dump(exclude_none=True)
    message = dumped["messages"][0]
    assert message["payload"] == "kept"
    assert "optional_value" not in message
    restored = TypeAdapter(AgentMessage).validate_python(message)
    assert isinstance(restored, CustomAgentMessage)
    assert restored.model_dump()["payload"] == "kept"


def test_agent_event_nested_include_exclude_and_context_match_direct_messages():
    from nova_protocol import AgentEndEvent, CustomAgentMessage, ToolResultMessage
    from pydantic import field_serializer

    event = AgentEndEvent(
        messages=[ToolResultMessage(tool_name="tool", details={"x": 1}, content=[])]
    )
    excluded = event.model_dump(
        exclude={"messages": {"__all__": {"details", "tool_name"}}}
    )["messages"][0]
    assert "details" not in excluded
    assert "tool_name" not in excluded
    included = event.model_dump(include={"messages": {"__all__": {"role", "details"}}})[
        "messages"
    ][0]
    assert set(included) == {"role", "details"}

    class ContextMessage(CustomAgentMessage):
        value: str = "default"

        @field_serializer("value")
        def _serialize_value(self, _value, info):
            prefix = (info.context or {}).get("prefix", "missing")
            return f"{prefix}:{self.value}"

    direct = ContextMessage().model_dump(context={"prefix": "ok"})
    nested = AgentEndEvent(messages=[ContextMessage()]).model_dump(
        context={"prefix": "ok"}
    )["messages"][0]
    assert nested["value"] == direct["value"] == "ok:default"


def test_custom_agent_message_wire_and_unset_defaults_modes():
    from nova_protocol import AgentEndEvent, CustomAgentMessage

    class PackageMessage(CustomAgentMessage):
        role: str = "package.event"
        optional_value: str | None = None

    event = AgentEndEvent(messages=[PackageMessage()])
    wire = event.dump_wire()["messages"][0]
    assert wire["role"] == "package.event"
    assert "optionalValue" in wire
    assert "optionalValue" not in event.dump_wire(exclude_none=True)["messages"][0]
    assert event.model_dump(exclude_unset=True)["messages"][0] == {}
    assert event.model_dump(exclude_defaults=True)["messages"][0] == {}


def _custom_message_event_factories():
    from nova_protocol import (
        AgentEndEvent,
        AssistantMessage,
        CustomAgentMessage,
        MessageEndEvent,
        MessageStartEvent,
        MessageUpdateEvent,
        StartEvent,
        TurnEndEvent,
    )

    class PackageMessage(CustomAgentMessage):
        role: str = "package.event"
        payload: str = "kept"

    assistant = AssistantMessage()
    return [
        lambda: AgentEndEvent(messages=[PackageMessage()]),
        lambda: TurnEndEvent(message=PackageMessage(), tool_results=[]),
        lambda: MessageStartEvent(message=PackageMessage()),
        lambda: MessageUpdateEvent(
            message=PackageMessage(),
            assistant_message_event=StartEvent(partial=assistant),
        ),
        lambda: MessageEndEvent(message=PackageMessage()),
    ]


@pytest.mark.parametrize(
    "factory",
    _custom_message_event_factories(),
    ids=["messages", "turn_end", "message_start", "message_update", "message_end"],
)
def test_custom_agent_message_is_preserved_at_every_event_field(factory):
    event = factory()
    dumped = event.model_dump()
    message = dumped["messages"][0] if "messages" in dumped else dumped["message"]
    assert message["payload"] == "kept"
    wire = event.dump_wire()
    wire_message = wire["messages"][0] if "messages" in wire else wire["message"]
    assert wire_message["payload"] == "kept"


def test_dump_agent_message_direct_contract():
    from nova_protocol import CustomAgentMessage, StopReason, dump_agent_message

    class PackageMessage(CustomAgentMessage):
        role: str = "package.event"
        payload_value: str = "kept"
        optional_value: str | None = None
        stop_reason: StopReason = StopReason.STOP

    message = PackageMessage()
    persisted = dump_agent_message(message, wire=False, exclude_none=True)
    wire = dump_agent_message(message, wire=True, exclude_none=True)
    assert persisted == {
        "role": "package.event",
        "payload_value": "kept",
        "stop_reason": "stop",
    }
    assert wire == {
        "role": "package.event",
        "payloadValue": "kept",
        "stopReason": "stop",
    }
    python_dump = dump_agent_message(message, wire=False, mode="python")
    assert python_dump["stop_reason"] is StopReason.STOP
    assert dump_agent_message(message, wire=False, exclude_unset=True) == {}
    assert dump_agent_message(message, wire=False, exclude_defaults=True) == {}


def test_agent_event_reads_literal_historical_payloads():
    from nova_protocol import (
        AgentEndEvent,
        AssistantMessage,
        CustomAgentMessage,
        SystemMessage,
        ToolResultMessage,
        UserMessage,
    )

    event = AgentEndEvent.model_validate(
        {
            "type": "agent_end",
            "messages": [
                {
                    "role": "toolResult",
                    "toolCallId": "call-1",
                    "toolName": "legacy",
                    "content": [],
                    "isError": True,
                },
                {"role": "user", "content": "legacy question", "timestamp": 7},
                {
                    "role": "system",
                    "content": "legacy instruction",
                    "sections": {"rules": "kept", "obsolete": None},
                    "toolsAdded": [
                        {"name": "new_tool", "description": "new", "parameters": {}}
                    ],
                    "toolsRemoved": [{"name": "old_tool"}],
                    "timestamp": 8,
                },
                {"role": "package.unknown", "payload": {"kept": True}},
                {"role": "assistant", "content": [], "stopReason": "error"},
            ],
        }
    )
    tool_result, user, system, custom, assistant = event.messages
    assert isinstance(tool_result, ToolResultMessage)
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.is_error is True
    assert isinstance(user, UserMessage)
    assert user.content == "legacy question"
    assert isinstance(system, SystemMessage)
    assert system.sections == {"rules": "kept", "obsolete": None}
    assert system.tools_added is not None
    assert system.tools_added[0].name == "new_tool"
    assert system.tools_removed is not None
    assert system.tools_removed[0].name == "old_tool"
    assert isinstance(custom, CustomAgentMessage)
    assert custom.model_dump()["payload"] == {"kept": True}
    assert isinstance(assistant, AssistantMessage)
    assert assistant.error_kind is None
    assert assistant.retry_after_ms is None
    assert event.model_dump()["messages"][0]["tool_call_id"] == "call-1"


@pytest.mark.parametrize(
    "message",
    [
        {"role": "assistant", "content": [], "stopReason": "bogus"},
        {"role": "user", "content": [{"type": "unknown"}]},
        {"role": "toolResult", "content": [{"type": "unknown"}]},
        {"role": "system", "content": [], "sections": "not-a-map"},
    ],
)
def test_known_agent_message_roles_do_not_fall_back_when_malformed(message):
    from nova_protocol import AgentMessage
    from pydantic import TypeAdapter

    with pytest.raises(ValueError):
        TypeAdapter(AgentMessage).validate_python(message)


def test_custom_message_model_cannot_impersonate_known_framework_role():
    from nova_protocol import CustomAgentMessage

    class InvalidAssistant(CustomAgentMessage):
        role: str = "assistant"
        payload: str = "not-an-assistant-contract"

    with pytest.raises(ValueError, match="reserved"):
        InvalidAssistant()


def test_specialized_tool_result_preserves_details_subclass_fields():
    """序列化保留实际子类字段；反序列化类型仍由显式泛型参数决定。"""
    from nova_protocol import AgentToolResult, NovaBaseModel

    class Details(NovaBaseModel):
        base: int = 1

    class ChildDetails(Details):
        child_value: str = "kept"

    result = AgentToolResult[Details](content=[], details=ChildDetails())
    dumped = result.model_dump()
    assert dumped["details"] == {"base": 1, "child_value": "kept"}
    assert AgentToolResult[Details].model_validate(dumped).details == Details()
    assert (
        AgentToolResult[ChildDetails].model_validate(dumped).details == ChildDetails()
    )
