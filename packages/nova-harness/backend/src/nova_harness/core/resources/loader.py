"""默认资源加载器实现与抽象基类。

- ``ResourceLoader``：资源加载器抽象基类。
- ``DefaultResourceLoader``：默认实现，统一调度 ``resources/loaders/`` 下的各具体加载器。

本模块假设所有资源路径都来自 ``PackageResolver``；子加载器不再自行扫描默认目录。
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.extensions.event_bus import ExtensionEventBus
from nova_harness.core.resources.loaders.agent_config import load_agent_config_from_yaml
from nova_harness.core.resources.loaders.context_files import load_project_context_files
from nova_harness.core.resources.loaders.extensions import load_extensions
from nova_harness.core.resources.loaders.personas import load_personas
from nova_harness.core.resources.loaders.prompt_templates import (
    load_prompt_templates_with_diagnostics,
)
from nova_harness.core.resources.loaders.skills import load_skills
from nova_harness.core.resources.loaders.tools import ToolLoader
from nova_harness.core.resources.loaders.user_tools import UserToolLoader
from nova_harness.core.resources.source_info import source_info_from_extension_entry
from nova_harness.core.types.extensions import (
    ExtensionRuntime,
    LoadedExtensionsResult,
    SourceInfo,
)
from nova_harness.core.types.package import (
    PathMetadata,
    ResolvedPaths,
    ResolvedResource,
    SourceOrigin,
    SourceScope,
)
from nova_harness.core.types.resources.agents import AgentConfig
from nova_harness.core.types.resources.context_files import ContextFile
from nova_harness.core.types.resources.diagnostics import (
    ResourceCollision,
    ResourceDiagnostic,
)
from nova_harness.core.types.resources.extension_paths import (
    ResourceExtensionPathEntry,
    ResourceExtensionPaths,
)
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions
from nova_harness.core.types.resources.personas import Persona
from nova_harness.core.types.resources.prompts import (
    LoadPromptTemplatesOptions,
    PromptTemplate,
)
from nova_harness.core.types.resources.skills import Skill
from nova_harness.core.types.resources.tools import (
    NULL_TOOL_SETTINGS,
    ToolContext,
    ToolDefinition,
)


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

    def get_personas(self) -> Dict[str, Any]:
        """获取已加载的 persona（key 为注册名）和诊断信息。"""
        return {"personas": {}, "diagnostics": []}

    @abstractmethod
    def get_tools(self) -> Dict[str, Any]:
        """获取已加载的工具（key 为工具名称）和诊断信息。"""

    def get_user_tools(self) -> Dict[str, Any]:
        """获取已加载的用户工具（key 为工具名称）和诊断信息。"""
        return {"user_tools": {}, "diagnostics": []}

    @abstractmethod
    def get_context_files(self) -> List[ContextFile]:
        """获取项目上下文文件列表（如 ``AGENTS.md`` / ``CLAUDE.md``）。"""

    @abstractmethod
    async def reload(self) -> None:
        """重新加载所有资源"""

    @abstractmethod
    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        """合并扩展通过 resources_discover 贡献的临时资源路径。"""


class DefaultResourceLoader(ResourceLoader):
    """默认资源加载器实现（prompt templates + extensions + agents + skills）。"""

    def __init__(self, options: DefaultResourceLoaderOptions) -> None:
        if options.package_manager is None:
            raise ValueError(
                "DefaultResourceLoader requires a package_manager; "
                "resource discovery is no longer performed by sub-loaders."
            )

        self._options = options
        self._cwd = str(
            Path(options.cwd).resolve() if options.cwd else Path.cwd().resolve()
        )
        self._agent_dir = str(
            Path(options.agent_dir).resolve()
            if options.agent_dir
            else get_agent_dir().resolve()
        )
        self._settings_manager = options.settings_manager
        self._model_runtime = options.model_runtime
        self._tool_context = options.tool_context or ToolContext(
            cwd=self._cwd,
            settings=options.settings_manager or NULL_TOOL_SETTINGS,
        )
        self._package_manager = options.package_manager
        self._install_missing_packages = options.install_missing_packages

        # 显式传入路径（CLI --skill/--prompt-template 与 SDK 注入共用
        # additional 通道，对齐 pi 的单通道设计）：最低优先层，在 resolver
        # 资源之后、扩展贡献之前加载。
        self._additional_prompt_template_paths = (
            options.additional_prompt_template_paths or []
        )
        self._additional_extension_paths = options.additional_extension_paths or []
        self._additional_skill_paths = options.additional_skill_paths or []
        self._extension_factories = options.extension_factories or []

        self._no_prompt_templates = options.no_prompt_templates or False
        self._no_extensions = options.no_extensions or False
        self._no_skills = options.no_skills or False
        self._no_tools = options.no_tools or False
        self._no_context_files = options.no_context_files or False

        # 资源覆盖回调
        self._extensions_override = options.extensions_override
        self._skills_override = options.skills_override
        self._prompts_override = options.prompts_override
        self._agents_override = options.agents_override
        self._context_files_override = options.context_files_override

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
        self._personas: Dict[str, Persona] = {}
        self._persona_diagnostics: List[ResourceDiagnostic] = []
        self._tools: Dict[str, ToolDefinition] = {}
        self._tool_diagnostics: List[ResourceDiagnostic] = []
        self._user_tools: Dict[str, Any] = {}
        self._user_tool_diagnostics: List[ResourceDiagnostic] = []
        self._context_files: List[ContextFile] = []
        self._agent_diagnostics: List[ResourceDiagnostic] = []

        # 扩展通过 resources_discover 贡献的路径与来源信息。
        # 与 SDK 的 _additional_*_paths 严格分离：这批路径的生命周期绑定
        # 扩展加载过程——全量 reload 时与 source infos 一起清空，由扩展
        # 在重新加载时通过 extend_resources 再次贡献（对齐 TS 的
        # lastSkillPaths 重建语义）。
        self._extension_skill_paths: List[str] = []
        self._extension_prompt_paths: List[str] = []
        self._extension_persona_paths: List[str] = []
        self._extension_skill_source_infos: List[SourceInfo] = []
        self._extension_prompt_source_infos: List[SourceInfo] = []
        self._extension_persona_source_infos: List[SourceInfo] = []

    @property
    def event_bus(self) -> ExtensionEventBus:
        return self._event_bus

    @property
    def tool_context(self) -> ToolContext:
        """包 LLM 工具的构造期上下文（随 loader 生命周期，会话工厂后绑定模型）。"""
        return self._tool_context

    def get_prompts(self) -> dict:
        """获取提示词模板和诊断信息"""
        return {
            "prompts": self._prompts,
            "diagnostics": self._prompt_diagnostics,
        }

    def get_extensions(self) -> LoadedExtensionsResult:
        return self._extensions_result

    def get_disabled_extension_names(self) -> set:
        """被 settings 层裁掉（``enabled=False``）的扩展注册名集合。

        settings 的 extensions pattern 是**路径级**（resolver 应用），被裁资源
        不进入加载管线；此处按 ``ExtensionLoader`` 的命名规则（目录取目录名、
        单文件取 stem）把被裁路径推导回注册名，供 CapabilitySelection 报告
        区分 ``missing`` 与 ``disabled_by_settings``。
        """
        names: set = set()
        resolved = self._resolved_paths
        if resolved is None:
            return names
        for resource in resolved.extensions:
            if resource.enabled:
                continue
            path = Path(resource.path)
            names.add(path.name if path.is_dir() else path.stem)
        return names

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

    def get_personas(self) -> Dict[str, Any]:
        return {
            "personas": self._personas,
            "diagnostics": self._persona_diagnostics,
        }

    def get_tools(self) -> Dict[str, Any]:
        return {
            "tools": dict(self._tools),
            "diagnostics": self._tool_diagnostics,
        }

    def get_user_tools(self) -> Dict[str, Any]:
        return {
            "user_tools": dict(self._user_tools),
            "diagnostics": self._user_tool_diagnostics,
        }

    def get_context_files(self) -> List[ContextFile]:
        """获取项目上下文文件列表。"""
        return list(self._context_files)

    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        """合并扩展贡献的临时资源路径并重新加载受影响资源。

        贡献进入独立的 ``_extension_*_paths``（不混入 SDK 的
        ``_additional_*_paths``），全量 reload 时清空、由扩展重新贡献。
        路径与 metadata.base_dir 统一相对 loader cwd 解析（对齐 TS
        ``normalizeExtensionPaths``）。
        """
        for entry in paths.prompt_paths:
            path = self._resolve_path(entry.path)
            if path not in self._extension_prompt_paths:
                self._extension_prompt_paths.append(path)
                self._extension_prompt_source_infos.append(
                    self._source_info_from_entry(entry, path)
                )
        for entry in paths.skill_paths:
            path = self._resolve_path(entry.path)
            if path not in self._extension_skill_paths:
                self._extension_skill_paths.append(path)
                self._extension_skill_source_infos.append(
                    self._source_info_from_entry(entry, path)
                )
        for entry in paths.persona_paths:
            path = self._resolve_path(entry.path)
            if path not in self._extension_persona_paths:
                self._extension_persona_paths.append(path)
                self._extension_persona_source_infos.append(
                    self._source_info_from_entry(entry, path)
                )
        if paths.prompt_paths:
            self._reload_prompts()
        if paths.skill_paths:
            self._reload_skills()
        if paths.persona_paths:
            self._reload_personas()

    def _source_info_from_entry(
        self, entry: ResourceExtensionPathEntry, resolved_path: str
    ) -> SourceInfo:
        """把扩展贡献的路径项转成 ``SourceInfo``，path 指向解析后的实际路径。"""
        info = source_info_from_extension_entry(entry)
        base_dir = self._resolve_path(info.base_dir) if info.base_dir else info.base_dir
        return info.model_copy(update={"path": resolved_path, "base_dir": base_dir})

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

        # 全量 reload 时清空扩展通过 resources_discover 贡献的路径与来源
        # 信息；扩展在随后的扩展加载阶段会重新 extend_resources 填充。
        self._extension_skill_paths = []
        self._extension_prompt_paths = []
        self._extension_persona_paths = []
        self._extension_skill_source_infos = []
        self._extension_prompt_source_infos = []
        self._extension_persona_source_infos = []

        self._reload_context_files()
        self._reload_prompts()
        self._reload_agents(resolved.agents)
        self._reload_skills(resolved.skills)
        self._reload_personas(resolved.personas)
        self._reload_tools(resolved.tools)
        self._reload_user_tools(resolved.user_tools)

        runtime = pre_trust_extensions.runtime if pre_trust_extensions else None
        await self._reload_extensions(
            resolved.extensions,
            runtime=runtime,
            preloaded=pre_trust_extensions,
        )

    def _resolve_path(self, raw: Union[str, Path]) -> str:
        """把显式传入的资源路径解析为绝对路径。

        相对路径相对 **loader 的 cwd** 解析（对齐 TS ``resolveResourcePath``），
        而不是进程当前工作目录——RPC 模式下两者可能不同。
        """
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = Path(self._cwd) / p
        return str(p.resolve())

    def _diagnose_missing_additional_paths(
        self,
        paths: List[str],
        diagnostics: List[ResourceDiagnostic],
        resource_kind: str,
    ) -> None:
        """检查额外资源路径中不存在的本地路径并记录 error 诊断。

        远程或带协议前缀的路径（如 ``git:``、``https://``）跳过检查，
        由 ``PackageManager`` 负责处理其可用性。同一路径已在
        *diagnostics* 中有记录（如 skill 加载阶段报过 warning）时跳过，
        避免同一路径重复报告（对齐 TS 的 ``!diagnostics.some(d => d.path === path)`` 语义）。
        """
        reported = {d.path for d in diagnostics}
        for raw in paths:
            stripped = str(raw).strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if "://" in lower or lower.startswith(("git:", "path:")):
                continue
            try:
                resolved = self._resolve_path(stripped)
                if resolved in reported:
                    continue
                if not Path(resolved).exists():
                    diagnostics.append(
                        ResourceDiagnostic(
                            category="error",
                            message=f"{resource_kind} path does not exist: {resolved}",
                            path=resolved,
                        )
                    )
            except OSError:
                pass

    def _reload_context_files(self) -> None:
        """加载项目上下文文件。

        先加载全局 ``agent_dir`` 中的上下文文件（用户级，不受项目门控），
        项目链经 ``load_project_context_files`` 收集（**git root 封顶**——
        不越出项目读祖先目录；**trust 门控**——项目不被信任时项目链不读）。
        ``no_context_files=True`` 时基础结果为空；``context_files_override``
        在基础结果之后应用（对齐 pi 的 agentsFilesOverride）——SDK 可以过滤、
        改写，也可以在禁用自动发现后注入自定义条目。
        """
        if self._no_context_files:
            context_files: List[ContextFile] = []
        else:
            context_files = load_project_context_files(
                self._cwd,
                self._agent_dir,
                project_trusted=self._settings_manager.is_project_trusted(),
            )
        if self._context_files_override is not None:
            context_files = self._context_files_override(context_files)
        self._context_files = context_files

    def _reload_prompts(self) -> None:
        """从 resolver 与额外路径加载提示词模板。

        路径优先级（对齐 pi 的单通道语义）：resolver（settings/自动发现/包）
        > additional（CLI/程序显式传入）> 扩展贡献。
        ``_no_prompt_templates=True`` 只禁用 resolver 发现的 prompts，不禁用
        显式传入的路径。
        """
        self._prompt_diagnostics = []
        self._diagnose_missing_additional_paths(
            [str(p) for p in self._additional_prompt_template_paths],
            self._prompt_diagnostics,
            "prompt template",
        )

        resolved_resources = (
            self._resolved_paths.prompts if self._resolved_paths else []
        )
        paths: List[str] = []
        if not self._no_prompt_templates:
            # 只加载 enabled 的 resolver 资源；disabled（filters/override 排除）
            # 不进入路径列表（与其他四类加载器的过滤语义一致）。
            paths.extend(r.path for r in resolved_resources if r.enabled)
        paths.extend(
            self._resolve_path(p) for p in self._additional_prompt_template_paths
        )
        paths.extend(self._extension_prompt_paths)

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
        """加载 agent 组合声明（``<name>.yaml``——一文件一 agent）。

        同名碰撞采用 first-wins：资源已按优先级排序（project 在前、
        package 在最后），先加载者胜出，后者记录 collision 诊断。
        resolver 的 provenance（scope/origin/base_dir）透传进
        ``AgentConfig.source_info``——七类资源来源跟踪齐平。
        """
        agents: Dict[str, AgentConfig] = {}
        diagnostics: List[ResourceDiagnostic] = []
        for resource in resolved_resources:
            if not resource.enabled:
                continue
            config, load_diags = load_agent_config_from_yaml(resource.path)
            diagnostics.extend(load_diags)
            if config is None:
                continue
            metadata = resource.metadata
            config.source_info = SourceInfo(
                path=resource.path,
                source=metadata.source,
                scope=getattr(metadata.scope, "value", metadata.scope),
                origin=getattr(metadata.origin, "value", metadata.origin),
                base_dir=metadata.base_dir,
            )
            existing = agents.get(config.name)
            if existing is not None:
                diagnostics.append(
                    ResourceDiagnostic(
                        category="collision",
                        message=(
                            f"Agent '{config.name}' from {resource.path} "
                            f"shadowed by {existing.agent_dir}"
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
                continue
            agents[config.name] = config
        if self._agents_override is not None:
            agents = self._agents_override(agents)
        self._agents = agents
        self._agent_diagnostics = diagnostics

    def _reload_skills(
        self, resolved_resources: Optional[List[ResolvedResource]] = None
    ) -> None:
        """加载 skill。

        路径优先级（对齐 pi 的单通道语义）：resolver > additional（CLI/SDK
        显式传入）> 扩展贡献。
        """
        effective_resources = resolved_resources or (
            self._resolved_paths.skills if self._resolved_paths else None
        )
        skills, diagnostics = load_skills(
            additional_paths=[
                self._resolve_path(p) for p in self._additional_skill_paths
            ]
            + self._extension_skill_paths,
            no_skills=self._no_skills,
            resolved_resources=effective_resources,
            extension_source_infos=self._extension_skill_source_infos,
            agent_dir=self._agent_dir,
            cwd=self._cwd,
        )
        if self._skills_override is not None:
            skills = self._skills_override(skills)
        self._skills = skills
        self._skill_diagnostics = list(diagnostics)
        self._diagnose_missing_additional_paths(
            list(self._additional_skill_paths),
            self._skill_diagnostics,
            "skill",
        )

    def _reload_personas(
        self, resolved_resources: Optional[List[ResolvedResource]] = None
    ) -> None:
        """加载 persona（人格文本资源）。

        路径优先级（与 skills 的单通道语义一致）：resolver > 扩展贡献。
        persona 无 CLI/SDK additional 通道——显式注入走 settings ``personas``
        键或扩展 ``resources_discover``。
        """
        effective_resources = resolved_resources or (
            self._resolved_paths.personas if self._resolved_paths else None
        )
        personas, diagnostics = load_personas(
            additional_paths=list(self._extension_persona_paths),
            resolved_resources=effective_resources,
            extension_source_infos=self._extension_persona_source_infos,
            agent_dir=self._agent_dir,
            cwd=self._cwd,
        )
        self._personas = personas
        self._persona_diagnostics = list(diagnostics)

    def _reload_tools(self, resolved_resources: List[ResolvedResource]) -> None:
        """加载工具。"""
        loader = ToolLoader(
            agent_dir=None,
            cwd=None,
            # 只加载 enabled 的 resolver 资源（与其他四类加载器一致）
            additional_paths=[r.path for r in resolved_resources if r.enabled],
            resolved_resources=resolved_resources,
            # 有意为空：扩展不能贡献 tools——tools 只来自已安装包与
            # settings 显式条目（与"tools 走包、不走扩展"的设计一致）。
            extension_source_infos=[],
            no_tools=self._no_tools,
            tool_context=self._tool_context,
        )
        tools = loader.load_tools()
        diagnostics = loader.get_diagnostics()
        self._tools = tools
        self._tool_diagnostics = list(diagnostics)

    def _reload_user_tools(self, resolved_resources: List[ResolvedResource]) -> None:
        """加载用户工具（与 tools 同一纪律：只来自已安装包，扩展不可贡献）。"""
        loader = UserToolLoader(
            additional_paths=[r.path for r in resolved_resources if r.enabled],
            resolved_resources=resolved_resources,
            extension_source_infos=[],
            no_user_tools=self._no_tools,
        )
        self._user_tools = loader.load_user_tools()
        self._user_tool_diagnostics = list(loader.get_diagnostics())

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
            model_runtime=self._model_runtime,
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
        """合并 resolver 结果与额外扩展路径为统一的 ``ResolvedResource`` 列表。

        显式传入的扩展路径排在最前（对齐 TS 的 CLI/temporary 层最高优先），
        与 resolver 结果重复时保留显式路径。
        """
        result: List[ResolvedResource] = []
        seen: set = set()

        for raw in self._additional_extension_paths:
            path = self._resolve_path(raw)
            if path in seen:
                continue
            seen.add(path)
            result.append(
                ResolvedResource(
                    path=path,
                    enabled=True,
                    metadata=PathMetadata(
                        source="cli",
                        scope=SourceScope.TEMPORARY,
                        origin=SourceOrigin.TOP_LEVEL,
                        base_dir=None,
                    ),
                )
            )

        for resource in resolved_resources:
            if resource.path in seen:
                continue
            seen.add(resource.path)
            result.append(resource)

        return result
