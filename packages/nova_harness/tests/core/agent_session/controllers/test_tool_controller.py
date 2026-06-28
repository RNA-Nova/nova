"""
ToolController 单元测试。

验证工具注册表刷新、激活、查询与 DynamicTool 包装。
"""

from unittest.mock import MagicMock

import pytest

from nova_harness.core.types.tools import DynamicTool, ToolDefinition


def _make_tool(name):
    return DynamicTool(
        ToolDefinition(name=name, description=f"{name} tool", parameters={})
    )


@pytest.fixture
def tool_session(make_agent_session):
    """构造一个带 mock extension runner 的 session。"""
    sess = make_agent_session()
    runner = MagicMock()
    runner.get_extension_tools.return_value = []
    sess._extension_runner = runner
    # __init__ 期间会调用 set_active_tools([]) 与 set_tool_definitions([])，避免干扰后续断言
    sess.system_prompt_manager.set_active_tools.reset_mock()
    sess.system_prompt_manager.set_tool_definitions.reset_mock()
    sess.session_manager.append_active_tools_change.reset_mock()
    return sess


def test_refresh_registry_combines_sources(tool_session):
    """refresh_registry 应合并包工具、扩展工具与调用方覆盖。"""
    pkg_tool = _make_tool("bash")
    ext_tool = _make_tool("edit")
    override_tool = _make_tool("custom")

    tool_session.resource_loader.get_tools.return_value = {"bash": pkg_tool}
    tool_session._extension_runner.get_extension_tools.return_value = [ext_tool]
    tool_session.base_tools_override = {"custom": override_tool}

    tool_session._tools.refresh_registry()

    assert "bash" in tool_session._tool_registry
    assert "edit" in tool_session._tool_registry
    assert "custom" in tool_session._tool_registry
    assert tool_session._tool_registry["custom"] is override_tool


def test_refresh_registry_builds_definitions(tool_session):
    """DynamicTool 的工具定义应收集到 _tool_definitions。"""
    tool = _make_tool("bash")
    tool_session.resource_loader.get_tools.return_value = {"bash": tool}
    tool_session._tools.refresh_registry()
    assert tool_session._tool_definitions == {"bash": tool._definition}
    tool_session.system_prompt_manager.set_tool_definitions.assert_called_once_with(
        [tool._definition]
    )


def test_refresh_registry_active_from_previous(tool_session):
    """传入 active_tool_names 时应过滤为仍存在的工具。"""
    bash = _make_tool("bash")
    edit = _make_tool("edit")
    tool_session.resource_loader.get_tools.return_value = {"bash": bash, "edit": edit}
    tool_session._tools.refresh_registry(active_tool_names=["bash", "missing"])
    assert tool_session._tools.get_active_names() == ["bash"]


def test_refresh_registry_active_from_initial(tool_session):
    """未传 active_tool_names 时使用 initial_active_tool_names 过滤。"""
    tool_session.initial_active_tool_names = ["edit"]
    edit = _make_tool("edit")
    tool_session.resource_loader.get_tools.return_value = {"edit": edit}
    tool_session._tools.refresh_registry()
    assert tool_session._tools.get_active_names() == ["edit"]


def test_refresh_registry_active_from_override(tool_session):
    """存在 base_tools_override 时默认激活覆盖的工具。"""
    override = _make_tool("override")
    tool_session.base_tools_override = {"override": override}
    tool_session._tools.refresh_registry()
    assert tool_session._tools.get_active_names() == ["override"]


def test_refresh_registry_includes_extension_tools(tool_session):
    """include_all_extension_tools 为 True 时应把扩展工具加入激活列表。"""
    ext = _make_tool("ext")
    tool_session._extension_runner.get_extension_tools.return_value = [ext]
    tool_session._tools.refresh_registry()
    assert "ext" in tool_session._tools.get_active_names()


def test_get_active_names(tool_session):
    """get_active_names 返回 agent.state.tools 中工具的名称。"""
    tool_session.agent.state.tools = [_make_tool("a"), _make_tool("b")]
    assert tool_session._tools.get_active_names() == ["a", "b"]


def test_get_all_tools(tool_session):
    """get_all_tools 返回 ToolInfo 列表。"""
    tool_session._tool_registry = {"bash": _make_tool("bash")}
    infos = tool_session._tools.get_all_tools()
    assert len(infos) == 1
    assert infos[0].name == "bash"


def test_get_definition(tool_session):
    """get_definition 按名称返回注册表中的工具。"""
    tool = _make_tool("bash")
    tool_session._tool_registry = {"bash": tool}
    assert tool_session._tools.get_definition("bash") is tool
    assert tool_session._tools.get_definition("missing") is None


def test_refresh_reuses_active_names(tool_session):
    """refresh 应使用当前激活名称重新扫描。"""
    bash = _make_tool("bash")
    tool_session.agent.state.tools = [bash]
    tool_session.resource_loader.get_tools.return_value = {"bash": bash}
    tool_session._tools.refresh()
    assert tool_session._tools.get_active_names() == ["bash"]


def test_set_active_by_name_updates_state(tool_session):
    """set_active_by_name 应更新 agent.state.tools 与 system_prompt_manager。"""
    bash = _make_tool("bash")
    edit = _make_tool("edit")
    tool_session._tool_registry = {"bash": bash, "edit": edit}

    tool_session._tools.set_active_by_name(["bash"])

    assert tool_session.agent.state.tools == [bash]
    tool_session.system_prompt_manager.set_active_tools.assert_called_once_with(
        ["bash"]
    )
    tool_session.session_manager.append_active_tools_change.assert_called_once_with(
        ["bash"]
    )


def test_set_active_by_name_skips_unknown(tool_session):
    """未知工具名称应被忽略。"""
    bash = _make_tool("bash")
    tool_session._tool_registry = {"bash": bash}
    tool_session._tools.set_active_by_name(["bash", "missing"])
    assert tool_session.agent.state.tools == [bash]
