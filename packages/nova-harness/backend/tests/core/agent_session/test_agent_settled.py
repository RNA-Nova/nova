"""agent_settled 事件测试（对齐 pi ``_emitAgentSettled``）。

run 终结（含续话 drain）后在 finally 中双发：Bus 2（``_emit``）与扩展面
（runner）。正常结束、异常路径均发射；无 runner 时仅 Bus 2。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nova_harness.core import AgentSession
from nova_harness.core.extensions.runner import ExtensionRunner
from nova_harness.core.types.events.constants import AGENT_SETTLED
from nova_harness.core.types.extensions import Extension, ExtensionRuntime
from nova_harness.core.types.session.config import AgentSessionConfig


def _minimal_runtime() -> ExtensionRuntime:
    runtime = ExtensionRuntime(cwd="/tmp")
    for name in (
        "send_message",
        "send_user_message",
        "exec",
        "append_entry",
        "set_session_name",
        "get_session_name",
        "set_label",
        "get_active_tools",
        "get_all_tools",
        "set_active_tools",
        "refresh_tools",
        "get_commands",
        "set_model",
        "get_thinking_level",
        "set_thinking_level",
    ):
        setattr(runtime, name, lambda *args, **kwargs: None)
    runtime.context_actions = SimpleNamespace(
        get_model=lambda: None,
        is_idle=lambda: True,
        is_project_trusted=lambda: True,
        get_signal=lambda: None,
        abort=lambda: None,
        has_pending_messages=lambda: False,
        shutdown=lambda: None,
        get_context_usage=lambda: None,
        compact=lambda: None,
        get_system_prompt=lambda: "",
        get_system_prompt_options=lambda: {},
        get_personas=lambda: [],
        get_persona_override=lambda: None,
        set_persona_override=lambda name: None,
        clear_persona_override=lambda: None,
        get_agents=lambda: [],
        change_agent=lambda name: None,
        save_agent=lambda as_name=None: None,
        get_executor_settings=lambda: None,
        register_executor_endpoint=lambda *args: None,
        unregister_executor_endpoint=lambda *args: False,
        refresh_system_prompt=lambda: None,
    )
    return runtime


def _make_config() -> AgentSessionConfig:
    agent = MagicMock()
    agent.state.messages = []
    agent.state.is_streaming = False
    agent.prompt = AsyncMock()
    agent.continue_ = AsyncMock()
    agent.has_queued_messages = MagicMock(return_value=False)
    return AgentSessionConfig(
        agent=agent,
        session_manager=MagicMock(),
        settings_manager=MagicMock(),
        cwd="/tmp",
        system_prompt_manager=MagicMock(),
        tools_manager=MagicMock(),
        resource_loader=MagicMock(),
        model_runtime=MagicMock(),
        scoped_models=[],
        initial_active_tool_names=[],
        base_tools_override=None,
        extension_runner_ref=None,
        session_start_event=None,
    )


def _runner_with_settled_handler(received):
    def handler(event, ctx):
        received.append(event)

    ext = Extension(path="watcher", handlers={AGENT_SETTLED: [handler]})
    return ExtensionRunner(
        extensions=[ext],
        runtime=_minimal_runtime(),
        cwd="/tmp",
        session_manager=None,
        model_runtime=None,
    )


@pytest.mark.asyncio
async def test_settled_fires_after_run_on_both_channels():
    received = []
    session = AgentSession(_make_config())
    session._extension_runner = _runner_with_settled_handler(received)
    bus_events = []
    session._emit = bus_events.append

    await session._run_agent_prompt([])

    assert [e.type for e in received] == [AGENT_SETTLED]
    assert [e.type for e in bus_events] == [AGENT_SETTLED]
    # run 无续话：continue_ 不应被调用
    session.agent.continue_.assert_not_called()


@pytest.mark.asyncio
async def test_settled_fires_after_prompt_completes():
    """时序：settled 在 agent.prompt 完成之后发射。"""
    order = []
    session = AgentSession(_make_config())
    session.agent.prompt = AsyncMock(side_effect=lambda *a: order.append("prompt"))

    def handler(event, ctx):
        order.append(event.type)

    ext = Extension(path="watcher", handlers={AGENT_SETTLED: [handler]})
    session._extension_runner = ExtensionRunner(
        extensions=[ext],
        runtime=_minimal_runtime(),
        cwd="/tmp",
        session_manager=None,
        model_runtime=None,
    )
    session._emit = lambda e: None

    await session._run_agent_prompt([])
    assert order == ["prompt", AGENT_SETTLED]


@pytest.mark.asyncio
async def test_settled_fires_on_exception():
    """异常路径（含 abort/错误）：finally 保证 settled 照常双发。"""
    received = []
    session = AgentSession(_make_config())
    session.agent.prompt = AsyncMock(side_effect=RuntimeError("boom"))
    session._extension_runner = _runner_with_settled_handler(received)
    bus_events = []
    session._emit = bus_events.append

    with pytest.raises(RuntimeError):
        await session._run_agent_prompt([])

    assert [e.type for e in received] == [AGENT_SETTLED]
    assert [e.type for e in bus_events] == [AGENT_SETTLED]


@pytest.mark.asyncio
async def test_settled_without_runner_bus_only():
    session = AgentSession(_make_config())
    session._extension_runner = None
    bus_events = []
    session._emit = bus_events.append

    await session._run_agent_prompt([])
    assert [e.type for e in bus_events] == [AGENT_SETTLED]
