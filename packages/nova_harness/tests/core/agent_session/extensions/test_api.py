"""
NovaExtensionAPI 单元测试。

验证扩展工厂拿到的 ``nova`` 对象能够正确注册事件处理器、工具、命令、
flag、渲染器、provider，并把运行时 action 委托给 ExtensionRunner。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nova_harness.core.agent_session.extensions import (
    Extension,
    ExtensionRunner,
    NovaExtensionAPI,
)
from nova_harness.core.types.events import SESSION_START
from nova_harness.core.types.extensions import (
    ExtensionCommand,
    ExtensionFlag,
    ExtensionMessageRenderer,
    ExtensionProviderRegistration,
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
def api(runner):
    """构造一个 NovaExtensionAPI 及其绑定的 Extension。"""
    ext = Extension(path="ext/path", name="ext")
    runner.extensions = [ext]
    return NovaExtensionAPI(ext, runner), ext


# -----------------------------------------------------------------------------
# 注册接口
# -----------------------------------------------------------------------------


def test_api_on_registers_handler(api):
    """on 应把 handler 追加到扩展的 handlers 列表。"""
    nova, ext = api

    def handler(event):
        return None

    nova.on(SESSION_START, handler)
    assert ext.handlers[SESSION_START] == [handler]


def test_api_on_multiple_handlers(api):
    """同一事件可以注册多个 handler。"""
    nova, ext = api
    nova.on("custom", lambda e: 1)
    nova.on("custom", lambda e: 2)
    assert len(ext.handlers["custom"]) == 2


def test_api_register_tool(api):
    """register_tool 应把工具定义追加到扩展。"""
    nova, ext = api
    nova.register_tool(ExtensionToolDefinition(name="t", description="d"))
    assert len(ext.tools) == 1
    assert ext.tools[0].name == "t"


def test_api_register_command_sets_extension_path(api):
    """register_command 应设置 extension_path 并追加命令。"""
    nova, ext = api
    cmd = ExtensionCommand(name="cmd", description="desc")
    nova.register_command(cmd)
    assert cmd.extension_path == "ext/path"
    assert ext.commands == [cmd]


def test_api_register_shortcut_sets_extension_path(api):
    """register_shortcut 应设置 extension_path 并追加快捷键。"""
    nova, ext = api
    shortcut = ExtensionShortcut(key="ctrl+x")
    nova.register_shortcut(shortcut)
    assert shortcut.extension_path == "ext/path"
    assert ext.shortcuts == [shortcut]


def test_api_register_flag_sets_extension_path(api):
    """register_flag 应设置 extension_path 并追加 flag。"""
    nova, ext = api
    flag = ExtensionFlag(name="debug", default=True)
    nova.register_flag(flag)
    assert flag.extension_path == "ext/path"
    assert ext.flags == [flag]
    assert nova.get_flag("debug") is True


def test_api_register_message_renderer_sets_extension_path(api):
    """register_message_renderer 应设置 extension_path 并追加渲染器。"""
    nova, ext = api
    renderer = ExtensionMessageRenderer(custom_type="card")
    nova.register_message_renderer(renderer)
    assert renderer.extension_path == "ext/path"
    assert ext.message_renderers == [renderer]


# -----------------------------------------------------------------------------
# Provider 注册与注销
# -----------------------------------------------------------------------------


def test_api_register_provider_delegates_to_model_registry(api, runner):
    """register_provider 应立即委托给 ModelRegistry 并记录注册信息。"""
    nova, ext = api
    nova.register_provider("custom", {"base_url": "http://localhost"})
    assert len(ext.providers) == 1
    assert isinstance(ext.providers[0], ExtensionProviderRegistration)
    assert ext.providers[0].name == "custom"
    runner.services.model_registry.register_provider.assert_called_once_with(
        "custom", {"base_url": "http://localhost"}
    )


def test_api_register_provider_captures_registry_error(api, runner):
    """ModelRegistry 抛错时应记录诊断信息，不影响扩展注册。"""
    nova, ext = api
    runner.services.model_registry.register_provider.side_effect = RuntimeError("boom")
    nova.register_provider("custom", {})
    assert len(ext.providers) == 1
    diagnostics = runner.drain_diagnostics()
    assert len(diagnostics) == 1
    assert diagnostics[0].type == "error"
    assert "custom" in diagnostics[0].message


def test_api_unregister_provider(api, runner):
    """unregister_provider 应移除 provider 并调用 ModelRegistry 注销。"""
    nova, ext = api
    nova.register_provider("p1", {"base_url": "http://x"})
    assert len(ext.providers) == 1
    nova.unregister_provider("p1")
    assert len(ext.providers) == 0
    runner.services.model_registry.unregister_provider.assert_called_once_with("p1")


def test_api_unregister_provider_missing_is_noop(api, runner):
    """注销不存在的 provider 不应报错。"""
    nova, _ = api
    nova.unregister_provider("missing")
    runner.services.model_registry.unregister_provider.assert_called_once_with(
        "missing"
    )


# -----------------------------------------------------------------------------
# Flag 运行时值
# -----------------------------------------------------------------------------


def test_api_get_flag_returns_default(api):
    """get_flag 返回注册时的默认值。"""
    nova, ext = api
    nova.register_flag(ExtensionFlag(name="f", default="default"))
    assert nova.get_flag("f") == "default"
    assert nova.get_flag("missing") is None


def test_api_get_flag_value_prefers_runtime_value(api, runner):
    """get_flag_value 优先返回运行时设置值，未设置时回退默认值。"""
    nova, _ = api
    nova.register_flag(ExtensionFlag(name="f", default="default"))
    assert nova.get_flag_value("f") == "default"
    runner.set_flag_value("f", "runtime")
    assert nova.get_flag_value("f") == "runtime"


def test_api_set_flag_value_delegates_to_context(api, runner):
    """set_flag_value 应委托给 runner 的 flag 存储。"""
    nova, _ = api
    nova.set_flag_value("x", 42)
    assert runner.get_flag_value("x") == 42


# -----------------------------------------------------------------------------
# Action 委托
# -----------------------------------------------------------------------------


@pytest.fixture
def session():
    """构造一个已绑定的 mock session。"""
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
    s.get_context_usage.return_value = {"tokens": 10}
    return s


@pytest.fixture
def bound_api(api, session):
    """构造一个已绑定 session 的 NovaExtensionAPI。"""
    nova, ext = api
    nova._context.bind_session(session)
    return nova, ext


async def test_api_send_message_delegates(bound_api, session):
    """send_message 应委托给 session.prompt。"""
    nova, _ = bound_api
    await nova.send_message("hello", {"opt": 1})
    session.prompt.assert_awaited_once_with("hello", {"opt": 1})


async def test_api_send_user_message_delegates(bound_api, session):
    """send_user_message 应委托给 session.send_user_message。"""
    nova, _ = bound_api
    await nova.send_user_message("content", {"opt": 2})
    session.send_user_message.assert_awaited_once_with("content", {"opt": 2})


def test_api_append_entry_delegates(api, runner):
    """append_entry 应委托给 session_manager.append_custom_entry。"""
    nova, _ = api
    runner.services.session_manager.append_custom_entry.return_value = "entry-1"
    assert nova.append_entry("type-x", {"data": 1}) == "entry-1"
    runner.services.session_manager.append_custom_entry.assert_called_once_with(
        "type-x", {"data": 1}
    )


def test_api_set_session_name_delegates(bound_api, session):
    """set_session_name 应委托给 session.set_session_name。"""
    nova, _ = bound_api
    nova.set_session_name("new-name")
    session.set_session_name.assert_called_once_with("new-name")


def test_api_get_session_name_delegates(api, runner):
    """get_session_name 应委托给 session_manager。"""
    nova, _ = api
    runner.services.session_manager.get_session_name.return_value = "s-name"
    assert nova.get_session_name() == "s-name"


def test_api_set_label_delegates(api, runner):
    """set_label 应委托给 session_manager.append_label_change。"""
    nova, _ = api
    nova.set_label("e1", "label")
    runner.services.session_manager.append_label_change.assert_called_once_with(
        "e1", "label"
    )


def test_api_get_active_tools_delegates(bound_api, session):
    """get_active_tools 应委托给 session.get_active_tool_names。"""
    nova, _ = bound_api
    assert nova.get_active_tools() == ["t1"]
    session.get_active_tool_names.assert_called_once()


def test_api_get_all_tools_delegates(bound_api, session):
    """get_all_tools 应委托给 session.get_all_tools。"""
    nova, _ = bound_api
    assert nova.get_all_tools() == ["t2"]
    session.get_all_tools.assert_called_once()


def test_api_set_active_tools_delegates(bound_api, session):
    """set_active_tools 应委托给 session.set_active_tools_by_name。"""
    nova, _ = bound_api
    nova.set_active_tools(["a", "b"])
    session.set_active_tools_by_name.assert_called_once_with(["a", "b"])


def test_api_refresh_tools_delegates(bound_api, session):
    """refresh_tools 应委托给 session.refresh_tools。"""
    nova, _ = bound_api
    nova.refresh_tools()
    session.refresh_tools.assert_called_once()


def test_api_get_commands_returns_runner_commands(bound_api):
    """get_commands 应返回 runner 解析后的命令列表。"""
    nova, ext = bound_api
    ext.commands = [ExtensionCommand(name="c1")]
    commands = nova.get_commands()
    assert len(commands) == 1
    assert commands[0].name == "c1"


async def test_api_set_model_delegates(bound_api, session):
    """set_model 应委托给 session.set_model。"""
    nova, _ = bound_api
    await nova.set_model("model-x")
    session.set_model.assert_awaited_once_with("model-x")


def test_api_get_thinking_level_delegates(bound_api, session):
    """get_thinking_level 应返回 session.thinking_level。"""
    nova, _ = bound_api
    assert nova.get_thinking_level() == session.thinking_level


async def test_api_set_thinking_level_delegates(bound_api, session):
    """set_thinking_level 应委托给 session.set_thinking_level。"""
    nova, _ = bound_api
    await nova.set_thinking_level("low")
    session.set_thinking_level.assert_awaited_once_with("low")


async def test_api_compact_delegates(bound_api, session):
    """compact 应委托给 session.compact。"""
    nova, _ = bound_api
    result = await nova.compact("instructions")
    session.compact.assert_awaited_once_with("instructions")
    assert result == "compacted"


def test_api_get_system_prompt_delegates(bound_api):
    """get_system_prompt 应返回 session._base_system_prompt。"""
    nova, _ = bound_api
    assert nova.get_system_prompt() == "base-prompt"


def test_api_events_property_returns_event_bus(api, runner):
    """events 属性应返回 runner 的事件总线。"""
    nova, _ = api
    assert nova.events is runner.event_bus


async def test_api_send_message_before_session_bound_raises(api):
    """未绑定 session 时调用 send_message 应抛出 RuntimeError。"""
    nova, _ = api
    with pytest.raises(RuntimeError, match="before session bound"):
        await nova.send_message("hello")
