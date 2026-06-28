"""
ExtensionRunner 单元测试。

验证事件分发、hook 调用、错误处理、工具/命令/flag/渲染器管理，
以及对 AgentSession / Runtime 的动作委托。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from nova_agent import AgentContext, AgentLoopTurnUpdate, AgentToolResult
from nova_ai import AssistantMessage, TextContent, UserMessage

from nova_harness.core.agent_session.extensions import (
    Extension,
    ExtensionLoader,
    ExtensionRunner,
    NovaExtensionAPI,
)
from nova_harness.core.types.events import (
    BEFORE_AGENT_START,
    CONTEXT,
    INPUT,
    MESSAGE_END,
    PREPARE_NEXT_TURN,
    RESOURCES_DISCOVER,
    SHOULD_STOP_AFTER_TURN,
    TOOL_CALL,
    TOOL_RESULT,
    USER_BASH,
    BeforeAgentStartEventResult,
    CompactionEndEvent,
    CompactionStartEvent,
    ContextEventResult,
    ExtensionErrorEvent,
    InputEvent,
    InputEventResult,
    MessageEndEventResult,
    PrepareNextTurnEvent,
    PrepareNextTurnEventResult,
    ResourcesDiscoverEventResult,
    ShouldStopAfterTurnEvent,
    ShouldStopAfterTurnEventResult,
    ToolCallEvent,
    ToolCallEventResult,
    ToolResultEvent,
    ToolResultEventResult,
    UserBashEvent,
)
from nova_harness.core.types.extensions import (
    ExtensionCommand,
    ExtensionFlag,
    ExtensionMessageRenderer,
    ExtensionShortcut,
    ExtensionToolDefinition,
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
    s.prompt = AsyncMock()
    s.send_user_message = AsyncMock()
    s.set_session_name = MagicMock()
    s.get_active_tool_names.return_value = ["t1"]
    s.get_all_tools.return_value = ["t2"]
    s.set_active_tools_by_name = MagicMock()
    s.refresh_tools = MagicMock()
    s.set_model = AsyncMock()
    s.thinking_level = "medium"
    s.set_thinking_level = AsyncMock()
    s.compact = AsyncMock(return_value="compacted")
    s._base_system_prompt = "base-prompt"
    s.is_streaming = False
    s._steering_messages = []
    s._follow_up_messages = []
    s.get_context_usage.return_value = {"tokens": 10}
    s.navigate_tree = AsyncMock()
    return s


@pytest.fixture
def runtime():
    """构造一个 mock runtime。"""
    r = MagicMock()
    r.new_session = AsyncMock()
    r.fork = AsyncMock()
    r.switch_session = AsyncMock()
    r.reload = AsyncMock()
    return r


def run_async(coro):
    """在当前事件循环运行一个协程。"""
    return asyncio.get_event_loop().run_until_complete(coro)


# -----------------------------------------------------------------------------
# 生命周期与访问器
# -----------------------------------------------------------------------------


def test_runner_init(runner):
    """ExtensionRunner 初始化后应持有正确的默认状态。"""
    assert runner.extensions == []
    assert runner._invalid is False
    assert runner._session is None
    assert runner._runtime is None
    assert runner.drain_diagnostics() == []


def test_runner_bind_session_and_runtime(runner):
    """bind_session / bind_runtime 应设置内部引用。"""
    runner.bind_session("session")
    runner.bind_runtime("runtime")
    assert runner._session == "session"
    assert runner._runtime == "runtime"


def test_runner_invalidate(runner):
    """invalidate 应清空 session/runtime 和事件总线。"""
    runner.bind_session("session")
    runner.event_bus.on("x", lambda: None)
    runner.invalidate()
    assert runner._invalid is True
    assert runner._session is None
    assert runner._runtime is None
    assert runner.event_bus._handlers == {}


def test_runner_assert_active_raises_after_invalidate(runner):
    """失效后再调用动作应抛出 RuntimeError。"""
    runner.invalidate()
    with pytest.raises(RuntimeError, match="invalidated"):
        runner.bind_session(MagicMock())


def test_runner_accessors(runner):
    """runner 的基础属性应来自 services。"""
    assert runner.cwd == "/tmp"
    assert runner.session_manager is runner.services.session_manager
    assert runner.model_registry is runner.services.model_registry
    assert runner.settings_manager is runner.services.settings_manager


def test_runner_model_property(runner):
    """model 属性在 session 未绑定/已绑定时表现应正确。"""
    assert runner.model is None
    runner.bind_session(MagicMock(model="m1"))
    assert runner.model == "m1"


def test_runner_add_and_drain_diagnostics(runner):
    """add_diagnostic 与 drain_diagnostics 应正确累积与清空。"""
    runner.add_diagnostic("info", "msg")
    assert len(runner._diagnostics) == 1
    drained = runner.drain_diagnostics()
    assert len(drained) == 1
    assert drained[0].type == "info"
    assert runner._diagnostics == []


# -----------------------------------------------------------------------------
# 通用事件分发
# -----------------------------------------------------------------------------


async def test_emit_returns_last_result(runner):
    """emit 应按顺序执行 handler 并返回最后一个非 None 结果。"""
    ext = Extension(path="a", name="a")
    ext.handlers["test"] = [lambda e: None, lambda e: "second", lambda e: "third"]
    runner.extensions = [ext]
    result = await runner.emit(MagicMock(type="test"))
    assert result == "third"


async def test_emit_cancel_short_circuits(runner):
    """cancel=True 的结果应立即返回，不再执行后续 handler。"""
    ext = Extension(path="a", name="a")

    class CancelResult:
        cancel = True

    calls = []
    ext.handlers["test"] = [
        lambda e: calls.append("first") or None,
        lambda e: calls.append("second") or CancelResult(),
        lambda e: calls.append("third") or None,
    ]
    runner.extensions = [ext]
    result = await runner.emit(MagicMock(type="test"))
    assert isinstance(result, CancelResult)
    assert calls == ["first", "second"]


async def test_emit_async_handler_supported(runner):
    """emit 应支持异步 handler。"""
    ext = Extension(path="a", name="a")

    async def async_handler(e):
        return "async"

    ext.handlers["test"] = [lambda e: "sync", async_handler]
    runner.extensions = [ext]
    result = await runner.emit(MagicMock(type="test"))
    assert result == "async"


async def test_emit_without_type_returns_none(runner):
    """事件没有 type 属性时 emit 返回 None。"""
    assert await runner.emit(MagicMock(spec=[])) is None


async def test_emit_without_handlers_returns_none(runner):
    """没有订阅者时 emit 返回 None。"""
    assert await runner.emit(MagicMock(type="unknown")) is None


def test_has_handlers(runner):
    """has_handlers 应正确判断是否有扩展订阅事件。"""
    ext = Extension(path="a", name="a")
    ext.handlers["evt"] = [lambda e: None]
    runner.extensions = [ext]
    assert runner.has_handlers("evt") is True
    assert runner.has_handlers("other") is False


# -----------------------------------------------------------------------------
# 错误处理
# -----------------------------------------------------------------------------


async def test_emit_error_handler_receives_error(runner):
    """handler 抛错时应通过 on_error 上报 ExtensionErrorEvent。"""
    ext = Extension(path="a", name="a")

    def bad_handler(e):
        raise RuntimeError("boom")

    ext.handlers["test"] = [bad_handler]
    runner.extensions = [ext]

    errors = []
    runner.on_error(lambda e: errors.append(e))
    await runner.emit(MagicMock(type="test"))

    assert len(errors) == 1
    assert isinstance(errors[0], ExtensionErrorEvent)
    assert errors[0].extension_path == "a"
    assert errors[0].event == "test"
    assert "boom" in errors[0].error


async def test_emit_error_handler_swallows_exceptions(runner):
    """错误 handler 自身抛错不应中断后续错误处理。"""
    ext = Extension(path="a", name="a")
    ext.handlers["test"] = [lambda e: (_ for _ in ()).throw(RuntimeError("x"))]
    runner.extensions = [ext]

    received = []
    runner.on_error(lambda e: received.append(e))
    runner.on_error(lambda e: (_ for _ in ()).throw(RuntimeError("handler error")))
    await runner.emit(MagicMock(type="test"))
    assert len(received) == 1


def test_emit_error_public_method(runner):
    """emit_error 应广播错误事件。"""
    received = []
    runner.on_error(lambda e: received.append(e))
    err = ExtensionErrorEvent(extension_path="p", event="e", error="msg")
    runner.emit_error(err)
    assert received == [err]


# -----------------------------------------------------------------------------
# 特殊事件分发
# -----------------------------------------------------------------------------


async def test_emit_context_replaces_messages(runner):
    """emit_context 应把 handler 返回的 messages 作为后续输入。"""
    ext = Extension(path="a", name="a")
    msg_a = MagicMock(role="user")
    msg_b = MagicMock(role="user")
    ext.handlers[CONTEXT] = [
        lambda e: ContextEventResult(messages=[msg_a]),
        lambda e: ContextEventResult(messages=[msg_b]),
    ]
    runner.extensions = [ext]
    messages = await runner.emit_context([MagicMock()])
    assert messages == [msg_b]


async def test_emit_before_agent_start_merges(runner):
    """emit_before_agent_start 应合并 system_prompt 与 extra_messages。"""
    ext = Extension(path="a", name="a")
    extra = UserMessage(role="user", content=[TextContent(text="extra")])
    ext.handlers[BEFORE_AGENT_START] = [
        lambda e: BeforeAgentStartEventResult(system_prompt="sys2", message=extra),
    ]
    runner.extensions = [ext]
    system_prompt, extras = await runner.emit_before_agent_start(
        "prompt", [], "sys1", {}
    )
    assert system_prompt == "sys2"
    assert extras == [extra]


async def test_emit_tool_call_blocks(runner):
    """emit_tool_call 应在任一 handler 返回 block=True 时立即返回。"""
    ext = Extension(path="a", name="a")
    ext.handlers[TOOL_CALL] = [
        lambda e: ToolCallEventResult(block=False),
        lambda e: ToolCallEventResult(block=True, reason="blocked"),
        lambda e: ToolCallEventResult(block=True),
    ]
    runner.extensions = [ext]
    event = ToolCallEvent(tool_call_id="1", tool_name="t", args={})
    result = await runner.emit_tool_call(event)
    assert result.block is True
    assert result.reason == "blocked"


async def test_emit_tool_result_merges(runner):
    """emit_tool_result 应合并多个 handler 对 content/details/is_error 的修改。"""
    ext = Extension(path="a", name="a")
    ext.handlers[TOOL_RESULT] = [
        lambda e: ToolResultEventResult(content=[TextContent(text="hello")]),
        lambda e: ToolResultEventResult(details={"x": 1}, is_error=True),
    ]
    runner.extensions = [ext]
    event = ToolResultEvent(
        tool_call_id="1",
        tool_name="t",
        args={},
        content=[],
        details=None,
        is_error=False,
    )
    result = await runner.emit_tool_result(event)
    assert result.content == [TextContent(text="hello")]
    assert result.details == {"x": 1}
    assert result.is_error is True


async def test_emit_tool_result_unchanged_returns_none(runner):
    """没有 handler 修改 tool_result 时返回 None。"""
    event = ToolResultEvent(
        tool_call_id="1",
        tool_name="t",
        args={},
        content=[],
        details=None,
        is_error=False,
    )
    assert await runner.emit_tool_result(event) is None


async def test_emit_message_end_replaces_same_role(runner):
    """emit_message_end 应允许同 role 的消息替换。"""
    ext = Extension(path="a", name="a")
    replacement = AssistantMessage(
        role="assistant", content=[TextContent(text="replaced")]
    )
    ext.handlers[MESSAGE_END] = [lambda e: MessageEndEventResult(message=replacement)]
    runner.extensions = [ext]
    original = AssistantMessage(role="assistant", content=[TextContent(text="orig")])
    result = await runner.emit_message_end(original)
    assert result.content[0].text == "replaced"


async def test_emit_message_end_rejects_role_change(runner):
    """role 不一致时应报错并保留原消息。"""
    ext = Extension(path="a", name="a")
    replacement = MagicMock()
    replacement.role = "user"
    ext.handlers[MESSAGE_END] = [lambda e: MessageEndEventResult(message=replacement)]
    runner.extensions = [ext]

    errors = []
    runner.on_error(lambda e: errors.append(e))
    original = AssistantMessage(role="assistant", content=[TextContent(text="orig")])
    result = await runner.emit_message_end(original)
    assert result is original
    assert len(errors) == 1
    assert "same role" in errors[0].error


async def test_emit_input_transform(runner):
    """emit_input 应支持 transform 输入文本与图片。"""
    ext = Extension(path="a", name="a")
    ext.handlers[INPUT] = [
        lambda e: InputEventResult(action="transform", text="transformed")
    ]
    runner.extensions = [ext]
    result = await runner.emit_input(InputEvent(text="hello"))
    assert result.action == "transform"
    assert result.text == "transformed"


async def test_emit_input_handled(runner):
    """emit_input 遇到 handled 应立即返回。"""
    ext = Extension(path="a", name="a")
    ext.handlers[INPUT] = [
        lambda e: InputEventResult(action="handled"),
        lambda e: InputEventResult(action="transform", text="should_not_apply"),
    ]
    runner.extensions = [ext]
    result = await runner.emit_input(InputEvent(text="hello"))
    assert result.action == "handled"


async def test_emit_user_bash_returns_first_non_none(runner):
    """emit_user_bash 应返回第一个非 None 结果。"""
    ext = Extension(path="a", name="a")
    ext.handlers[USER_BASH] = [lambda e: None, lambda e: "result"]
    runner.extensions = [ext]
    event = UserBashEvent(command="ls")
    assert await runner.emit_user_bash(event) == "result"


async def test_emit_resources_discover_merges_paths(runner):
    """emit_resources_discover 应合并多个扩展返回的路径。"""
    ext1 = Extension(path="a", name="a")
    ext1.handlers[RESOURCES_DISCOVER] = [
        lambda e: ResourcesDiscoverEventResult(skill_paths=["/a"], prompt_paths=["/p"])
    ]
    ext2 = Extension(path="b", name="b")
    ext2.handlers[RESOURCES_DISCOVER] = [
        lambda e: ResourcesDiscoverEventResult(theme_paths=["/t"])
    ]
    runner.extensions = [ext1, ext2]
    result = await runner.emit_resources_discover("/tmp", "startup")
    assert result.skill_paths == ["/a"]
    assert result.prompt_paths == ["/p"]
    assert result.theme_paths == ["/t"]


async def test_emit_prepare_next_turn_merges(runner):
    """prepare_next_turn 应合并多个扩展返回的 context/model/thinking_level。"""
    ext = Extension(path="a", name="a")
    ctx = AgentContext(system_prompt="sys")
    ext.handlers[PREPARE_NEXT_TURN] = [
        lambda e: PrepareNextTurnEventResult(thinking_level="low"),
        lambda e: PrepareNextTurnEventResult(context=ctx, thinking_level="high"),
    ]
    runner.extensions = [ext]
    event = PrepareNextTurnEvent()
    result = await runner.emit_prepare_next_turn(event)
    assert isinstance(result, AgentLoopTurnUpdate)
    assert result.context is ctx
    assert result.thinking_level == "high"


async def test_emit_prepare_next_turn_no_handler(runner):
    """没有 handler 时 prepare_next_turn 返回 None。"""
    assert await runner.emit_prepare_next_turn(PrepareNextTurnEvent()) is None


async def test_emit_should_stop_after_turn_short_circuits(runner):
    """should_stop_after_turn 任一扩展返回 stop=True 应立即返回 True。"""
    ext = Extension(path="a", name="a")
    ext.handlers[SHOULD_STOP_AFTER_TURN] = [
        lambda e: ShouldStopAfterTurnEventResult(stop=False),
        lambda e: ShouldStopAfterTurnEventResult(stop=True),
        lambda e: ShouldStopAfterTurnEventResult(stop=True),
    ]
    runner.extensions = [ext]
    result = await runner.emit_should_stop_after_turn(ShouldStopAfterTurnEvent())
    assert result is True


async def test_emit_should_stop_after_turn_default(runner):
    """没有扩展要求停止时返回 False。"""
    result = await runner.emit_should_stop_after_turn(ShouldStopAfterTurnEvent())
    assert result is False


# -----------------------------------------------------------------------------
# 工具包装
# -----------------------------------------------------------------------------


def test_extension_tool_wraps_string_result(runner):
    """ExtensionTool 应把字符串返回值包装成 AgentToolResult。"""
    definition = ExtensionToolDefinition(
        name="echo",
        description="echo",
        parameters={},
        execute=lambda ctx, tool_call_id, params, signal: params.get("text", ""),
    )
    runner.extensions = [Extension(path="a", name="a", tools=[definition])]
    tools = runner.get_extension_tools()
    assert len(tools) == 1
    result = run_async(tools[0].execute("id-1", {"text": "hi"}))
    assert isinstance(result, AgentToolResult)
    assert result.content[0].text == "hi"


def test_extension_tool_propagates_exception(runner):
    """ExtensionTool 执行异常时应抛出，由 Agent 循环标记为错误。"""

    def failing(ctx, tool_call_id, params, signal):
        raise RuntimeError("tool failed")

    definition = ExtensionToolDefinition(
        name="fail", description="fail", parameters={}, execute=failing
    )
    runner.extensions = [Extension(path="a", name="a", tools=[definition])]
    tool = runner.get_extension_tools()[0]
    with pytest.raises(RuntimeError, match="tool failed"):
        run_async(tool.execute("id-2", {}))


def test_extension_tool_returns_agent_tool_result(runner):
    """如果扩展直接返回 AgentToolResult，应原样透传。"""
    definition = ExtensionToolDefinition(
        name="raw",
        description="raw",
        parameters={},
        execute=lambda ctx, tool_call_id, params, signal: AgentToolResult(
            content=[TextContent(text="raw")], details={"ok": True}
        ),
    )
    runner.extensions = [Extension(path="a", name="a", tools=[definition])]
    tool = runner.get_extension_tools()[0]
    result = run_async(tool.execute("id-3", {}))
    assert result.content[0].text == "raw"
    assert result.details == {"ok": True}


def test_get_tool_definition(runner):
    """get_tool_definition 应按名称查找扩展工具定义。"""
    definition = ExtensionToolDefinition(name="found", description="d", parameters={})
    runner.extensions = [Extension(path="a", name="a", tools=[definition])]
    assert runner.get_tool_definition("found") is definition
    assert runner.get_tool_definition("missing") is None


# -----------------------------------------------------------------------------
# 命令与快捷键
# -----------------------------------------------------------------------------


def test_get_commands(runner):
    """get_commands 应返回所有扩展命令。"""
    cmd = ExtensionCommand(name="c1")
    runner.extensions = [Extension(path="a", name="a", commands=[cmd])]
    assert runner.get_commands() == [cmd]


def test_get_registered_commands_resolves_conflicts(runner):
    """同名命令应生成带 :N 后缀的 invocation name。"""
    cmd1 = ExtensionCommand(name="dup")
    cmd2 = ExtensionCommand(name="dup")
    runner.extensions = [
        Extension(path="a", name="a", commands=[cmd1]),
        Extension(path="b", name="b", commands=[cmd2]),
    ]
    resolved = runner.get_registered_commands()
    names = {c.name for c in resolved}
    assert names == {"dup:1", "dup:2"}


def test_get_registered_commands_avoids_invocation_collision(runner):
    """若命令名本身形如 'name:1'，应避免与自动生成的 invocation 冲突。"""
    cmd1 = ExtensionCommand(name="dup")
    cmd2 = ExtensionCommand(name="dup:1")
    runner.extensions = [
        Extension(path="a", name="a", commands=[cmd1]),
        Extension(path="b", name="b", commands=[cmd2]),
    ]
    resolved = runner.get_registered_commands()
    names = [c.name for c in resolved]
    assert len(names) == len(set(names))
    assert "dup" in names
    assert "dup:1" in names


def test_get_command(runner):
    """get_command 应支持带 :N 后缀的 invocation name。"""
    runner.extensions = [
        Extension(path="a", name="a", commands=[ExtensionCommand(name="cmd")]),
        Extension(path="b", name="b", commands=[ExtensionCommand(name="dup")]),
        Extension(path="c", name="c", commands=[ExtensionCommand(name="dup")]),
    ]
    assert runner.get_command("cmd").name == "cmd"
    assert runner.get_command("dup:1").name == "dup:1"
    assert runner.get_command("dup:2").name == "dup:2"
    assert runner.get_command("missing") is None


def test_get_shortcuts_detects_conflicts(runner):
    """get_shortcuts 应检测扩展之间的快捷键冲突并记录诊断。"""
    ext1 = Extension(path="a", name="a")
    ext1.shortcuts = [ExtensionShortcut(key="ctrl+x", extension_path="a")]
    ext2 = Extension(path="b", name="b")
    ext2.shortcuts = [ExtensionShortcut(key="Ctrl+X", extension_path="b")]
    runner.extensions = [ext1, ext2]

    shortcuts = runner.get_shortcuts()
    assert len(shortcuts) == 1
    diagnostics = runner.drain_diagnostics()
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "warning"
    assert "ctrl+x" in diagnostics[0].message.lower()


# -----------------------------------------------------------------------------
# Flag 与渲染器
# -----------------------------------------------------------------------------


def test_get_flags_dedup(runner):
    """get_flags 按名称去重，先注册的优先。"""
    ext1 = Extension(path="a", name="a")
    ext1.flags = [ExtensionFlag(name="f", default=True)]
    ext2 = Extension(path="b", name="b")
    ext2.flags = [ExtensionFlag(name="f", default=False)]
    runner.extensions = [ext1, ext2]
    flags = runner.get_flags()
    assert len(flags) == 1
    assert flags["f"].default is True


def test_flag_values(runner):
    """flag 当前值的读写应保持一致。"""
    assert runner.get_flag_values() == {}
    runner.set_flag_value("x", 1)
    assert runner.get_flag_value("x") == 1
    assert runner.get_flag_values() == {"x": 1}


def test_get_message_renderer(runner):
    """get_message_renderer 应按 custom_type 查找渲染器。"""
    renderer = ExtensionMessageRenderer(custom_type="card")
    runner.extensions = [Extension(path="a", name="a", message_renderers=[renderer])]
    assert runner.get_message_renderer("card") is renderer
    assert runner.get_message_renderer("missing") is None


# -----------------------------------------------------------------------------
# Session / Runtime 动作委托
# -----------------------------------------------------------------------------


def test_runner_refresh_tools(runner, session):
    """refresh_tools 应调用当前 session 的 refresh_tools。"""
    runner.bind_session(session)
    runner.refresh_tools()
    session.refresh_tools.assert_called_once()


async def test_runner_send_message(runner, session):
    """send_message 应委托给 session.prompt。"""
    runner.bind_session(session)
    await runner.send_message("hello", {"opt": 1})
    session.prompt.assert_awaited_once_with("hello", {"opt": 1})


async def test_runner_send_user_message(runner, session):
    """send_user_message 应委托给 session.send_user_message。"""
    runner.bind_session(session)
    await runner.send_user_message("content", {"opt": 2})
    session.send_user_message.assert_awaited_once_with("content", {"opt": 2})


def test_runner_append_entry(runner):
    """append_entry 应委托给 session_manager.append_custom_entry。"""
    runner.services.session_manager.append_custom_entry.return_value = "entry-1"
    assert runner.append_entry("type-x", {"data": 1}) == "entry-1"
    runner.services.session_manager.append_custom_entry.assert_called_once_with(
        "type-x", {"data": 1}
    )


def test_runner_set_session_name(runner, session):
    """set_session_name 应委托给 session.set_session_name。"""
    runner.bind_session(session)
    runner.set_session_name("new-name")
    session.set_session_name.assert_called_once_with("new-name")


def test_runner_get_session_name(runner):
    """get_session_name 应委托给 session_manager。"""
    runner.services.session_manager.get_session_name.return_value = "s-name"
    assert runner.get_session_name() == "s-name"


def test_runner_set_label(runner):
    """set_label 应委托给 session_manager.append_label_change。"""
    runner.set_label("e1", "label")
    runner.services.session_manager.append_label_change.assert_called_once_with(
        "e1", "label"
    )


def test_runner_get_active_tools(runner, session):
    """get_active_tools 应委托给 session.get_active_tool_names。"""
    runner.bind_session(session)
    assert runner.get_active_tools() == ["t1"]


def test_runner_get_all_tools(runner, session):
    """get_all_tools 应委托给 session.get_all_tools。"""
    runner.bind_session(session)
    assert runner.get_all_tools() == ["t2"]


def test_runner_set_active_tools(runner, session):
    """set_active_tools 应委托给 session.set_active_tools_by_name。"""
    runner.bind_session(session)
    runner.set_active_tools(["a", "b"])
    session.set_active_tools_by_name.assert_called_once_with(["a", "b"])


async def test_runner_set_model(runner, session):
    """set_model 应委托给 session.set_model。"""
    runner.bind_session(session)
    await runner.set_model("model-x")
    session.set_model.assert_awaited_once_with("model-x")


def test_runner_get_thinking_level(runner, session):
    """get_thinking_level 应返回 session.thinking_level。"""
    runner.bind_session(session)
    assert runner.get_thinking_level() == "medium"


async def test_runner_set_thinking_level(runner, session):
    """set_thinking_level 应委托给 session.set_thinking_level。"""
    runner.bind_session(session)
    await runner.set_thinking_level("low")
    session.set_thinking_level.assert_awaited_once_with("low")


async def test_runner_compact(runner, session):
    """compact 应委托给 session.compact。"""
    runner.bind_session(session)
    result = await runner.compact("instr")
    session.compact.assert_awaited_once_with("instr")
    assert result == "compacted"


def test_runner_get_system_prompt(runner, session):
    """get_system_prompt 应返回 session._base_system_prompt。"""
    runner.bind_session(session)
    assert runner.get_system_prompt() == "base-prompt"


def test_runner_get_system_prompt_options(runner):
    """get_system_prompt_options 应返回 cwd。"""
    assert runner.get_system_prompt_options() == {"cwd": "/tmp"}


def test_runner_is_idle(runner, session):
    """is_idle 应返回 session.is_streaming 的取反。"""
    runner.bind_session(session)
    session.is_streaming = False
    assert runner.is_idle() is True
    session.is_streaming = True
    assert runner.is_idle() is False


def test_runner_is_project_trusted(runner):
    """is_project_trusted 默认返回 True。"""
    assert runner.is_project_trusted() is True


def test_runner_has_pending_messages(runner, session):
    """has_pending_messages 应检测 steering / follow_up 消息队列。"""
    runner.bind_session(session)
    assert runner.has_pending_messages() is False
    session._steering_messages = [MagicMock()]
    assert runner.has_pending_messages() is True


def test_runner_get_context_usage(runner, session):
    """get_context_usage 应委托给 session.get_context_usage。"""
    runner.bind_session(session)
    assert runner.get_context_usage() == {"tokens": 10}


async def test_runner_new_session(runner, runtime):
    """new_session 应委托给 runtime.new_session。"""
    runner.bind_runtime(runtime)
    await runner.new_session({"name": "x"})
    runtime.new_session.assert_awaited_once_with({"name": "x"})


async def test_runner_fork(runner, runtime):
    """fork 应委托给 runtime.fork。"""
    runner.bind_runtime(runtime)
    await runner.fork("entry-1")
    runtime.fork.assert_awaited_once_with("entry-1")


async def test_runner_navigate_tree(runner, session):
    """navigate_tree 应委托给 session.navigate_tree。"""
    runner.bind_session(session)
    await runner.navigate_tree("target", {"opt": 1})
    session.navigate_tree.assert_awaited_once_with("target", {"opt": 1})


async def test_runner_switch_session(runner, runtime):
    """switch_session 应委托给 runtime.switch_session。"""
    runner.bind_runtime(runtime)
    await runner.switch_session("/path")
    runtime.switch_session.assert_awaited_once_with("/path")


async def test_runner_reload(runner, runtime):
    """reload 应委托给 runtime.reload。"""
    runner.bind_runtime(runtime)
    await runner.reload()
    runtime.reload.assert_awaited_once()


async def test_runner_wait_for_idle(runner, session):
    """wait_for_idle 应委托给 session.agent.wait_for_idle。"""
    agent = MagicMock()
    agent.wait_for_idle = AsyncMock()
    session.agent = agent
    runner.bind_session(session)
    await runner.wait_for_idle()
    agent.wait_for_idle.assert_awaited_once()


# -----------------------------------------------------------------------------
# 扩展加载器
# -----------------------------------------------------------------------------


async def test_extension_loader_discovers_paths(tmp_path):
    """ExtensionLoader 应发现显式配置、项目级和全局扩展路径。"""
    cwd = tmp_path / "project"
    cwd.mkdir()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    configured = tmp_path / "configured_ext.py"
    configured.write_text("def extension(nova): pass")

    project_ext_dir = cwd / ".nova" / "extensions"
    project_ext_dir.mkdir(parents=True)
    project_ext = project_ext_dir / "project_ext.py"
    project_ext.write_text("def extension(nova): pass")

    global_ext_dir = agent_dir / "extensions"
    global_ext_dir.mkdir(parents=True)
    global_ext = global_ext_dir / "global_ext.py"
    global_ext.write_text("def extension(nova): pass")

    loader = ExtensionLoader(
        cwd=str(cwd),
        agent_dir=agent_dir,
        extension_api_factory=lambda extension, context: NovaExtensionAPI(
            extension, context
        ),
    )
    paths = loader.discover_paths(configured_paths=[str(configured)])

    assert configured.resolve() in paths
    assert project_ext.resolve() in paths
    assert global_ext.resolve() in paths


async def test_extension_loader_loads_factory(tmp_path, runner):
    """ExtensionLoader 应能加载扩展模块并执行工厂函数。"""
    ext_file = tmp_path / "my_ext.py"
    ext_file.write_text("""
from nova_harness.core.agent_session.extensions import ExtensionFlag

def extension(nova):
    nova.on("session_start", lambda e: "handled")
    nova.register_flag(ExtensionFlag(name="flag", default=True))
""".strip())

    loader = ExtensionLoader(
        cwd=str(tmp_path),
        agent_dir=tmp_path,
        extension_api_factory=lambda extension, context: NovaExtensionAPI(
            extension, context
        ),
    )
    ext = await loader.load_extension_async(ext_file, runner)

    assert ext is not None
    assert ext.name == "my_ext"
    assert "session_start" in ext.handlers
    assert len(ext.flags) == 1


async def test_compaction_start_end_events_emitted(runner):
    """手动 compact 事件应能被扩展捕获。"""
    events = []

    def capture(event):
        events.append(event)
        return None

    ext = Extension(path="a", name="a")
    from nova_harness.core.types.events import COMPACTION_END, COMPACTION_START

    ext.handlers[COMPACTION_START] = [capture]
    ext.handlers[COMPACTION_END] = [capture]
    runner.extensions = [ext]

    await runner.emit(CompactionStartEvent(custom_instructions="x"))
    await runner.emit(
        CompactionEndEvent(result=None, aborted=False, error_message=None)
    )

    assert len(events) == 2
    assert isinstance(events[0], CompactionStartEvent)
    assert events[0].custom_instructions == "x"
    assert isinstance(events[1], CompactionEndEvent)
