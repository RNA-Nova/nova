"""session_info_changed 扩展事件双发测试（对齐 pi setSessionName）。

改名事件要同时上两条总线：Bus 2（前端呈现）与扩展面（runner 分派）。
本测试钉住第二发：扩展 handler 收到事件且 name 字段正确；无 runner /
无 handler 时不报错。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nova_harness.core import AgentSession
from nova_harness.core.extensions.runner import ExtensionRunner
from nova_harness.core.types.events.constants import SESSION_INFO_CHANGED
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
    session_manager = MagicMock()
    # Pydantic 事件校验需要真实类型（name: str——MagicMock 返回值过不了校验）
    session_manager.get_session_name.return_value = "demo"
    return AgentSessionConfig(
        agent=agent,
        session_manager=session_manager,
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


@pytest.mark.asyncio
async def test_session_info_changed_reaches_extension():
    received = []

    def handler(event, ctx):
        received.append(event)

    ext = Extension(path="watcher", handlers={SESSION_INFO_CHANGED: [handler]})
    runner = ExtensionRunner(
        extensions=[ext],
        runtime=_minimal_runtime(),
        cwd="/tmp",
        session_manager=None,
        model_runtime=None,
    )

    session = AgentSession(_make_config())
    session._extension_runner = runner
    session.set_session_name("my-session")

    # set_session_name 是同步方法，runner.emit 经 create_task 调度，让出两拍
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0].type == SESSION_INFO_CHANGED


@pytest.mark.asyncio
async def test_session_info_changed_no_runner_no_error():
    session = AgentSession(_make_config())
    session._extension_runner = None
    session.set_session_name("quiet")  # 不报错即通过


@pytest.mark.asyncio
async def test_session_info_changed_no_handlers_no_task():
    runner = ExtensionRunner(
        extensions=[],
        runtime=_minimal_runtime(),
        cwd="/tmp",
        session_manager=None,
        model_runtime=None,
    )
    session = AgentSession(_make_config())
    session._extension_runner = runner

    session.set_session_name("quiet")
    # 无 handler：不调度任务，静默通过
