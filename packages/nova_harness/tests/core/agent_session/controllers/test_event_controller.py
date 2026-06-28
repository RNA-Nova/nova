"""
EventController 单元测试。

验证底层 Agent 事件的分发、扩展转发、消息替换与持久化。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nova_harness.core.types.events import (
    AGENT_END,
    AGENT_START,
    MESSAGE_END,
    MESSAGE_START,
    MESSAGE_UPDATE,
    TOOL_EXECUTION_END,
    TOOL_EXECUTION_START,
    TOOL_EXECUTION_UPDATE,
    TURN_END,
    TURN_START,
    AgentEndEvent,
    AgentStartEvent,
    AutoRetryEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    QueueUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)


@pytest.fixture
def event_session(make_agent_session):
    """构造一个带 mock extension runner 的 session。"""
    sess = make_agent_session()
    runner = MagicMock()
    runner.emit = AsyncMock()
    runner.emit_message_end = AsyncMock(return_value=None)
    sess._extension_runner = runner
    return sess


# ---------------------------------------------------------------------------
# handle 主逻辑
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_start_user_removes_steering(event_session):
    """用户 MESSAGE_START 应从 steering 队列移除对应文本并 emit 更新。"""
    event_session._steering_messages.append("go")
    events = []
    event_session.subscribe(events.append)

    msg = SimpleNamespace(role="user", content="go")
    await event_session._events.handle(SimpleNamespace(type=MESSAGE_START, message=msg))

    assert "go" not in event_session._steering_messages
    assert any(isinstance(e, QueueUpdateEvent) for e in events)


@pytest.mark.asyncio
async def test_handle_message_start_user_removes_follow_up(event_session):
    """用户 MESSAGE_START 应从 follow_up 队列移除对应文本。"""
    event_session._follow_up_messages.append("follow")
    await event_session._events.handle(
        SimpleNamespace(
            type=MESSAGE_START, message=SimpleNamespace(role="user", content="follow")
        )
    )
    assert "follow" not in event_session._follow_up_messages


@pytest.mark.asyncio
async def test_handle_agent_end_emits_with_will_retry(event_session):
    """AGENT_END 应补充 will_retry 后 emit。"""
    event_session._retry.will_retry_after_agent_end = MagicMock(return_value=True)
    events = []
    event_session.subscribe(events.append)

    await event_session._events.handle(SimpleNamespace(type=AGENT_END, messages=[]))

    emitted = [e for e in events if isinstance(e, AgentEndEvent)]
    assert len(emitted) == 1
    assert emitted[0].will_retry is True


@pytest.mark.asyncio
async def test_handle_message_end_persists_and_updates_last_assistant(event_session):
    """MESSAGE_END 应持久化消息并记录最后一条 assistant。"""
    msg = SimpleNamespace(role="assistant", stop_reason="stop")
    await event_session._events.handle(SimpleNamespace(type=MESSAGE_END, message=msg))

    assert event_session._last_assistant_message is msg
    event_session.session_manager.append_message.assert_called_once_with(msg)


@pytest.mark.asyncio
async def test_handle_message_end_resets_retry_attempt_on_success(event_session):
    """成功 assistant 消息应重置 retry_attempt 并 emit AutoRetryEndEvent。"""
    event_session._retry_attempt = 2
    msg = SimpleNamespace(role="assistant", stop_reason="stop")
    events = []
    event_session.subscribe(events.append)

    await event_session._events.handle(SimpleNamespace(type=MESSAGE_END, message=msg))

    assert event_session._retry_attempt == 0
    retry_events = [e for e in events if isinstance(e, AutoRetryEndEvent)]
    assert len(retry_events) == 1
    assert retry_events[0].success is True


@pytest.mark.asyncio
async def test_handle_message_end_error_stop_reason_does_not_emit_retry_success(
    event_session,
):
    """stop_reason=error 的 assistant 消息不应触发成功重试事件。"""
    event_session._retry_attempt = 1
    msg = SimpleNamespace(role="assistant", stop_reason="error")
    events = []
    event_session.subscribe(events.append)

    await event_session._events.handle(SimpleNamespace(type=MESSAGE_END, message=msg))

    assert event_session._retry_attempt == 1
    assert not any(isinstance(e, AutoRetryEndEvent) for e in events)


# ---------------------------------------------------------------------------
# forward_to_runner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_to_runner_no_runner(make_agent_session):
    """runner 为 None 时直接返回。"""
    sess = make_agent_session()
    sess._extension_runner = None
    await sess._events.forward_to_runner(SimpleNamespace(type=AGENT_START))


@pytest.mark.asyncio
async def test_forward_to_runner_maps_agent_start(event_session):
    """AGENT_START 应映射为 AgentStartEvent。"""
    await event_session._events.forward_to_runner(SimpleNamespace(type=AGENT_START))
    event_session._extension_runner.emit.assert_awaited_once()
    event = event_session._extension_runner.emit.call_args[0][0]
    assert isinstance(event, AgentStartEvent)


@pytest.mark.asyncio
async def test_forward_to_runner_maps_turn_events(event_session):
    """TURN_START / TURN_END 应正确映射。"""
    await event_session._events.forward_to_runner(
        SimpleNamespace(type=TURN_START, turn_index=3)
    )
    ev = event_session._extension_runner.emit.call_args[0][0]
    assert isinstance(ev, TurnStartEvent)
    assert ev.turn_index == 3

    await event_session._events.forward_to_runner(
        SimpleNamespace(type=TURN_END, turn_index=4, message=None, tool_results=[])
    )
    ev = event_session._extension_runner.emit.call_args[0][0]
    assert isinstance(ev, TurnEndEvent)


@pytest.mark.asyncio
async def test_forward_to_runner_maps_message_start_and_update(event_session):
    """MESSAGE_START / UPDATE 应通过 runner.emit 映射。"""
    msg = SimpleNamespace(role="user")
    await event_session._events.forward_to_runner(
        SimpleNamespace(type=MESSAGE_START, message=msg)
    )
    ev = event_session._extension_runner.emit.call_args[0][0]
    assert isinstance(ev, MessageStartEvent)

    await event_session._events.forward_to_runner(
        SimpleNamespace(type=MESSAGE_UPDATE, message=msg, assistant_message_event=None)
    )
    ev = event_session._extension_runner.emit.call_args[0][0]
    assert isinstance(ev, MessageUpdateEvent)


@pytest.mark.asyncio
async def test_forward_to_runner_message_end_uses_emit_message_end(event_session):
    """MESSAGE_END 应通过 runner.emit_message_end 分发。"""
    msg = SimpleNamespace(role="user")
    await event_session._events.forward_to_runner(
        SimpleNamespace(type=MESSAGE_END, message=msg)
    )
    event_session._extension_runner.emit_message_end.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_forward_to_runner_maps_tool_events(event_session):
    """工具执行事件应正确映射。"""
    await event_session._events.forward_to_runner(
        SimpleNamespace(
            type=TOOL_EXECUTION_START,
            tool_call_id="1",
            tool_name="bash",
            args={},
        )
    )
    ev = event_session._extension_runner.emit.call_args[0][0]
    assert isinstance(ev, ToolExecutionStartEvent)

    await event_session._events.forward_to_runner(
        SimpleNamespace(
            type=TOOL_EXECUTION_UPDATE,
            tool_call_id="1",
            tool_name="bash",
            args={},
            partial_result="x",
        )
    )
    ev = event_session._extension_runner.emit.call_args[0][0]
    assert isinstance(ev, ToolExecutionUpdateEvent)

    await event_session._events.forward_to_runner(
        SimpleNamespace(
            type=TOOL_EXECUTION_END,
            tool_call_id="1",
            tool_name="bash",
            result="ok",
            is_error=False,
        )
    )
    ev = event_session._extension_runner.emit.call_args[0][0]
    assert isinstance(ev, ToolExecutionEndEvent)


@pytest.mark.asyncio
async def test_forward_to_runner_message_end_replacement(event_session):
    """runner 返回合法 replacement 时应原地替换消息。"""
    original = SimpleNamespace(role="assistant", old_attr=1)
    replacement = SimpleNamespace(role="assistant", new_attr=2)
    event_session._extension_runner.emit_message_end = AsyncMock(
        return_value=replacement
    )

    await event_session._events.forward_to_runner(
        SimpleNamespace(type=MESSAGE_END, message=original)
    )

    assert original.new_attr == 2
    assert not hasattr(original, "old_attr")


@pytest.mark.asyncio
async def test_forward_to_runner_message_end_same_object_skips_replacement(
    event_session,
):
    """replacement 与 original 为同一对象时不替换。"""
    original = SimpleNamespace(role="assistant", old_attr=1)
    event_session._extension_runner.emit_message_end = AsyncMock(return_value=original)

    await event_session._events.forward_to_runner(
        SimpleNamespace(type=MESSAGE_END, message=original)
    )

    assert original.old_attr == 1


# ---------------------------------------------------------------------------
# replace_message_in_place
# ---------------------------------------------------------------------------


def test_replace_message_in_place_updates_dict(event_session):
    """replace_message_in_place 应替换 target 的 __dict__。"""
    target = SimpleNamespace(a=1, b=2)
    replacement = SimpleNamespace(c=3)
    event_session._events.replace_message_in_place(target, replacement)
    assert target.c == 3
    assert not hasattr(target, "a")


def test_replace_message_in_place_same_object(event_session):
    """target 与 replacement 相同时不操作。"""
    target = SimpleNamespace(a=1)
    event_session._events.replace_message_in_place(target, target)
    assert target.a == 1


# ---------------------------------------------------------------------------
# persist_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_message_custom_role(event_session):
    """custom 角色应调用 append_custom_message_entry。"""
    msg = SimpleNamespace(
        role="custom",
        custom_type="note",
        content="hello",
        display=True,
        details=None,
    )
    await event_session._events.persist_message(msg)
    event_session.session_manager.append_custom_message_entry.assert_called_once_with(
        "note", "hello", True, None
    )


@pytest.mark.asyncio
async def test_persist_message_user_role(event_session):
    """user 角色应调用 append_message。"""
    msg = SimpleNamespace(role="user")
    await event_session._events.persist_message(msg)
    event_session.session_manager.append_message.assert_called_once_with(msg)


@pytest.mark.asyncio
async def test_persist_message_other_role_ignored(event_session):
    """未知角色不应触发持久化。"""
    msg = SimpleNamespace(role="unknown")
    await event_session._events.persist_message(msg)
    event_session.session_manager.append_message.assert_not_called()
    event_session.session_manager.append_custom_message_entry.assert_not_called()
