"""
BashController 单元测试。

验证 bash 命令执行、结果记录、pending 消息刷新与取消逻辑。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_harness.core.types.messages import BashExecutionMessage
from nova_harness.core.utils.bash import BashResult


@pytest.fixture
def bash_session(make_agent_session):
    """构造一个用于测试 BashController 的 AgentSession。"""
    return make_agent_session()


def test_is_running_and_pending(bash_session):
    """is_running 与 has_pending_messages 应反映内部状态。"""
    assert bash_session._bash.is_running is False
    assert bash_session._bash.has_pending_messages is False

    bash_session._bash_abort_event = asyncio.Event()
    bash_session._pending_bash_messages.append(
        BashExecutionMessage(
            command="echo hi",
            output="hi",
            exit_code=0,
            cancelled=False,
            truncated=False,
        )
    )

    assert bash_session._bash.is_running is True
    assert bash_session._bash.has_pending_messages is True


@pytest.mark.asyncio
async def test_execute_bash_records_directly_when_not_streaming(bash_session):
    """非流式模式下 execute_bash 应直接把结果写入 agent state 与 session_manager。"""
    result = BashResult(output="hi\n", exit_code=0)
    operations = MagicMock()
    operations.execute = AsyncMock(return_value=result)

    with patch(
        "nova_harness.core.agent_session.controllers.bash.execute_bash",
        AsyncMock(return_value=result),
    ) as mock_exec:
        res = await bash_session._bash.execute_bash(
            "echo hi", options={"operations": operations}
        )

    assert res is result
    mock_exec.assert_awaited_once()
    assert len(bash_session.agent.state.messages) == 1
    bash_session.session_manager.append_message.assert_called_once()
    recorded = bash_session.agent.state.messages[0]
    assert recorded.role == "bashExecution"
    assert recorded.command == "echo hi"


@pytest.mark.asyncio
async def test_execute_bash_appends_to_pending_when_streaming(bash_session):
    """流式模式下 execute_bash 应把结果暂存到 pending_bash_messages。"""
    bash_session.agent.state.is_streaming = True
    result = BashResult(output="stream\n", exit_code=0)
    operations = MagicMock()
    operations.execute = AsyncMock(return_value=result)

    with patch(
        "nova_harness.core.agent_session.controllers.bash.execute_bash",
        AsyncMock(return_value=result),
    ):
        await bash_session._bash.execute_bash(
            "echo stream", options={"operations": operations}
        )

    assert len(bash_session._pending_bash_messages) == 1
    assert bash_session.agent.state.messages == []
    assert bash_session.session_manager.append_message.called is False


@pytest.mark.asyncio
async def test_execute_bash_applies_shell_prefix_and_path(bash_session):
    """execute_bash 应读取 settings_manager 中的 prefix 与 shell_path。"""
    bash_session.settings_manager.get_shell_command_prefix.return_value = "set -e"
    bash_session.settings_manager.get_shell_path.return_value = "/bin/zsh"
    result = BashResult(output="", exit_code=0)
    operations = MagicMock()
    operations.execute = AsyncMock(return_value=result)

    with patch(
        "nova_harness.core.agent_session.controllers.bash.execute_bash",
        AsyncMock(return_value=result),
    ) as mock_exec:
        await bash_session._bash.execute_bash(
            "echo hi", options={"operations": operations}
        )

    resolved_command = mock_exec.call_args[0][0]
    assert resolved_command == "set -e\necho hi"


def test_record_bash_result_respects_exclude_from_context(bash_session):
    """record_bash_result 应把 exclude_from_context 写入消息。"""
    result = BashResult(output="out", exit_code=1)
    bash_session._bash.record_bash_result(
        "cmd", result, options={"exclude_from_context": True}
    )
    recorded = bash_session.agent.state.messages[0]
    assert recorded.exclude_from_context is True
    bash_session.session_manager.append_message.assert_called_once()


def test_flush_pending_moves_messages_to_session(bash_session):
    """flush_pending 应把 pending bash 消息追加到会话并清空队列。"""
    msg = BashExecutionMessage(
        command="c", output="o", exit_code=0, cancelled=False, truncated=False
    )
    bash_session._pending_bash_messages.append(msg)

    bash_session._bash.flush_pending()

    assert bash_session.agent.state.messages == [msg]
    assert bash_session._pending_bash_messages == []
    assert bash_session.session_manager.append_message.call_count == 1


def test_flush_pending_empty_queue(bash_session):
    """pending 队列为空时 flush_pending 不应操作。"""
    bash_session._bash.flush_pending()
    assert bash_session.agent.state.messages == []
    assert bash_session.session_manager.append_message.called is False


def test_abort_bash_sets_event(bash_session):
    """abort_bash 应设置当前 abort event。"""
    event = asyncio.Event()
    bash_session._bash_abort_event = event
    bash_session._bash.abort_bash()
    assert event.is_set() is True
