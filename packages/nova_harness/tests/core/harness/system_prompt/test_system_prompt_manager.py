"""
SystemPromptManager 与 Builder 测试。
"""

from unittest.mock import MagicMock

from nova_harness.core.harness.agents import AgentManager
from nova_harness.core.harness.system_prompt import SystemPromptManager
from nova_harness.core.harness.system_prompt.builder import (
    compose_system_prompt,
    render_delegation_menu,
    render_guidelines,
    render_tools,
)
from nova_harness.core.harness.tools import ToolsManager
from nova_harness.core.types.resources.agents import AgentConfig
from nova_harness.core.types.resources.context_files import ContextFile
from nova_harness.core.types.resources.tools import ToolDefinition, ToolInfo


def _make_agent_loader(config):
    loader = MagicMock()
    loader.get_agents.return_value = {config.name: config}
    loader.get_skills.return_value = {"skills": {}, "diagnostics": []}
    loader.get_context_files.return_value = []
    loader.get_tools.return_value = {
        "tools": {
            t.name: ToolDefinition(name=t.name, description=t.description)
            for t in config.tools
        },
        "diagnostics": [],
    }
    return loader


def _make_agent_manager(loader, name):
    """构造指向指定角色的 AgentManager（注册表由 loader 假件提供）。"""
    manager = AgentManager(resource_loader=loader)
    manager.change_agent(name)
    return manager


def _make_manager(config, custom_tools=None, loader=None):
    """构造一个带 ToolsManager 的 SystemPromptManager。"""
    if loader is None:
        loader = _make_agent_loader(config)
    tools_manager = ToolsManager(
        resource_loader=loader,
        custom_tools=custom_tools or [],
    )
    tools_manager.refresh()
    return SystemPromptManager(
        loader, _make_agent_manager(loader, config.name), tools_manager
    )


def test_system_prompt_manager_default_active_tools_intersection():
    manager = _make_manager(
        AgentConfig(
            name="base_agent",
            agent_dir="",
            tools=[
                ToolInfo(name="read", description="read"),
                ToolInfo(name="bash", description="bash"),
            ],
        )
    )
    assert manager.get_default_active_tool_names() == ["read", "bash"]
    assert manager.get_active_tool_names() == ["read", "bash"]


def test_system_prompt_manager_custom_tools_increase_availability():
    custom_tool_def = ToolDefinition(name="custom", description="custom")
    manager = _make_manager(
        AgentConfig(
            name="base_agent",
            agent_dir="",
            tools=[
                ToolInfo(name="read", description="read"),
                ToolInfo(name="custom", description="custom"),
            ],
        ),
        custom_tools=[custom_tool_def],
    )
    assert "custom" in manager.get_available_tool_names()
    manager.set_active_tools(["read", "custom"])
    assert manager.get_active_tool_names() == ["read", "custom"]


def test_system_prompt_manager_tool_definitions_used_in_prompt():
    bash_def = ToolDefinition(
        name="bash",
        description="Run shell",
        prompt_snippet="bash: run commands",
        prompt_guidelines=["Use absolute paths."],
    )
    loader = _make_agent_loader(
        AgentConfig(
            name="base_agent",
            agent_dir="",
            tools=[ToolInfo(name="bash", description="Run shell")],
        )
    )
    loader.get_tools.return_value = {"tools": {"bash": bash_def}, "diagnostics": []}
    tools_manager = ToolsManager(resource_loader=loader)
    tools_manager.refresh()
    manager = SystemPromptManager(
        loader, _make_agent_manager(loader, "base_agent"), tools_manager
    )

    manager.set_active_tools(["bash"])
    prompt = manager.build_system_prompt()
    assert "bash: run commands" in prompt
    assert "# Tool Guidelines" in prompt
    assert "Use absolute paths." in prompt


def test_render_tools_uses_snippet_and_filters_unselected():
    tools = [ToolInfo(name="a", description="A"), ToolInfo(name="b", description="B")]
    definitions = {
        "a": ToolDefinition(name="a", description="A", prompt_snippet="snippet A"),
    }
    md = render_tools(tools, selected_names={"a"}, tool_definitions=definitions)
    assert "snippet A" in md
    assert "**b**" not in md


def test_render_tools_includes_definition_only_tools():
    definitions = {
        "ext": ToolDefinition(
            name="ext", description="ext desc", prompt_snippet="ext snippet"
        ),
    }
    md = render_tools([], tool_definitions=definitions)
    assert "ext snippet" in md


def test_render_guidelines_empty_returns_empty():
    assert render_guidelines([]) == ""


def test_compose_system_prompt_fallback_when_no_config():
    prompt = compose_system_prompt(AgentConfig(name="x", agent_dir=""))
    assert "You are a helpful assistant." in prompt


def _skill(name: str, description: str, origin: str = "package"):
    """构造带指定来源的 Skill（origin 决定白名单是否适用）。"""
    from nova_harness.core.types.extensions import SourceInfo
    from nova_harness.core.types.resources.skills import Skill

    return Skill(
        name=name,
        description=description,
        file_path=f"/tmp/{name}/SKILL.md",
        base_dir=f"/tmp/{name}",
        source_info=SourceInfo(path=f"/tmp/{name}/SKILL.md", origin=origin),
    )


def test_system_prompt_manager_filters_package_skills_by_config():
    """包内 skill（origin=package）过 agent.yaml 白名单。"""
    config = AgentConfig(
        name="agent",
        agent_dir="",
        tools=[ToolInfo(name="read", description="read")],
        skills=["review", "debug"],
    )
    loader = _make_agent_loader(config)
    loader.get_skills.return_value = {
        "skills": {
            "review": _skill("review", "Code review"),
            "debug": _skill("debug", "Debug"),
            "write-docs": _skill("write-docs", "Write docs"),
        },
        "diagnostics": [],
    }
    manager = _make_manager(config, loader=loader)
    manager.set_active_tools(["read"])
    prompt = manager.build_system_prompt()
    assert "review" in prompt
    assert "debug" in prompt
    assert "write-docs" not in prompt


def test_system_prompt_manager_package_skills_allowed_when_config_empty():
    """名单为空时包内 skill 也进附录（默认不裁剪，与空名单全放语义一致）。"""
    config = AgentConfig(
        name="agent",
        agent_dir="",
        tools=[ToolInfo(name="read", description="read")],
    )
    loader = _make_agent_loader(config)
    loader.get_skills.return_value = {
        "skills": {"review": _skill("review", "Code review")},
        "diagnostics": [],
    }
    manager = _make_manager(config, loader=loader)
    manager.set_active_tools(["read"])
    prompt = manager.build_system_prompt()
    assert "review" in prompt


def test_system_prompt_manager_user_and_project_skills_always_allowed():
    """用户级/项目级/无来源 skill 不受白名单约束（用户与团队的技能库）。"""
    config = AgentConfig(
        name="agent",
        agent_dir="",
        tools=[ToolInfo(name="read", description="read")],
        # 白名单为空——但非包来源的 skill 应全部放行
    )
    loader = _make_agent_loader(config)
    loader.get_skills.return_value = {
        "skills": {
            "mine": _skill("mine", "我的技能", origin="auto"),
            "team": _skill("team", "团队技能", origin="top-level"),
            "plain": _skill("plain", "无来源", origin="local"),
        },
        "diagnostics": [],
    }
    manager = _make_manager(config, loader=loader)
    manager.set_active_tools(["read"])
    prompt = manager.build_system_prompt()
    assert "mine" in prompt
    assert "team" in prompt
    assert "plain" in prompt


def test_system_prompt_manager_includes_project_context_files():
    config = AgentConfig(
        name="agent",
        agent_dir="",
        tools=[ToolInfo(name="read", description="read")],
    )
    loader = _make_agent_loader(config)
    loader.get_context_files.return_value = [
        ContextFile(path="/project/AGENTS.md", content="Project rules"),
        ContextFile(path="/project/CLAUDE.md", content="Claude notes"),
    ]
    manager = _make_manager(config, loader=loader)
    manager.set_active_tools(["read"])
    prompt = manager.build_system_prompt()

    assert "<project_context>" in prompt
    assert "Project-specific instructions and guidelines:" in prompt
    assert '<project_instructions path="/project/AGENTS.md">' in prompt
    assert "Project rules" in prompt
    assert "Claude notes" in prompt
    assert "</project_context>" in prompt


def test_compose_system_prompt_renders_project_context():
    from nova_harness.core.harness.system_prompt.builder import render_project_context

    context_files = [
        ContextFile(path="/a/AGENTS.md", content="A"),
        ContextFile(path="/b/CLAUDE.md", content="B"),
    ]
    rendered = render_project_context(context_files)
    assert "<project_context>" in rendered
    assert '<project_instructions path="/a/AGENTS.md">' in rendered
    assert "A" in rendered
    assert '<project_instructions path="/b/CLAUDE.md">' in rendered
    assert "B" in rendered
    assert "</project_context>" in rendered


def test_render_project_context_empty_returns_empty():
    from nova_harness.core.harness.system_prompt.builder import render_project_context

    assert render_project_context([]) == ""


def test_system_prompt_manager_works_without_tools_manager():
    """未传入 ToolsManager 时不应抛出 AttributeError，且仍能渲染基本 prompt。"""
    config = AgentConfig(
        name="no_tools_agent",
        agent_dir="",
        description="A simple agent.",
        tools=[ToolInfo(name="read", description="read")],
    )
    loader = _make_agent_loader(config)
    manager = SystemPromptManager(
        loader, _make_agent_manager(loader, config.name), tools_manager=None
    )

    assert manager.get_active_tool_names() == []
    assert manager.get_available_tool_names() == []
    assert manager.get_default_active_tool_names() == ["read"]

    prompt = manager.build_system_prompt()
    assert "A simple agent." in prompt
    assert "Available Tools" in prompt


# ---------------------------------------------------------------------------
# 可委派 agent 菜单注入（# Available Agents——激活工具含 subagent 时渲染）
# ---------------------------------------------------------------------------


def test_render_delegation_menu_with_source_tags():
    """builder 层：菜单条目带 source 标签；空菜单不渲染。"""
    md = render_delegation_menu(
        [
            {"name": "scout", "description": "侦察", "source": "project · package"},
            {"name": "worker", "description": "", "source": ""},
        ]
    )
    assert "# Available Agents" in md
    assert "- **scout**(project · package): 侦察" in md
    # 无 source/无描述：裸名，不留空括号与冒号
    assert "- **worker**" in md
    assert "()" not in md
    assert render_delegation_menu([]) == ""


def test_compose_system_prompt_includes_delegation_menu():
    """compose 层：delegation_menu 传入即渲染段落。"""
    prompt = compose_system_prompt(
        AgentConfig(name="x", agent_dir=""),
        delegation_menu=[{"name": "scout", "description": "侦察", "source": "user"}],
    )
    assert "# Available Agents" in prompt
    assert "**scout**(user): 侦察" in prompt


def test_manager_injects_menu_only_when_subagent_active():
    """manager 层：激活工具含 subagent 时菜单出现（含 source 标签），否则无此段。"""
    from nova_harness.core.types.extensions import SourceInfo

    scout = AgentConfig(
        name="scout",
        agent_dir="",
        description="侦察",
        source_info=SourceInfo(path="/pkg/agents/scout.yaml", origin="package"),
    )
    config = AgentConfig(
        name="coding_agent",
        agent_dir="",
        tools=[
            ToolInfo(name="read", description="read"),
            ToolInfo(name="subagent", description="delegate"),
        ],
    )
    loader = _make_agent_loader(config)
    loader.get_agents.return_value = {"coding_agent": config, "scout": scout}
    manager = _make_manager(config, loader=loader)

    # subagent 在激活集 → 菜单段出现，含 source 标签
    manager.set_active_tools(["read", "subagent"])
    prompt = manager.build_system_prompt()
    assert "# Available Agents" in prompt
    assert "**scout**(temporary · package): 侦察" in prompt

    # subagent 离开激活集 → 菜单段消失（_sync_system_prompt 重建自动反映）
    manager.set_active_tools(["read"])
    prompt = manager.build_system_prompt()
    assert "# Available Agents" not in prompt
