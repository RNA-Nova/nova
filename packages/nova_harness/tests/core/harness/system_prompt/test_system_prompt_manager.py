"""
SystemPromptManager 与 Builder 测试。
"""

from unittest.mock import MagicMock

from nova_harness.core.harness.system_prompt import SystemPromptManager
from nova_harness.core.harness.system_prompt.builder import (
    compose_system_prompt,
    render_guidelines,
    render_tools,
)
from nova_harness.core.harness.tools import ToolsManager
from nova_harness.core.harness.tools.dynamic_tool import DynamicTool
from nova_harness.core.types.agent.config import AgentConfig, ToolInfo
from nova_harness.core.types.resources.context_files import ContextFile
from nova_harness.core.types.runtime.tools import ToolDefinition


def _make_agent_loader(config):
    loader = MagicMock()
    loader.get_agents.return_value = {config.name: config}
    loader.get_skills.return_value = {"skills": {}, "diagnostics": []}
    loader.get_context_files.return_value = []
    loader.get_tools.return_value = {
        "tools": {
            t.name: DynamicTool(ToolDefinition(name=t.name, description=t.description))
            for t in config.tools
        },
        "diagnostics": [],
    }
    return loader


def _make_manager(config, custom_tools=None, loader=None):
    """构造一个带 ToolsManager 的 SystemPromptManager。"""
    if loader is None:
        loader = _make_agent_loader(config)
    tools_manager = ToolsManager(
        resource_loader=loader,
        custom_tools=custom_tools or [],
        default_active_tools=[],
    )
    tools_manager.refresh()
    return SystemPromptManager(loader, config.name, tools_manager)


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
    bash_tool = DynamicTool(bash_def)
    loader = _make_agent_loader(
        AgentConfig(
            name="base_agent",
            agent_dir="",
            tools=[ToolInfo(name="bash", description="Run shell")],
        )
    )
    loader.get_tools.return_value = {"tools": {"bash": bash_tool}, "diagnostics": []}
    tools_manager = ToolsManager(resource_loader=loader)
    tools_manager.refresh()
    manager = SystemPromptManager(loader, "base_agent", tools_manager)

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


def test_system_prompt_manager_filters_skills_by_config():
    from nova_harness.core.types.skills import Skill

    config = AgentConfig(
        name="agent",
        agent_dir="",
        tools=[ToolInfo(name="read", description="read")],
        skills=["review", "debug"],
    )
    loader = _make_agent_loader(config)
    loader.get_skills.return_value = {
        "skills": {
            "review": Skill(
                name="review",
                description="Code review",
                file_path="/tmp/review/SKILL.md",
                base_dir="/tmp/review",
            ),
            "debug": Skill(
                name="debug",
                description="Debug",
                file_path="/tmp/debug/SKILL.md",
                base_dir="/tmp/debug",
            ),
            "write-docs": Skill(
                name="write-docs",
                description="Write docs",
                file_path="/tmp/write-docs/SKILL.md",
                base_dir="/tmp/write-docs",
            ),
        },
        "diagnostics": [],
    }
    manager = _make_manager(config, loader=loader)
    manager.set_active_tools(["read"])
    prompt = manager.build_system_prompt()
    assert "review" in prompt
    assert "debug" in prompt
    assert "write-docs" not in prompt


def test_system_prompt_manager_no_skills_when_config_empty():
    from nova_harness.core.types.skills import Skill

    config = AgentConfig(
        name="agent",
        agent_dir="",
        tools=[ToolInfo(name="read", description="read")],
    )
    loader = _make_agent_loader(config)
    loader.get_skills.return_value = {
        "skills": {
            "review": Skill(
                name="review",
                description="Code review",
                file_path="/tmp/review/SKILL.md",
                base_dir="/tmp/review",
            ),
        },
        "diagnostics": [],
    }
    manager = _make_manager(config, loader=loader)
    manager.set_active_tools(["read"])
    prompt = manager.build_system_prompt()
    assert "review" not in prompt


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
    manager = SystemPromptManager(loader, config.name, tools_manager=None)

    assert manager.get_active_tool_names() == []
    assert manager.get_available_tool_names() == []
    assert manager.get_default_active_tool_names() == ["read"]

    prompt = manager.build_system_prompt()
    assert "A simple agent." in prompt
    assert "Available Tools" in prompt
