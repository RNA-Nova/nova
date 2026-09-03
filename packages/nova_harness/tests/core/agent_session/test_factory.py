"""agent_session factory 的会话状态恢复/持久化测试。"""

from unittest.mock import MagicMock

from nova_ai import ModelThinkingLevel
from nova_harness.core.agent_session.factory import restore_or_persist_session_state


def _session_manager(messages=None, branch=None) -> MagicMock:
    session_manager = MagicMock()
    session_manager.build_session_context.return_value = MagicMock(
        messages=messages or []
    )
    session_manager.get_branch.return_value = branch or []
    return session_manager


def test_new_session_writes_model_and_thinking_entries():
    """新会话写入初始 model / thinking_level 条目。"""
    session_manager = _session_manager()
    agent = MagicMock()
    model = MagicMock()

    restore_or_persist_session_state(
        session_manager, agent, model=model, thinking_level=ModelThinkingLevel.HIGH
    )

    session_manager.append_model_change.assert_called_once_with(
        model.provider, model.id
    )
    session_manager.append_thinking_level_change.assert_called_once_with(
        ModelThinkingLevel.HIGH
    )


def test_new_session_skips_thinking_entry_when_level_none():
    """思考级别为 None 时不写 thinking_level_change 条目（避免 null 条目）。"""
    session_manager = _session_manager()
    agent = MagicMock()

    restore_or_persist_session_state(
        session_manager, agent, model=None, thinking_level=None
    )

    session_manager.append_model_change.assert_not_called()
    session_manager.append_thinking_level_change.assert_not_called()


def test_existing_session_restores_messages_and_persists_level():
    """已有会话恢复历史消息；无 thinking 条目时补写当前级别。"""
    message = MagicMock()
    session_manager = _session_manager(messages=[message])
    agent = MagicMock()

    restore_or_persist_session_state(
        session_manager, agent, model=None, thinking_level=ModelThinkingLevel.HIGH
    )

    assert agent.state.messages == [message]
    session_manager.append_thinking_level_change.assert_called_once_with(
        ModelThinkingLevel.HIGH
    )


def test_existing_session_with_thinking_entry_keeps_it():
    """已有 thinking 条目的会话不重复补写。"""
    entry = MagicMock(type="thinking_level_change")
    session_manager = _session_manager(messages=[MagicMock()], branch=[entry])
    agent = MagicMock()

    restore_or_persist_session_state(
        session_manager, agent, model=None, thinking_level=ModelThinkingLevel.HIGH
    )

    session_manager.append_thinking_level_change.assert_not_called()
