"""
CommandDispatcher 单元测试。

验证 slash 命令分发的基本分支与错误处理。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nova_harness.core.types.events import ExtensionErrorEvent


@pytest.fixture
def cmd_session(make_agent_session):
    """构造一个带 mock extension runner 的 session。"""
    sess = make_agent_session()
    sess._extension_runner = MagicMock()
    return sess


@pytest.mark.asyncio
async def test_try_execute_no_runner(make_agent_session):
    """没有 extension runner 时不应处理任何命令。"""
    sess = make_agent_session()
    sess._extension_runner = None
    assert await sess._commands.try_execute("/foo") is False


@pytest.mark.asyncio
async def test_try_execute_unknown_command(cmd_session):
    """未知命令返回 False。"""
    cmd_session._extension_runner.get_command.return_value = None
    assert await cmd_session._commands.try_execute("/unknown arg") is False


@pytest.mark.asyncio
async def test_try_execute_known_command_without_args(cmd_session):
    """无参数命令应正确调用 handler 并返回 True。"""
    command = MagicMock()
    command.handler = AsyncMock()
    cmd_session._extension_runner.get_command.return_value = command

    assert await cmd_session._commands.try_execute("/hello") is True
    command.handler.assert_awaited_once_with(
        "", cmd_session._extension_runner.create_command_context.return_value
    )


@pytest.mark.asyncio
async def test_try_execute_known_command_with_args(cmd_session):
    """有参数命令应把空格后内容作为参数。"""
    command = MagicMock()
    command.handler = AsyncMock()
    cmd_session._extension_runner.get_command.return_value = command

    assert await cmd_session._commands.try_execute("/hello world 123") is True
    command.handler.assert_awaited_once_with(
        "world 123", cmd_session._extension_runner.create_command_context.return_value
    )


@pytest.mark.asyncio
async def test_try_execute_handler_error_emits_extension_error(cmd_session):
    """handler 抛错时应 emit 错误事件并仍返回 True。"""
    command = MagicMock()
    command.handler = AsyncMock(side_effect=RuntimeError("boom"))
    cmd_session._extension_runner.get_command.return_value = command

    assert await cmd_session._commands.try_execute("/bad") is True
    cmd_session._extension_runner.emit_error.assert_called_once()
    event = cmd_session._extension_runner.emit_error.call_args[0][0]
    assert isinstance(event, ExtensionErrorEvent)
    assert event.extension_path == "command:bad"
    assert "boom" in event.error


def test_throw_if_extension_command_no_runner(make_agent_session):
    """没有 runner 时 throw_if_extension_command 不抛错。"""
    sess = make_agent_session()
    sess._extension_runner = None
    sess._commands.throw_if_extension_command("/any")  # 不应抛异常


def test_throw_if_extension_command_raises_for_registered(cmd_session):
    """已注册的扩展命令应触发 RuntimeError。"""
    cmd_session._extension_runner.get_command.return_value = MagicMock()
    with pytest.raises(RuntimeError, match='Extension command "/hello"'):
        cmd_session._commands.throw_if_extension_command("/hello")


def test_throw_if_extension_command_ignores_unknown(cmd_session):
    """未注册的 slash 文本不应抛错。"""
    cmd_session._extension_runner.get_command.return_value = None
    cmd_session._commands.throw_if_extension_command("/hello")
