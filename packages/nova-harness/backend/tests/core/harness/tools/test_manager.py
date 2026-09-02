"""ToolsManager 与 DynamicTool 测试。

覆盖：
- DynamicTool 的 prepare_arguments 透传（对齐 TS wrapToolDefinition）
- 纯 AgentTool override 合成 ToolDefinition（对齐 TS definition-first）
- 过滤链与激活决策

加载器契约：``get_tools()`` 返回 ``{name: ToolDefinition}``（未包装）；
``refresh`` 统一包装为 ``DynamicTool`` 并注入 ``context_provider``。
"""

from unittest.mock import MagicMock

import pytest
from nova_agent import AgentTool, AgentToolResult
from nova_ai import TextContent
from nova_harness.core.harness.tools import ToolsManager
from nova_harness.core.harness.tools.dynamic_tool import (
    DynamicTool,
    create_tool_definition_from_agent_tool,
)
from nova_harness.core.types.resources.tools import ToolDefinition


class _EchoTool(AgentTool):
    """最简纯 AgentTool（无 _definition，模拟 SDK override）。"""

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(type="text", text="echo")], details=None
        )


def _loader(tools=None, agents=None):
    """构造 mock resource_loader：tools 为 ``{name: ToolDefinition}``。"""
    loader = MagicMock()
    loader.get_tools.return_value = {"tools": tools or {}, "diagnostics": []}
    loader.get_agents.return_value = agents or {}
    return loader


def _definition(name: str, **kwargs) -> ToolDefinition:
    return ToolDefinition(name=name, description=f"{name} tool", **kwargs)


def _dynamic(name: str, **kwargs) -> DynamicTool:
    return DynamicTool(_definition(name, **kwargs))


# -----------------------------------------------------------------------------
# DynamicTool.prepare_arguments 透传
# -----------------------------------------------------------------------------


def test_dynamic_tool_prepare_arguments_passthrough():
    """definition 提供 prepare_arguments 时透传调用（对齐 TS）。"""
    definition = ToolDefinition(
        name="t",
        description="d",
        prepare_arguments=lambda args: {**args, "prepared": True},
    )
    tool = DynamicTool(definition)
    assert tool.prepare_arguments({"a": 1}) == {"a": 1, "prepared": True}


def test_dynamic_tool_prepare_arguments_fallback_identity():
    """definition 未提供 prepare_arguments 时回退基类默认（原样返回）。"""
    tool = DynamicTool(ToolDefinition(name="t", description="d"))
    assert tool.prepare_arguments({"a": 1}) == {"a": 1}


@pytest.mark.asyncio
async def test_dynamic_tool_execute_result_normalization():
    """execute 结果归一化：str → TextContent（Python 对工具作者的宽容）。"""

    async def execute(tool_call_id, params, signal, on_update, ctx):
        return "plain text"

    tool = DynamicTool(ToolDefinition(name="t", description="d", execute=execute))
    result = await tool.execute("id1", {})
    assert result.content[0].text == "plain text"


# -----------------------------------------------------------------------------
# 纯 AgentTool override 合成 definition
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_tool_definition_from_agent_tool():
    """合成 definition 透传核心字段，来源标 sdk（对齐 TS）。

    纯 AgentTool 的 execute 是 4 参签名，合成适配器丢弃第 5 参 ctx。
    """
    echo = _EchoTool(name="echo", description="echo tool", label="Echo", parameters={})
    definition = create_tool_definition_from_agent_tool(echo)

    assert definition.name == "echo"
    assert definition.label == "Echo"
    assert definition.description == "echo tool"
    assert definition.source_info is not None
    assert definition.source_info.source == "sdk"
    assert definition.source_info.path == "<sdk:echo>"

    # 适配器接受 5 参（ctx 被丢弃），转发到纯 AgentTool 的 4 参 execute
    result = await definition.execute("id", {}, None, None, object())
    assert result.content[0].text == "echo"


def test_refresh_synthesizes_definition_for_plain_agent_tool_override():
    """base_tools_override 的纯 AgentTool 在注册表中同样 definition-first。"""
    echo = _EchoTool(name="echo", description="echo tool", label="Echo", parameters={})
    manager = ToolsManager(
        resource_loader=_loader(),
        base_tools_override={"echo": echo},
    )
    manager.refresh()

    definition = manager.get_tool_definition("echo")
    assert definition is not None
    assert definition.name == "echo"
    assert definition.source_info is not None
    assert definition.source_info.source == "sdk"

    infos = {t.name: t for t in manager.get_all_tools()}
    assert infos["echo"].source == "sdk"
    assert infos["echo"].source_path == "<sdk:echo>"
    assert infos["echo"].description == "echo tool"


def test_package_tool_keeps_real_definition_and_source():
    """包管理工具保留 loader 产出的 definition 与 package 来源。"""
    pkg_definition = _definition("bash", tool_dir="/pkg/tools/bash")
    manager = ToolsManager(resource_loader=_loader(tools={"bash": pkg_definition}))
    manager.refresh()

    definition = manager.get_tool_definition("bash")
    assert definition is pkg_definition
    assert definition.tool_dir == "/pkg/tools/bash"

    infos = {t.name: t for t in manager.get_all_tools()}
    assert infos["bash"].source == "package"
    assert infos["bash"].source_path == "/pkg/tools/bash"


# -----------------------------------------------------------------------------
# 过滤链与激活决策
# -----------------------------------------------------------------------------


def test_filter_denylist_and_allowlist():
    """denylist 优先剔除；allowlist 存在时只保留名单内。"""
    tools = {n: _definition(n) for n in ("read", "bash", "grep")}

    manager = ToolsManager(
        resource_loader=_loader(tools=tools), excluded_tool_names={"bash"}
    )
    manager.refresh()
    assert set(manager.get_available_tools()) == {"read", "grep"}

    manager = ToolsManager(
        resource_loader=_loader(tools=tools), allowed_tool_names={"read"}
    )
    manager.refresh()
    assert set(manager.get_available_tools()) == {"read"}


def test_agent_config_whitelist_filters():
    """yaml tools 名单：open（默认）只做初始激活集；strict 才裁注册表。"""
    config = MagicMock()
    config.tools = [MagicMock(name="read")]
    # MagicMock(name=...) 的 .name 是 mock 的名称而不是值，用真实对象
    tool_cfg = type("T", (), {"name": "read"})()
    config.tools = [tool_cfg]

    tools = {n: _definition(n) for n in ("read", "bash")}

    # open（默认）：注册表 = 全池，yaml 名单 = 初始激活集
    manager = ToolsManager(
        resource_loader=_loader(tools=tools, agents={"a": config}),
    )
    manager.refresh()
    assert set(manager.get_available_tools()) == {"read", "bash"}
    assert manager.get_active_tools() == ["read"]

    # strict：yaml 名单 = 注册表闸门
    strict_manager = ToolsManager(
        resource_loader=_loader(tools=tools, agents={"a": config}),
        role_boundary="strict",
    )
    strict_manager.refresh()
    assert set(strict_manager.get_available_tools()) == {"read"}


def test_active_tools_default_is_entire_registry():
    """未声明 yaml 名单（None）：默认激活 = 注册表全部（保序）。"""
    tools = {n: _definition(n) for n in ("read", "bash", "grep")}

    manager = ToolsManager(resource_loader=_loader(tools=tools))
    manager.refresh()
    assert manager.get_active_tools() == ["read", "bash", "grep"]

    # 显式指定优先（含过滤未知名称）
    manager2 = ToolsManager(resource_loader=_loader(tools=tools))
    manager2.refresh(active_tool_names=["grep", "unknown"])
    assert manager2.get_active_tools() == ["grep"]

    # 显式空列表 = 显式不激活
    manager3 = ToolsManager(resource_loader=_loader(tools=tools))
    manager3.refresh(active_tool_names=[])
    assert manager3.get_active_tools() == []


def test_explicit_empty_active_tools_means_nothing_active():
    """三态之 []：显式不激活（工具在册但激活集合为空，可后开）。"""
    tools = {n: _definition(n) for n in ("read", "bash")}
    manager = ToolsManager(resource_loader=_loader(tools=tools))
    manager.refresh(active_tool_names=[])
    assert manager.get_active_tools() == []
    # 工具仍在册（激活层关闭 ≠ 过滤层移除），之后可开启
    assert set(manager.get_available_tools()) == {"read", "bash"}
    manager.set_active_tools(["read"])
    assert manager.get_active_tools() == ["read"]


def test_custom_tools_activated_via_registry():
    """SDK custom tools 进入注册表，默认激活自然包含（无需特殊追加逻辑）。"""
    tools = {"read": _definition("read")}
    custom = [ToolDefinition(name="custom", description="custom tool")]
    manager = ToolsManager(resource_loader=_loader(tools=tools), custom_tools=custom)
    manager.refresh()
    assert set(manager.get_active_tools()) == {"read", "custom"}


# -----------------------------------------------------------------------------
# Spawn hook 注入（扩展 hooks → LLM 工具链）
# -----------------------------------------------------------------------------


class _HookAwareExecutor:
    """模拟支持 spawn hook 注入的包工具执行体（SpawnHookAware）。"""

    def __init__(self):
        self.spawn_hook = "UNSET"

    def set_spawn_hook(self, hook):
        self.spawn_hook = hook

    async def execute(
        self, tool_call_id, params, signal=None, on_update=None, ctx=None
    ):
        return AgentToolResult(
            content=[TextContent(type="text", text="ok")], details=None
        )


def _runner_with_hooks(hooks):
    runner = MagicMock()
    runner.runtime.spawn_hooks = hooks
    return runner


def _hook_aware_definition(executor):
    return ToolDefinition(
        name="bash", description="bash tool", execute=executor.execute
    )


def test_refresh_injects_extension_spawn_hooks():
    """扩展注册的 spawn hooks 聚合后注入支持 hook 的工具执行体。"""
    executor = _HookAwareExecutor()
    tools = {"bash": _hook_aware_definition(executor)}

    def h1(ctx):
        return ctx + ["h1"]

    def h2(ctx):
        return ctx + ["h2"]

    manager = ToolsManager(
        resource_loader=_loader(tools=tools),
        extension_runner=_runner_with_hooks([h1, h2]),
    )
    manager.refresh()
    # 聚合 hook 按注册顺序依次应用
    assert executor.spawn_hook([]) == ["h1", "h2"]


def test_refresh_clears_spawn_hook_when_no_extension_runner():
    """无扩展 runner 时显式清除（reload 后不留陈旧 hook）。"""
    executor = _HookAwareExecutor()
    tools = {"bash": _hook_aware_definition(executor)}
    manager = ToolsManager(resource_loader=_loader(tools), extension_runner=None)
    manager.refresh()
    assert executor.spawn_hook is None


def test_refresh_skips_tools_without_spawn_hook_support():
    """不支持 hook 的工具执行体不受影响（无 set_spawn_hook 也不报错）。"""
    tools = {"read": _definition("read")}
    manager = ToolsManager(
        resource_loader=_loader(tools=tools),
        extension_runner=_runner_with_hooks([lambda ctx: ctx]),
    )
    manager.refresh()
    assert manager.get_active_tools() == ["read"]


# -----------------------------------------------------------------------------
# settings pattern（用户终裁层）与 CapabilitySelection 报告
# -----------------------------------------------------------------------------


def test_settings_patterns_gate_registry():
    """settings tools pattern：! 排除裁注册表（用户终裁，先于 yaml）。"""
    tools = {n: _definition(n) for n in ("read", "bash", "grep")}
    manager = ToolsManager(
        resource_loader=_loader(tools=tools),
        settings_tool_patterns=["!bash"],
    )
    manager.refresh()
    assert set(manager.get_available_tools()) == {"read", "grep"}


def test_settings_empty_list_disables_all():
    """settings tools = []（显式空）：全禁。"""
    tools = {n: _definition(n) for n in ("read", "bash")}
    manager = ToolsManager(
        resource_loader=_loader(tools=tools),
        settings_tool_patterns=[],
    )
    manager.refresh()
    assert manager.get_available_tools() == []


def test_settings_force_include_revives_own_exclusion():
    """单名单内：+ 复活同层 ! 裁掉的名（宽排除 + 精确豁免）。"""
    tools = {n: _definition(n) for n in ("read", "bash", "grep")}
    manager = ToolsManager(
        resource_loader=_loader(tools=tools),
        settings_tool_patterns=["!bash", "!grep", "+grep"],
    )
    manager.refresh()
    assert set(manager.get_available_tools()) == {"read", "grep"}


def test_sdk_exclusion_wins_over_settings_force_include():
    """跨层不可复活：SDK 硬闸裁掉的，settings + 救不回。"""
    tools = {n: _definition(n) for n in ("read", "bash")}
    manager = ToolsManager(
        resource_loader=_loader(tools=tools),
        settings_tool_patterns=["!bash", "+bash"],
        excluded_tool_names={"bash"},
    )
    manager.refresh()
    assert manager.get_available_tools() == ["read"]


def _agent_config_with_tools(names):
    config = MagicMock()
    config.tools = [type("T", (), {"name": n})() for n in names]
    return config


def test_selection_report_statuses():
    """选配报告：ok / disabled_by_settings / disabled_by_sdk / missing。"""
    config = _agent_config_with_tools(["read", "bash", "grep", "ghost"])
    tools = {n: _definition(n) for n in ("read", "bash", "grep")}
    manager = ToolsManager(
        resource_loader=_loader(tools=tools, agents={"a": config}),
        settings_tool_patterns=["!bash"],
        excluded_tool_names={"grep"},
    )
    manager.refresh()
    report = {s.name: s.status for s in manager.selection_report}
    assert report == {
        "read": "ok",
        "bash": "disabled_by_settings",
        "grep": "disabled_by_sdk",
        "ghost": "missing",
    }


def test_selection_report_empty_without_yaml_list():
    """yaml 未声明 tools（None）：无点名项，报告为空。"""
    tools = {n: _definition(n) for n in ("read",)}
    manager = ToolsManager(resource_loader=_loader(tools=tools))
    manager.refresh()
    assert manager.selection_report == []
