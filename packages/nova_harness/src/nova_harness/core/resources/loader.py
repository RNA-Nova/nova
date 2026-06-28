"""
默认资源加载器实现与抽象基类。

- ``ResourceLoader``：资源加载器抽象基类。
- ``DefaultResourceLoader``：默认实现，统一调度 ``resources/loaders/`` 下的各具体加载器。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from nova_harness.core.resources.loaders.agent_config import load_agent_config
from nova_harness.core.resources.loaders.extensions import load_extensions
from nova_harness.core.resources.loaders.prompt_templates import (
    load_prompt_templates_with_diagnostics,
)
from nova_harness.core.resources.loaders.skills import load_skills
from nova_harness.core.resources.loaders.tools import ToolLoader
from nova_harness.core.types.agent_config import AgentConfig
from nova_harness.core.types.diagnostics import ResourceDiagnostic
from nova_harness.core.types.extensions import ExtensionEventBus, LoadedExtensionsResult
from nova_harness.core.types.resource import (
    DefaultResourceLoaderOptions,
    LoadPromptTemplatesOptions,
    PromptTemplate,
    ResourceExtensionPaths,
)
from nova_harness.core.types.skills import Skill


class ResourceLoader(ABC):
    """资源加载器抽象基类。"""

    @property
    @abstractmethod
    def event_bus(self) -> ExtensionEventBus:
        """扩展间事件总线，生命周期与 ResourceLoader 一致。"""

    @abstractmethod
    def get_prompts(self) -> dict[str, list[PromptTemplate] | list[ResourceDiagnostic]]:
        """获取提示词模板和诊断信息"""

    @abstractmethod
    def get_extensions(self) -> LoadedExtensionsResult:
        """获取已加载的扩展及其诊断信息。"""

    @abstractmethod
    def get_agents(self) -> Dict[str, AgentConfig]:
        """获取已加载的 agent 配置（key 为 agent 名称）。"""

    @abstractmethod
    def get_agent_names(self) -> List[str]:
        """获取可用的 agent 名称列表。"""

    @abstractmethod
    def get_skills(self) -> Dict[str, Skill]:
        """获取已加载的 skill（key 为 skill 名称）。"""

    @abstractmethod
    def get_tools(self) -> Dict[str, Any]:
        """获取已加载的工具（key 为工具名称）。"""

    @abstractmethod
    async def reload(self) -> None:
        """重新加载所有资源"""

    @abstractmethod
    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        """合并扩展通过 resources_discover 贡献的临时资源路径。"""

    def get_themes(self) -> Dict[str, Any]:
        """获取主题资源（当前未实现，返回空字典）。"""
        return {}


class DefaultResourceLoader(ResourceLoader):
    """默认资源加载器实现（prompt templates + extensions + agents + skills）。"""

    def __init__(self, options: DefaultResourceLoaderOptions) -> None:
        self._options = options
        self._cwd = str(options.cwd) if options.cwd else options.cwd
        self._agent_dir = (
            str(options.agent_dir) if options.agent_dir else options.agent_dir
        )
        self._settings_manager = options.settings_manager
        self._model_registry = options.model_registry
        self._additional_prompt_template_paths = (
            options.additional_prompt_template_paths or []
        )
        self._additional_extension_paths = options.additional_extension_paths or []
        self._additional_skill_paths = options.additional_skill_paths or []
        self._additional_theme_paths = options.additional_theme_paths or []
        self._additional_tool_paths = options.additional_tool_paths or []
        self._no_prompt_templates = options.no_prompt_templates or False
        self._no_extensions = options.no_extensions or False
        self._no_skills = options.no_skills or False
        self._no_themes = options.no_themes or True
        self._no_tools = options.no_tools or False
        self._themes: Dict[str, Any] = {}
        self._theme_diagnostics: List[Any] = []

        self._event_bus: ExtensionEventBus = (
            options.event_bus
            if isinstance(options.event_bus, ExtensionEventBus)
            else ExtensionEventBus()
        )

        self._prompts: list = []
        self._prompt_diagnostics: list = []
        self._extensions_result = LoadedExtensionsResult()
        self._agents: Dict[str, AgentConfig] = {}
        self._skills: Dict[str, Skill] = {}
        self._skill_diagnostics: list = []
        self._tool_loader = ToolLoader(
            agent_dir=self._agent_dir,
            cwd=self._cwd,
            additional_paths=self._additional_tool_paths,
            no_tools=self._no_tools,
        )
        self._tools: Dict[str, Any] = {}
        self._tool_diagnostics: List[ResourceDiagnostic] = []

    @property
    def event_bus(self) -> ExtensionEventBus:
        return self._event_bus

    def get_prompts(self) -> dict:
        """获取提示词模板和诊断信息"""
        return {
            "prompts": self._prompts,
            "diagnostics": self._prompt_diagnostics,
        }

    def get_extensions(self) -> LoadedExtensionsResult:
        return self._extensions_result

    def get_agents(self) -> Dict[str, AgentConfig]:
        return self._agents

    def get_agent_names(self) -> List[str]:
        return sorted(self._agents.keys())

    def get_skills(self) -> Dict[str, Skill]:
        return self._skills

    def get_tools(self) -> Dict[str, Any]:
        return dict(self._tools)

    def get_themes(self) -> Dict[str, Any]:
        """获取主题资源（当前占位，返回空字典）。"""
        return {"themes": self._themes, "diagnostics": self._theme_diagnostics}

    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        """合并扩展贡献的临时资源路径并重新加载受影响资源。"""
        for entry in paths.prompt_paths:
            path = entry.path
            if path not in self._additional_prompt_template_paths:
                self._additional_prompt_template_paths.append(path)
        for entry in paths.skill_paths:
            path = entry.path
            if path not in self._additional_skill_paths:
                self._additional_skill_paths.append(path)
        for entry in paths.theme_paths:
            path = entry.path
            if path not in self._additional_theme_paths:
                self._additional_theme_paths.append(path)
        for entry in paths.tool_paths:
            path = entry.path
            if path not in self._additional_tool_paths:
                self._additional_tool_paths.append(path)

        if paths.prompt_paths:
            self._reload_prompts()
        if paths.skill_paths:
            self._reload_skills()
        if paths.theme_paths:
            self._reload_themes()
        if paths.tool_paths:
            self._reload_tools()

    def _reload_themes(self) -> None:
        """加载主题资源（当前未实现，仅清空占位）。"""
        self._themes = {}
        self._theme_diagnostics = []

    async def reload(self) -> None:
        """重新加载所有资源"""
        self._reload_prompts()
        self._reload_agents()
        self._reload_skills()
        self._reload_tools()
        await self._reload_extensions()

    def _reload_prompts(self) -> None:
        """从路径更新提示词模板"""
        if self._no_prompt_templates and not self._additional_prompt_template_paths:
            self._prompts = []
            self._prompt_diagnostics = []
            return

        result = load_prompt_templates_with_diagnostics(
            LoadPromptTemplatesOptions(
                cwd=self._cwd,
                agent_dir=self._agent_dir,
                prompt_paths=self._additional_prompt_template_paths,
                include_defaults=not self._no_prompt_templates,
            )
        )
        self._prompts = result["prompts"]  # type: ignore[assignment]
        self._prompt_diagnostics = result["diagnostics"]  # type: ignore[assignment]

    def _reload_agents(self) -> None:
        """从全局和项目两级目录加载 agent 配置。"""
        self._agents = load_agent_config(self._cwd, self._agent_dir)

    def _reload_skills(self) -> None:
        """发现并加载 skill。"""
        self._skills, self._skill_diagnostics = load_skills(
            cwd=self._cwd,
            agent_dir=self._agent_dir,
            settings_manager=self._settings_manager,
            additional_paths=self._additional_skill_paths,
            no_skills=self._no_skills,
        )

    def _reload_tools(self) -> None:
        """发现并加载工具。"""
        self._tools = self._tool_loader.load_tools()
        self._tool_diagnostics = self._tool_loader.get_diagnostics()

    async def _reload_extensions(self) -> None:
        """发现并加载扩展。"""
        self._extensions_result = await load_extensions(
            cwd=self._cwd,
            agent_dir=self._agent_dir,
            settings_manager=self._settings_manager,
            model_registry=self._model_registry,
            event_bus=self._event_bus,
            additional_paths=self._additional_extension_paths,
            no_extensions=self._no_extensions,
            extension_api_factory=self._options.extension_api_factory,
        )
