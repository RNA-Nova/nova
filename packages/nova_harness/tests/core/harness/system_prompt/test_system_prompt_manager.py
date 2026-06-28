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
from nova_harness.core.types.agent_config import AgentConfig, ToolInfo
from nova_harness.core.types.tools import ToolDefinition


def _make_loader(agents=None, skills=None):
    loader = MagicMock()
    loader.get_agents.return_value = agents or {}
    loader.get_skills.return_value = skills or {}
    return loader


def test_system_prompt_manager_default_active_tools_intersection():
    loader = _make_agent_loader(
        AgentConfig(
            name="base_agent",
            agent_dir="",
            tools=[
                ToolInfo(name="read", description="read"),
                ToolInfo(name="bash", description="bash"),
            ],
        )
    )
    manager = SystemPromptManager(loader, "base_agent")
    assert manager.get_default_active_tool_names() == ["read", "bash"]
    assert manager.get_active_tool_names() == ["read", "bash"]


def test_system_prompt_manager_extension_tools_increase_availability():
    loader = _make_agent_loader(
        AgentConfig(
            name="base_agent",
            agent_dir="",
            tools=[ToolInfo(name="read", description="read")],
        )
    )
    manager = SystemPromptManager(loader, "base_agent")
    manager.set_extension_tools([ToolInfo(name="custom", description="custom")])
    assert "custom" in manager.get_available_tool_names()
    manager.set_active_tools(["read", "custom"])
    assert manager.get_active_tool_names() == ["read", "custom"]


def test_system_prompt_manager_set_tool_definitions_used_in_prompt():
    loader = _make_agent_loader(
        AgentConfig(
            name="base_agent",
            agent_dir="",
            tools=[ToolInfo(name="bash", description="Run shell")],
        )
    )
    manager = SystemPromptManager(loader, "base_agent")
    manager.set_active_tools(["bash"])
    manager.set_tool_definitions(
        [
            ToolDefinition(
                name="bash",
                description="Run shell",
                prompt_snippet="bash: run commands",
                prompt_guidelines=["Use absolute paths."],
            )
        ]
    )
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


# helper


def _make_agent_loader(config):
    loader = MagicMock()
    loader.get_agents.return_value = {config.name: config}
    loader.get_skills.return_value = {}
    return loader
