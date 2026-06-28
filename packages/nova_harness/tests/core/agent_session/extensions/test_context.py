"""
ExtensionContext / ExtensionCommandContext 单元测试。

验证扩展在事件处理器中拿到的 ``ctx`` / ``cmdCtx`` 能够正确透 runner 的
属性、委托动作，并在命令上下文中调用 runtime 相关方法。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nova_harness.core.agent_session.extensions import ExtensionRunner
from nova_harness.core.agent_session.extensions.context import (
    ExtensionCommandContext,
    ExtensionContext,
)


@pytest.fixture
def services():
    """构造一个 mock 的 AgentSessionServices。"""
    return MagicMock(
        cwd="/tmp",
        session_manager=MagicMock(),
        settings_manager=MagicMock(),
        model_registry=MagicMock(),
        resource_loader=MagicMock(),
        system_prompt_manager=MagicMock(),
    )


@pytest.fixture
def runner(services):
    """构造一个空的 ExtensionRunner。"""
    return ExtensionRunner(services=services, extensions=[])


@pytest.fixture
def session():
    """构造一个 mock session。"""
    s = MagicMock()
    s.is_streaming = False
    s._steering_messages = []
    s._follow_up_messages = []
    s.get_context_usage.return_value = {"tokens": 5}
    s.compact = AsyncMock(return_value="compacted")
    s._base_system_prompt = "base-prompt"
    return s


# -----------------------------------------------------------------------------
# ExtensionContext 基础属性
# -----------------------------------------------------------------------------


def test_context_properties(runner):
    """ExtensionContext 应透传 runner 的基础属性。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    assert ctx.cwd == "/tmp"
    assert ctx.session_manager is runner.services.session_manager
    assert ctx.model_registry is runner.services.model_registry
    assert ctx.settings_manager is runner.services.settings_manager


def test_context_model_with_bound_session(runner):
    """绑定 session 后，ctx.model 应返回 session.model。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    assert ctx.model is None

    session = MagicMock(model="m1")
    runner.bind_session(session)
    assert ctx.model == "m1"


def test_context_signal_and_abort(runner):
    """ctx.signal 应返回构造时传入的 signal，并支持 abort。"""
    signal = MagicMock()
    ctx = ExtensionContext(runner=runner, _signal=signal)
    assert ctx.signal is signal
    ctx.abort()
    signal.set.assert_called_once()


def test_context_ui_and_mode(runner):
    """当前未接入 TUI 时，ui 为 None，mode 为 print，has_ui 为 False。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    assert ctx.ui is None
    assert ctx.mode == "print"
    assert ctx.has_ui is False


def test_context_is_project_trusted(runner):
    """is_project_trusted 默认返回 True。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    assert ctx.is_project_trusted() is True


# -----------------------------------------------------------------------------
# 动作委托
# -----------------------------------------------------------------------------


def test_context_is_idle(runner, session):
    """is_idle 应返回 session.is_streaming 的取反。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    runner.bind_session(session)
    session.is_streaming = False
    assert ctx.is_idle() is True
    session.is_streaming = True
    assert ctx.is_idle() is False


def test_context_has_pending_messages(runner, session):
    """has_pending_messages 应检测 steering / follow_up 消息队列。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    runner.bind_session(session)
    assert ctx.has_pending_messages() is False
    session._steering_messages = [MagicMock()]
    assert ctx.has_pending_messages() is True
    session._steering_messages = []
    session._follow_up_messages = [MagicMock()]
    assert ctx.has_pending_messages() is True


def test_context_get_context_usage(runner, session):
    """get_context_usage 应委托给 session.get_context_usage。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    runner.bind_session(session)
    assert ctx.get_context_usage() == {"tokens": 5}


async def test_context_compact_delegates(runner, session):
    """compact 应委托给 session.compact。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    runner.bind_session(session)
    result = await ctx.compact("instr")
    session.compact.assert_awaited_once_with("instr")
    assert result == "compacted"


def test_context_get_system_prompt(runner, session):
    """get_system_prompt 应返回 session._base_system_prompt。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    runner.bind_session(session)
    assert ctx.get_system_prompt() == "base-prompt"


def test_context_get_system_prompt_options(runner):
    """get_system_prompt_options 应返回包含 cwd 的字典。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    assert ctx.get_system_prompt_options() == {"cwd": "/tmp"}


# -----------------------------------------------------------------------------
# Flag 与生命周期
# -----------------------------------------------------------------------------


def test_context_flag_values(runner):
    """ExtensionContext 应委托 runner 读写 flag 值。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    ctx.set_flag_value("k", "v")
    assert ctx.get_flag_value("k") == "v"
    assert runner.get_flag_value("k") == "v"


def test_context_shutdown_invalidates_runner(runner):
    """shutdown 应使 runner 失效并清空事件总线。"""
    ctx = ExtensionContext(runner=runner, _signal=None)
    runner.event_bus.on("x", lambda: None)
    ctx.shutdown()
    assert runner._invalid is True
    with pytest.raises(RuntimeError, match="invalidated"):
        runner.bind_session(MagicMock())


# -----------------------------------------------------------------------------
# ExtensionCommandContext runtime 动作
# -----------------------------------------------------------------------------


@pytest.fixture
def runtime():
    """构造一个 mock runtime。"""
    r = MagicMock()
    r.new_session = AsyncMock()
    r.fork = AsyncMock()
    r.switch_session = AsyncMock()
    r.reload = AsyncMock()
    return r


async def test_command_context_new_session(runner, runtime):
    """new_session 应委托给 runtime.new_session。"""
    runner.bind_runtime(runtime)
    ctx = ExtensionCommandContext(runner=runner, _signal=None)
    await ctx.new_session({"name": "x"})
    runtime.new_session.assert_awaited_once_with({"name": "x"})


async def test_command_context_fork(runner, runtime):
    """fork 应委托给 runtime.fork。"""
    runner.bind_runtime(runtime)
    ctx = ExtensionCommandContext(runner=runner, _signal=None)
    await ctx.fork("entry-1")
    runtime.fork.assert_awaited_once_with("entry-1")


async def test_command_context_navigate_tree(runner, session, runtime):
    """navigate_tree 应委托给 session.navigate_tree。"""
    runner.bind_session(session)
    runner.bind_runtime(runtime)
    session.navigate_tree = AsyncMock()
    ctx = ExtensionCommandContext(runner=runner, _signal=None)
    await ctx.navigate_tree("target", {"opt": 1})
    session.navigate_tree.assert_awaited_once_with("target", {"opt": 1})


async def test_command_context_switch_session(runner, runtime):
    """switch_session 应委托给 runtime.switch_session。"""
    runner.bind_runtime(runtime)
    ctx = ExtensionCommandContext(runner=runner, _signal=None)
    await ctx.switch_session("/path/to/session")
    runtime.switch_session.assert_awaited_once_with("/path/to/session")


async def test_command_context_reload(runner, runtime):
    """reload 应委托给 runtime.reload。"""
    runner.bind_runtime(runtime)
    ctx = ExtensionCommandContext(runner=runner, _signal=None)
    await ctx.reload()
    runtime.reload.assert_awaited_once()


async def test_command_context_wait_for_idle(runner, session):
    """wait_for_idle 应委托给 session.agent.wait_for_idle。"""
    agent = MagicMock()
    agent.wait_for_idle = AsyncMock()
    session.agent = agent
    runner.bind_session(session)
    ctx = ExtensionCommandContext(runner=runner, _signal=None)
    await ctx.wait_for_idle()
    agent.wait_for_idle.assert_awaited_once()


async def test_command_context_requires_runtime(runner):
    """未绑定 runtime 时调用 runtime 动作应抛出 RuntimeError。"""
    ctx = ExtensionCommandContext(runner=runner, _signal=None)
    with pytest.raises(RuntimeError, match="before runtime bound"):
        await ctx.new_session()
