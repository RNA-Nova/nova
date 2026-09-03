"""tool_call 扩展事件的原地改参契约测试（对齐 pi 的 input 原地改参）。

扩展 handler 原地修改 ``ToolCallEvent.args``，修改应直送工具执行。
本测试钉住 harness 侧链路：``AgentSession`` 的 before_tool_call 闭包 →
``ToolCallEvent`` 构造 → ``ExtensionRunner`` 分发 → handler 的**引用同一性**
（改 event.args 即改调用方持有的同一 dict）；loop 侧（hook ctx.args →
execute）由 nova_agent 的 test_before_tool_call_args_mutation_flows_to_execution
钉住。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from nova_harness.core import AgentSession
from nova_harness.core.extensions.runner import ExtensionRunner
from nova_harness.core.types.events.constants import TOOL_CALL
from nova_harness.core.types.extensions import Extension, ExtensionRuntime
from nova_harness.core.types.session.config import AgentSessionConfig


def _minimal_runtime() -> ExtensionRuntime:
    """构造一个足够运行 create_context 的最小 runtime。"""
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


@pytest.mark.asyncio
async def test_tool_call_args_mutation_reaches_caller_dict():
    """扩展原地改 event.args → 调用方持有的同一 dict 被改（引用透传）。"""

    def mutate(event, ctx):
        event.args["command"] = f"sandbox-exec -- {event.args['command']}"
        return None

    ext = Extension(path="sandbox", handlers={TOOL_CALL: [mutate]})
    runner = ExtensionRunner(
        extensions=[ext],
        runtime=_minimal_runtime(),
        cwd="/tmp",
        session_manager=None,
        model_runtime=None,
    )

    session = AgentSession(_make_config())
    session._extension_runner = runner

    hook_ctx = SimpleNamespace(
        tool_call=SimpleNamespace(id="tc1", name="bash"),
        args={"command": "rm -rf /tmp/x"},
    )
    result = await session.agent.before_tool_call(hook_ctx, None)

    assert result is None  # 未拦截
    assert hook_ctx.args["command"] == "sandbox-exec -- rm -rf /tmp/x"


@pytest.mark.asyncio
async def test_tool_call_later_handler_sees_earlier_mutation():
    """串行 handler 后者可见前者的修改（对齐 pi 的链式可见语义）。"""
    seen = []

    def first(event, ctx):
        event.args["env"] = {"A": "1"}
        return None

    def second(event, ctx):
        seen.append(dict(event.args))
        return None

    ext_a = Extension(path="a", handlers={TOOL_CALL: [first]})
    ext_b = Extension(path="b", handlers={TOOL_CALL: [second]})
    runner = ExtensionRunner(
        extensions=[ext_a, ext_b],
        runtime=_minimal_runtime(),
        cwd="/tmp",
        session_manager=None,
        model_runtime=None,
    )

    session = AgentSession(_make_config())
    session._extension_runner = runner

    hook_ctx = SimpleNamespace(
        tool_call=SimpleNamespace(id="tc1", name="bash"),
        args={"command": "ls"},
    )
    await session.agent.before_tool_call(hook_ctx, None)

    assert seen == [{"command": "ls", "env": {"A": "1"}}]
