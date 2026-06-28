"""
DefaultResourceLoader 单元测试。
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_harness.core.resources.loader import DefaultResourceLoader, ResourceLoader
from nova_harness.core.types.extensions import (
    ExtensionEventBus,
    LoadedExtensionsResult,
)
from nova_harness.core.types.resource import (
    DefaultResourceLoaderOptions,
    ResourceExtensionPathEntry,
    ResourceExtensionPaths,
)


def test_resource_loader_abstract_methods():
    """ResourceLoader 子类必须实现所有抽象方法。"""

    class PartialLoader(ResourceLoader):
        pass

    with pytest.raises(TypeError):
        PartialLoader()


def test_default_loader_init_options(tmp_path: Path):
    """DefaultResourceLoader 应正确保存各类选项。"""
    options = DefaultResourceLoaderOptions(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / ".nova" / "agent"),
        settings_manager=MagicMock(),
        model_registry=MagicMock(),
        additional_prompt_template_paths=["/prompt"],
        additional_extension_paths=["/ext"],
        additional_skill_paths=["/skill"],
        additional_theme_paths=["/theme"],
        additional_tool_paths=["/tool"],
        no_prompt_templates=True,
        no_extensions=True,
        no_skills=True,
        no_themes=False,
        no_tools=True,
        event_bus=ExtensionEventBus(),
    )
    loader = DefaultResourceLoader(options)
    assert loader._cwd == str(tmp_path)
    assert loader._agent_dir == str(tmp_path / ".nova" / "agent")
    assert loader._additional_prompt_template_paths == ["/prompt"]
    assert loader.event_bus is options.event_bus


def test_default_loader_event_bus_default():
    """未提供 event_bus 时应自动创建。"""
    loader = DefaultResourceLoader(DefaultResourceLoaderOptions())
    assert isinstance(loader.event_bus, ExtensionEventBus)


def test_default_loader_get_prompts_empty():
    """未加载时 prompts 返回空。"""
    loader = DefaultResourceLoader(DefaultResourceLoaderOptions())
    result = loader.get_prompts()
    assert result["prompts"] == []
    assert result["diagnostics"] == []


def test_default_loader_get_extensions_default():
    """默认扩展结果为空 LoadedExtensionsResult。"""
    loader = DefaultResourceLoader(DefaultResourceLoaderOptions())
    assert isinstance(loader.get_extensions(), LoadedExtensionsResult)


def test_default_loader_get_agents_and_names(tmp_path: Path):
    """_reload_agents 应从目录加载 agent 配置。"""
    agent_dir = tmp_path / ".nova" / "agent"
    agents_dir = agent_dir / "agents" / "test_agent"
    agents_dir.mkdir(parents=True)
    (agents_dir / "description.md").write_text("A test agent")

    options = DefaultResourceLoaderOptions(
        cwd=str(tmp_path),
        agent_dir=str(agent_dir),
    )
    loader = DefaultResourceLoader(options)
    loader._reload_agents()
    assert "test_agent" in loader.get_agents()
    assert loader.get_agent_names() == ["test_agent"]


def test_default_loader_get_skills(tmp_path: Path):
    """_reload_skills 应加载 skill。"""
    agent_dir = tmp_path / ".nova" / "agent"
    skills_dir = agent_dir / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill\n---\n\n# Test\n",
        encoding="utf-8",
    )

    options = DefaultResourceLoaderOptions(
        cwd=str(tmp_path),
        agent_dir=str(agent_dir),
    )
    loader = DefaultResourceLoader(options)
    loader._reload_skills()
    assert "test-skill" in loader.get_skills()


def test_default_loader_get_tools(tmp_path: Path):
    """_reload_tools 应加载工具定义。"""
    agent_dir = tmp_path / ".nova" / "agent"
    tool_dir = agent_dir / "tools" / "bash"
    tool_dir.mkdir(parents=True)
    (tool_dir / "schema.json").write_text(
        '{"name": "bash", "description": "run shell", "parameters": {"type": "object"}}'
    )
    (tool_dir / "executor.py").write_text(
        "class ToolExecutor:\n    async def execute(self, *a, **k):\n        pass\n"
    )

    options = DefaultResourceLoaderOptions(
        cwd=str(tmp_path),
        agent_dir=str(agent_dir),
    )
    loader = DefaultResourceLoader(options)
    loader._reload_tools()
    assert "bash" in loader.get_tools()


def test_default_loader_get_themes():
    """get_themes 当前返回占位结构。"""
    loader = DefaultResourceLoader(DefaultResourceLoaderOptions())
    assert loader.get_themes() == {"themes": {}, "diagnostics": []}


@pytest.mark.asyncio
async def test_default_loader_reload_calls_all(tmp_path: Path):
    """reload() 应触发所有子资源重载。"""
    options = DefaultResourceLoaderOptions(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / ".nova" / "agent"),
    )
    loader = DefaultResourceLoader(options)
    loader._reload_prompts = MagicMock()
    loader._reload_agents = MagicMock()
    loader._reload_skills = MagicMock()
    loader._reload_tools = MagicMock()
    loader._reload_extensions = AsyncMock()

    await loader.reload()

    loader._reload_prompts.assert_called_once()
    loader._reload_agents.assert_called_once()
    loader._reload_skills.assert_called_once()
    loader._reload_tools.assert_called_once()
    loader._reload_extensions.assert_awaited_once()


def test_default_loader_extend_resources(tmp_path: Path):
    """extend_resources 应合并路径并重载对应资源。"""
    options = DefaultResourceLoaderOptions(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / ".nova" / "agent"),
    )
    loader = DefaultResourceLoader(options)
    loader._reload_prompts = MagicMock()
    loader._reload_skills = MagicMock()
    loader._reload_themes = MagicMock()
    loader._reload_tools = MagicMock()

    paths = ResourceExtensionPaths(
        prompt_paths=[ResourceExtensionPathEntry(path="/new-prompt")],
        skill_paths=[ResourceExtensionPathEntry(path="/new-skill")],
        theme_paths=[ResourceExtensionPathEntry(path="/new-theme")],
        tool_paths=[ResourceExtensionPathEntry(path="/new-tool")],
    )
    loader.extend_resources(paths)

    assert "/new-prompt" in loader._additional_prompt_template_paths
    assert "/new-skill" in loader._additional_skill_paths
    assert "/new-theme" in loader._additional_theme_paths
    assert "/new-tool" in loader._additional_tool_paths
    loader._reload_prompts.assert_called_once()
    loader._reload_skills.assert_called_once()
    loader._reload_themes.assert_called_once()
    loader._reload_tools.assert_called_once()


def test_default_loader_extend_resources_no_duplicates():
    """extend_resources 不应重复添加已存在路径。"""
    options = DefaultResourceLoaderOptions(
        additional_prompt_template_paths=["/p"],
    )
    loader = DefaultResourceLoader(options)
    loader._reload_prompts = MagicMock()
    loader.extend_resources(
        ResourceExtensionPaths(prompt_paths=[ResourceExtensionPathEntry(path="/p")])
    )
    assert loader._additional_prompt_template_paths == ["/p"]


def test_default_loader_no_prompt_templates_flag():
    """no_prompt_templates=True 且无额外路径时 prompts 为空。"""
    options = DefaultResourceLoaderOptions(no_prompt_templates=True)
    loader = DefaultResourceLoader(options)
    loader._reload_prompts()
    assert loader.get_prompts()["prompts"] == []


@pytest.mark.asyncio
async def test_default_loader_reload_extensions(tmp_path: Path):
    """_reload_extensions 应调用 load_extensions 并保存结果。"""
    options = DefaultResourceLoaderOptions(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / ".nova" / "agent"),
    )
    loader = DefaultResourceLoader(options)
    fake_result = LoadedExtensionsResult(extensions=[], diagnostics=[])
    with patch(
        "nova_harness.core.resources.loader.load_extensions",
        new=AsyncMock(return_value=fake_result),
    ) as mock_load:
        await loader._reload_extensions()
    assert loader.get_extensions() is fake_result
    mock_load.assert_awaited_once()
