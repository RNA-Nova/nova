"""默认资源加载器实现与抽象基类。

- ``ResourceLoader``：资源加载器抽象基类。
- ``DefaultResourceLoader``：默认实现，统一调度 ``resources/loaders/`` 下的各具体加载器。

本模块假设所有资源路径都来自 ``PackageResolver``；子加载器不再自行扫描默认目录。
"""

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from nova_harness.core.extensions.event_bus import ExtensionEventBus
from nova_harness.core.package.discovery import collect_theme_entries
from nova_harness.core.resources.loaders.agent_config import (
    load_agent_config_from_dir,
    load_agent_configs,
)
from nova_harness.core.resources.loaders.context_files import load_project_context_files
from nova_harness.core.resources.loaders.extensions import load_extensions
from nova_harness.core.resources.loaders.prompt_templates import (
    load_prompt_templates_with_diagnostics,
)
from nova_harness.core.resources.loaders.skills import load_skills
from nova_harness.core.resources.loaders.tools import ToolLoader
from nova_harness.core.resources.source_info import source_info_from_extension_entry
from nova_harness.core.types.agent.config import AgentConfig
from nova_harness.core.types.extensions import (
    ExtensionRuntime,
    LoadedExtensionsResult,
    SourceInfo,
)
from nova_harness.core.types.package_manager import (
    PathMetadata,
    ResolvedPaths,
    ResolvedResource,
    SourceOrigin,
    SourceScope,
)
from nova_harness.core.types.resources.context_files import ContextFile
from nova_harness.core.types.resources.diagnostics import (
    ResourceCollision,
    ResourceDiagnostic,
)
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions
from nova_harness.core.types.resources.paths import ResourceExtensionPaths
from nova_harness.core.types.resources.prompts import (
    LoadPromptTemplatesOptions,
    PromptTemplate,
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

    def get_agent_diagnostics(self) -> List[ResourceDiagnostic]:
        """获取 agent 加载期间的诊断信息（默认无）。"""
        return []

    def get_diagnostics(self) -> List[ResourceDiagnostic]:
        """获取资源解析阶段产生的诊断信息（如缺失的 package source）。"""
        return []

    @abstractmethod
    def get_agent_names(self) -> List[str]:
        """获取可用的 agent 名称列表。"""

    @abstractmethod
    def get_skills(self) -> Dict[str, list]:
        """获取已加载的 skill（key 为 skill 名称）和诊断信息。"""

    @abstractmethod
    def get_tools(self) -> Dict[str, Any]:
        """获取已加载的工具（key 为工具名称）和诊断信息。"""

    @abstractmethod
    def get_context_files(self) -> List[ContextFile]:
        """获取项目上下文文件列表（如 ``AGENTS.md`` / ``CLAUDE.md``）。"""

    @abstractmethod
    async def reload(self) -> None:
        """重新加载所有资源"""

    @abstractmethod
    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        """合并扩展通过 resources_discover 贡献的临时资源路径。"""

    def get_themes(self) -> Dict[str, Any]:
        """获取主题资源（当前未实现，返回空字典）。"""
        return {"themes": {}, "diagnostics": []}


class DefaultResourceLoader(ResourceLoader):
    """默认资源加载器实现（prompt templates + extensions + agents + skills）。"""

    def __init__(self, options: DefaultResourceLoaderOptions) -> None:
        if options.package_manager is None:
            raise ValueError(
                "DefaultResourceLoader requires a package_manager; "
                "resource discovery is no longer performed by sub-loaders."
            )

        self._options = options
        self._cwd = str(options.cwd) if options.cwd else os.getcwd()
        self._agent_dir = (
            str(options.agent_dir) if options.agent_dir else str(get_agent_dir())
        )
        self._settings_manager = options.settings_manager
        self._model_registry = options.model_registry
        self._package_manager = options.package_manager
        self._project_trusted = options.project_trusted
        self._install_missing_packages = options.install_missing_packages

        self._additional_prompt_template_paths = (
            options.additional_prompt_template_paths or []
        )
        self._additional_extension_paths = options.additional_extension_paths or []
        self._additional_skill_paths = options.additional_skill_paths or []
        self._additional_theme_paths = options.additional_theme_paths or []
        self._extension_factories = options.extension_factories or []

        self._no_prompt_templates = options.no_prompt_templates or False
        self._no_extensions = options.no_extensions or False
        self._no_skills = options.no_skills or False
        self._no_themes = options.no_themes or False
        self._no_tools = options.no_tools or False
        self._no_context_files = options.no_context_files or False

        # 资源覆盖回调
        self._extensions_override = options.extensions_override
        self._skills_override = options.skills_override
        self._prompts_override = options.prompts_override
        self._agents_override = options.agents_override

        self._event_bus: ExtensionEventBus = (
            options.event_bus
            if isinstance(options.event_bus, ExtensionEventBus)
            else ExtensionEventBus()
        )

        self._resolved_paths: Optional[ResolvedPaths] = None
        self._resolver_diagnostics: List[ResourceDiagnostic] = []
        self._prompts: list = []
        self._prompt_diagnostics: List[ResourceDiagnostic] = []
        self._extensions_result = LoadedExtensionsResult()
        self._agents: Dict[str, AgentConfig] = {}
        self._skills: Dict[str, Skill] = {}
        self._skill_diagnostics: List[ResourceDiagnostic] = []
        self._tools: Dict[str, Any] = {}
        self._tool_diagnostics: List[ResourceDiagnostic] = []
        self._context_files: List[ContextFile] = []
        self._themes: Dict[str, Any] = {}
        self._theme_diagnostics: List[ResourceDiagnostic] = []
        self._agent_diagnostics: List[ResourceDiagnostic] = []

        # 扩展通过 resources_discover 贡献的路径来源信息，用于 skill/prompt/theme 前缀匹配
        self._extension_skill_source_infos: List[SourceInfo] = []
        self._extension_prompt_source_infos: List[SourceInfo] = []
        self._extension_theme_source_infos: List[SourceInfo] = []

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

    def get_agent_diagnostics(self) -> List[ResourceDiagnostic]:
        return list(self._agent_diagnostics)

    def get_diagnostics(self) -> List[ResourceDiagnostic]:
        """返回资源解析阶段（PackageResolver）产生的诊断信息。"""
        return list(self._resolver_diagnostics)

    def get_agent_names(self) -> List[str]:
        return sorted(self._agents.keys())

    def get_skills(self) -> Dict[str, Any]:
        return {
            "skills": self._skills,
            "diagnostics": self._skill_diagnostics,
        }

    def get_tools(self) -> Dict[str, Any]:
        return {
            "tools": dict(self._tools),
            "diagnostics": self._tool_diagnostics,
        }

    def get_context_files(self) -> List[ContextFile]:
        """获取项目上下文文件列表。"""
        return list(self._context_files)

    def get_themes(self) -> Dict[str, Any]:
        """获取主题资源（当前占位，返回空字典）。"""
        return {"themes": self._themes, "diagnostics": self._theme_diagnostics}

    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        """合并扩展贡献的临时资源路径并重新加载受影响资源。"""
        for entry in paths.prompt_paths:
            path = entry.path
            if path not in self._additional_prompt_template_paths:
                self._additional_prompt_template_paths.append(path)
                self._extension_prompt_source_infos.append(
                    source_info_from_extension_entry(entry)
                )
        for entry in paths.skill_paths:
            path = entry.path
            if path not in self._additional_skill_paths:
                self._additional_skill_paths.append(path)
                self._extension_skill_source_infos.append(
                    source_info_from_extension_entry(entry)
                )
        for entry in paths.theme_paths:
            path = entry.path
            if path not in self._additional_theme_paths:
                self._additional_theme_paths.append(path)
                self._extension_theme_source_infos.append(
                    source_info_from_extension_entry(entry)
                )

        if paths.prompt_paths:
            self._reload_prompts()
        if paths.skill_paths:
            self._reload_skills()
        if paths.theme_paths:
            self._reload_themes()

    async def load_project_trust_extensions(self) -> LoadedExtensionsResult:
        """加载供项目信任裁决使用的扩展集合。

        - 强制 SettingsManager 进入不信任状态，确保项目级扩展/包不参与本次加载。
        - 仅加载扩展，不加载 skill / prompt / agent / tool。
        - 返回的 ``LoadedExtensionsResult`` 可在最终 ``reload()`` 中复用。
        """
        # 强制 SettingsManager 进入不信任状态，确保项目级扩展/包不参与本次加载。
        if self._settings_manager is not None:
            self._settings_manager.set_project_trusted(False)
            await self._settings_manager.reload()

        resolved = await self._package_manager.resolve_resources(
            install_missing_packages=False
        )
        return await self._reload_extensions(
            resolved.extensions,
            runtime=None,
            preloaded=None,
        )

    async def reload(
        self, pre_trust_extensions: Optional[LoadedExtensionsResult] = None
    ) -> None:
        """重新加载所有资源。

        Args:
            pre_trust_extensions: pre-trust 阶段已加载的扩展结果，最终扩展加载
                会按 resolved path 复用其中的扩展与 runtime。
        """

        resolved = await self._package_manager.resolve_resources(
            install_missing_packages=self._install_missing_packages
        )
        self._resolved_paths = resolved
        self._resolver_diagnostics = list(resolved.diagnostics)

        # 全量 reload 时清空扩展通过 resources_discover 贡献的来源信息；
        # 扩展需要重新 extend_resources 才会再次填充。
        self._extension_skill_source_infos = []
        self._extension_prompt_source_infos = []
        self._extension_theme_source_infos = []

        self._reload_context_files()
        self._reload_prompts()
        self._reload_agents(resolved.agents)
        self._reload_skills(resolved.skills)
        self._reload_themes(resolved.themes)
        self._reload_tools(resolved.tools)

        runtime = pre_trust_extensions.runtime if pre_trust_extensions else None
        await self._reload_extensions(
            resolved.extensions,
            runtime=runtime,
            preloaded=pre_trust_extensions,
        )

    def _diagnose_missing_additional_paths(
        self,
        paths: List[str],
        diagnostics: List[ResourceDiagnostic],
        resource_kind: str,
    ) -> None:
        """检查额外资源路径中不存在的本地路径并记录 error 诊断。

        远程或带协议前缀的路径（如 ``git:``、``https://``）跳过检查，
        由 ``PackageManager`` 负责处理其可用性。
        """
        for raw in paths:
            stripped = raw.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if "://" in lower or lower.startswith(("git:", "path:")):
                continue
            try:
                if not Path(stripped).exists():
                    diagnostics.append(
                        ResourceDiagnostic(
                            category="error",
                            message=f"{resource_kind} path does not exist: {stripped}",
                            path=stripped,
                        )
                    )
            except OSError:
                pass

    def _reload_themes(
        self, resolved_resources: Optional[List[ResolvedResource]] = None
    ) -> None:
        """加载主题资源（.json 文件）。

        ``_no_themes=True`` 只禁用 resolver 发现的 themes，
        不禁用 ``_additional_theme_paths``（CLI/程序显式传入）。
        """
        self._themes = {}
        self._theme_diagnostics = []

        self._diagnose_missing_additional_paths(
            self._additional_theme_paths,
            self._theme_diagnostics,
            "theme",
        )

        effective_resources = []
        if not self._no_themes:
            effective_resources = (
                resolved_resources
                or (self._resolved_paths.themes if self._resolved_paths else None)
                or []
            )

        theme_paths = [r.path for r in effective_resources] + list(
            self._additional_theme_paths
        )

        for path in theme_paths:
            try:
                p = Path(path)
                if not p.exists():
                    self._theme_diagnostics.append(
                        ResourceDiagnostic(
                            category="error",
                            message=f"Theme path does not exist: {path}",
                            path=path,
                        )
                    )
                    continue

                if p.is_dir():
                    # 目录：交给发现层递归扫描，保持与 collect_theme_entries 行为一致
                    for file_path in collect_theme_entries(path):
                        self._load_theme_file(file_path)
                elif p.suffix == ".json":
                    self._load_theme_file(path)
                else:
                    self._theme_diagnostics.append(
                        ResourceDiagnostic(
                            category="warning",
                            message=f"Theme path is not a .json file or directory: {path}",
                            path=path,
                        )
                    )
            except OSError as exc:
                self._theme_diagnostics.append(
                    ResourceDiagnostic(
                        category="error",
                        message=f"Failed to load theme {path}: {exc}",
                        path=path,
                    )
                )

        self._diagnose_missing_additional_paths(
            self._additional_theme_paths,
            self._theme_diagnostics,
            "theme",
        )

    def _load_theme_file(self, path: str) -> None:
        """读取单个主题 JSON 文件并注册到 ``self._themes``。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self._theme_diagnostics.append(
                ResourceDiagnostic(
                    category="error",
                    message=f"Failed to parse theme {path}: {exc}",
                    path=path,
                )
            )
            return

        if not isinstance(data, dict):
            self._theme_diagnostics.append(
                ResourceDiagnostic(
                    category="warning",
                    message=f"Theme file {path} does not contain a JSON object",
                    path=path,
                )
            )
            return

        name = data.get("name") or Path(path).stem
        if name in self._themes:
            self._theme_diagnostics.append(
                ResourceDiagnostic(
                    category="warning",
                    message=f"Theme name collision: '{name}' is defined by multiple files",
                    path=path,
                )
            )
        theme_data = dict(data)
        theme_data["source_path"] = path
        self._themes[name] = theme_data

    def _reload_context_files(self) -> None:
        """加载项目上下文文件。

        先加载全局 ``agent_dir`` 中的上下文文件，再从 ``cwd`` 向上遍历到 git root。
        """
        if self._no_context_files:
            self._context_files = []
            return

        self._context_files = load_project_context_files(
            self._cwd,
            self._agent_dir,
            stop_at_git_root=True,
        )

    def _reload_prompts(self) -> None:
        """从 resolver 与额外路径加载提示词模板。

        ``_no_prompt_templates=True`` 只禁用 resolver 发现的 prompts，
        不禁用 ``_additional_prompt_template_paths``（CLI/程序显式传入）。
        """
        self._prompt_diagnostics = []
        self._diagnose_missing_additional_paths(
            self._additional_prompt_template_paths,
            self._prompt_diagnostics,
            "prompt template",
        )

        resolved_resources = (
            self._resolved_paths.prompts if self._resolved_paths else []
        )
        paths: List[str] = list(self._additional_prompt_template_paths)
        if not self._no_prompt_templates:
            paths = [r.path for r in resolved_resources] + paths

        result = load_prompt_templates_with_diagnostics(
            LoadPromptTemplatesOptions(
                cwd=self._cwd,
                agent_dir=self._agent_dir,
                prompt_paths=paths,
                resolved_resources=resolved_resources,
                extension_source_infos=self._extension_prompt_source_infos,
            )
        )
        if self._prompts_override is not None:
            result = self._prompts_override(result)
        self._prompts = result["prompts"]  # type: ignore[assignment]
        self._prompt_diagnostics.extend(result["diagnostics"])  # type: ignore[arg-type]

    def _reload_agents(self, resolved_resources: List[ResolvedResource]) -> None:
        """加载 agent 配置。"""
        agents: Dict[str, AgentConfig] = {}
        diagnostics: List[ResourceDiagnostic] = []
        for resource in resolved_resources:
            if not resource.enabled:
                continue
            config = load_agent_config_from_dir(resource.path)
            if config is None:
                continue
            existing = agents.get(config.name)
            if existing is not None:
                diagnostics.append(
                    ResourceDiagnostic(
                        category="collision",
                        message=(
                            f"Agent '{config.name}' from {resource.path} shadows "
                            f"previously loaded agent from {existing.agent_dir}"
                        ),
                        path=resource.path,
                        collision=ResourceCollision(
                            resource_type="agent",
                            name=config.name,
                            winner_path=existing.agent_dir,
                            loser_path=resource.path,
                        ),
                    )
                )
            agents[config.name] = config
        if self._agents_override is not None:
            agents = self._agents_override(agents)
        self._agents = agents
        self._agent_diagnostics = diagnostics

    def _reload_skills(
        self, resolved_resources: Optional[List[ResolvedResource]] = None
    ) -> None:
        """加载 skill。"""
        effective_resources = resolved_resources or (
            self._resolved_paths.skills if self._resolved_paths else None
        )
        skills, diagnostics = load_skills(
            additional_paths=self._additional_skill_paths,
            no_skills=self._no_skills,
            resolved_resources=effective_resources,
            extension_source_infos=self._extension_skill_source_infos,
        )
        if self._skills_override is not None:
            skills = self._skills_override(skills)
        self._skills = skills
        self._skill_diagnostics = list(diagnostics)
        self._diagnose_missing_additional_paths(
            self._additional_skill_paths,
            self._skill_diagnostics,
            "skill",
        )

    def _reload_tools(self, resolved_resources: List[ResolvedResource]) -> None:
        """加载工具。"""
        loader = ToolLoader(
            agent_dir=None,
            cwd=None,
            additional_paths=[r.path for r in resolved_resources],
            resolved_resources=resolved_resources,
            extension_source_infos=[],
            no_tools=self._no_tools,
        )
        tools = loader.load_tools()
        diagnostics = loader.get_diagnostics()
        self._tools = tools
        self._tool_diagnostics = list(diagnostics)

    async def _reload_extensions(
        self,
        resolved_resources: List[ResolvedResource],
        runtime: Optional[ExtensionRuntime] = None,
        preloaded: Optional[LoadedExtensionsResult] = None,
    ) -> LoadedExtensionsResult:
        """加载扩展，支持复用 pre-trust 已加载结果。

        ``_no_extensions=True`` 只禁用 resolver 发现的扩展，
        不禁用 ``_additional_extension_paths``（CLI/程序显式传入）。
        """
        effective_resources = resolved_resources if not self._no_extensions else []
        result = await load_extensions(
            cwd=self._cwd,
            agent_dir=self._agent_dir,
            model_registry=self._model_registry,
            event_bus=self._event_bus,
            resolved_paths=self._resolved_extension_resources(effective_resources),
            no_extensions=False,
            extension_api_factory=self._options.extension_api_factory,
            runtime=runtime,
            preloaded=preloaded,
            extension_factories=self._extension_factories,
        )
        missing_diagnostics: List[ResourceDiagnostic] = []
        self._diagnose_missing_additional_paths(
            self._additional_extension_paths,
            missing_diagnostics,
            "extension",
        )
        if missing_diagnostics:
            result.diagnostics = missing_diagnostics + list(result.diagnostics)
        if self._extensions_override is not None:
            result = self._extensions_override(result)
        self._extensions_result = result
        return self._extensions_result

    def _resolved_extension_resources(
        self, resolved_resources: List[ResolvedResource]
    ) -> List[ResolvedResource]:
        """合并 resolver 结果与额外扩展路径为统一的 ``ResolvedResource`` 列表。"""
        resolver_paths = {r.path for r in resolved_resources}
        result: List[ResolvedResource] = list(resolved_resources)

        for path in self._additional_extension_paths:
            if path in resolver_paths:
                continue
            resolver_paths.add(path)
            result.append(
                ResolvedResource(
                    path=path,
                    enabled=True,
                    metadata=PathMetadata(
                        source="extension",
                        scope=SourceScope.TEMPORARY,
                        origin=SourceOrigin.TOP_LEVEL,
                        base_dir=None,
                    ),
                )
            )

        return result
