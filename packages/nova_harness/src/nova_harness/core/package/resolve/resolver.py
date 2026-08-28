"""运行时资源解析器。

将安装层（`core.package.installer`）与运行时资源发现解耦：
`PackageResolver` 负责根据 settings、已安装包和自动发现规则，
输出带来源元数据的资源路径列表，供 `ResourceLoader` 加载具体内容。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from nova_harness.core.config.defaults import (
    GIT_PACKAGES_DIR_NAME,
    PACKAGES_DIR_NAME,
    PATH_PACKAGES_DIR_NAME,
    get_project_base_dir,
)
from nova_harness.core.package.discovery import (
    RESOURCE_DISCOVERY,
    apply_patterns,
    collect_ancestor_agents_skills_dirs,
    collect_package_entries,
    is_glob_pattern,
    is_override_pattern,
)
from nova_harness.core.package.locator import SourceResolver
from nova_harness.core.package.metadata.pyproject import resolve_extension_entries
from nova_harness.core.package.metadata.validation import is_extension_path
from nova_harness.core.package.source import (
    PackageSourceCollection,
    ResolvedScopedSources,
    parse_source,
)
from nova_harness.core.package.store import (
    _find_installed_metadata_by_source,
)
from nova_harness.core.package.utils.ignore import (
    is_ignored_by_specs,
    load_ignore_specs,
)
from nova_harness.core.types.config.settings import PackageSourceSpec, Settings
from nova_harness.core.types.package_manager import (
    RESOURCE_TYPE_DIRS,
    NovaManifest,
    PackageFilter,
    PathMetadata,
    ProgressEvent,
    ResolvedPaths,
    ResolvedResource,
    ResourceType,
    SourceOrigin,
    SourceScope,
)
from nova_harness.core.types.project_trust import ProjectNotTrustedError
from nova_harness.core.types.protocols import SettingsReaderProtocol
from nova_harness.core.types.resources.diagnostics import ResourceDiagnostic

logger = logging.getLogger(__name__)


def resource_precedence_rank(metadata: PathMetadata) -> int:
    """计算资源优先级，数值越小优先级越高。

    优先级（高 -> 低）：
    0: project + settings 显式条目 (source="local", scope="project")
    1: project + 自动发现 (source="auto", scope="project")
    2: user + settings 显式条目 (source="local", scope="user")
    3: user + 自动发现 (source="auto", scope="user")
    4: package 贡献 (origin="package")
    """
    if metadata.origin == SourceOrigin.PACKAGE:
        return 4
    scope_base = 0 if metadata.scope == SourceScope.PROJECT else 2
    return scope_base + (0 if metadata.source == "local" else 1)


def sort_resolved_resources(
    resources: List[ResolvedResource],
) -> List[ResolvedResource]:
    """按优先级排序资源，同优先级保持原始发现顺序。

    Python 的 ``sorted`` 是稳定排序，因此同优先级资源保持原始发现顺序。
    """
    return sorted(resources, key=lambda r: resource_precedence_rank(r.metadata))


def build_path_metadata(
    *,
    source: str,
    scope: SourceScope,
    origin: SourceOrigin,
    base_dir: Optional[str] = None,
) -> PathMetadata:
    """构造 PathMetadata 的便捷函数。"""
    return PathMetadata(
        source=source,
        scope=scope,
        origin=origin,
        base_dir=base_dir,
    )


def _is_type_disabled(filter_list: Optional[List[str]]) -> bool:
    """空列表表示显式禁用该资源类型；None 表示未声明（默认启用）。"""
    return filter_list is not None and len(filter_list) == 0


def _parse_package_source_spec(
    spec: PackageSourceSpec,
) -> tuple[str, bool, PackageFilter]:
    """解析 settings 中的 package source spec。"""
    from nova_harness.core.package.source import parse_package_source_spec

    return parse_package_source_spec(spec)


class PackageResolver:
    """根据 settings、已安装包和自动发现规则解析运行时资源路径。"""

    def __init__(
        self,
        *,
        cwd: str,
        agent_dir: str,
        settings_manager: SettingsReaderProtocol,
        project_trusted: Optional[bool] = None,
        on_progress: Optional[Callable[[ProgressEvent], None]] = None,
    ) -> None:
        self._cwd = str(Path(cwd).resolve())
        self._agent_dir = str(Path(agent_dir).resolve())
        self._project_base_dir = str(get_project_base_dir(cwd))
        self._settings_manager = settings_manager
        self._project_trusted = project_trusted
        self._on_progress = on_progress

        # 临时 scope 使用隔离目录，避免与用户已安装包冲突或污染。
        self._temp_root = Path(self._agent_dir) / "tmp" / "extensions"

        # 每个 scope 有独立的 SourceResolver 与安装根目录，因为 git 缓存根目录不同。
        # SourceResolver 需要 cwd，以便相对本地路径按 PackageManager 的 cwd 解析，
        # 而不是依赖进程当前工作目录。
        self._source_resolvers: Dict[SourceScope, SourceResolver] = {
            SourceScope.USER: SourceResolver(
                Path(self._agent_dir), Path(self._cwd), on_progress=on_progress
            ),
            SourceScope.PROJECT: SourceResolver(
                Path(self._project_base_dir),
                Path(self._cwd),
                on_progress=on_progress,
            ),
            SourceScope.TEMPORARY: SourceResolver(
                Path(self._temp_root), Path(self._cwd), on_progress=on_progress
            ),
        }
        self._path_roots: Dict[SourceScope, Path] = {
            SourceScope.USER: Path(self._agent_dir)
            / PACKAGES_DIR_NAME
            / PATH_PACKAGES_DIR_NAME,
            SourceScope.PROJECT: Path(self._project_base_dir)
            / PACKAGES_DIR_NAME
            / PATH_PACKAGES_DIR_NAME,
            SourceScope.TEMPORARY: self._temp_root / PATH_PACKAGES_DIR_NAME,
        }
        self._git_roots: Dict[SourceScope, Path] = {
            SourceScope.USER: Path(self._agent_dir)
            / PACKAGES_DIR_NAME
            / GIT_PACKAGES_DIR_NAME,
            SourceScope.PROJECT: Path(self._project_base_dir)
            / PACKAGES_DIR_NAME
            / GIT_PACKAGES_DIR_NAME,
            SourceScope.TEMPORARY: self._temp_root / GIT_PACKAGES_DIR_NAME,
        }

    def _is_project_trusted(self) -> bool:
        """按优先级获取项目信任状态。

        - ``project_trusted=True`` 强制信任；
        - ``project_trusted=None`` 完全由 ``settings_manager`` 决定；
        - ``project_trusted=False`` 视为初始不信任，但允许 ``settings_manager``
          在运行时被更新为信任（例如 AgentSessionServices 的信任裁决流程）。
        """
        if self._project_trusted is True:
            return True
        return bool(self._settings_manager.is_project_trusted())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def resolve(
        self,
        fallback_packages: Optional[Dict[SourceScope, List[str]]] = None,
        scoped_packages: Optional[ResolvedScopedSources] = None,
    ) -> ResolvedPaths:
        """解析所有资源路径。

        Args:
            fallback_packages: 按 scope 分组的额外 package source 列表，作为
                settings 中 packages 的兜底补充（例如已安装但未写入 settings 的包）。
            scoped_packages: 已经由调用方（如 ``PackageManager``）解析好的跨 scope
                package sources。提供时不再重复读取 settings，避免内部状态不一致。
        """
        accumulator: Dict[ResourceType, Dict[str, ResolvedResource]] = {
            rt: {} for rt in ResourceType
        }
        diagnostics: List[ResourceDiagnostic] = []

        project_trusted = self._is_project_trusted()
        project_settings = (
            self._settings_manager.get_project_settings()
            if project_trusted
            else Settings()
        )
        global_settings = self._settings_manager.get_global_settings()

        # 如果调用方已经提供了跨 scope 去重后的 package sources，直接使用；
        # 否则从 settings 读取，并信任门控 project scope。
        if scoped_packages is not None:
            user_packages = scoped_packages.user
            project_packages = scoped_packages.project
        else:
            user_packages = self._settings_manager.get_package_sources(
                local=False, base_dir=self._agent_dir
            )
            project_packages = (
                self._settings_manager.get_package_sources(
                    local=True, base_dir=self._project_base_dir
                )
                if project_trusted
                else []
            )

        fallback_packages = fallback_packages or {}

        # 资源优先级由 _add_resource 根据 PathMetadata 的 precedence rank 决定，
        # 因此写入顺序不再重要。为可读性按来源分组：
        # packages -> settings 直接条目 -> auto-discovery。
        # 同 scope 内 project 覆盖 user。

        # 1. packages (project first so cwd resources win collisions)
        await self._resolve_packages(
            project_packages,
            SourceScope.PROJECT,
            accumulator,
            diagnostics=diagnostics,
        )
        await self._resolve_packages(
            fallback_packages.get(SourceScope.PROJECT, []),
            SourceScope.PROJECT,
            accumulator,
            diagnostics=diagnostics,
        )
        await self._resolve_packages(
            user_packages,
            SourceScope.USER,
            accumulator,
            diagnostics=diagnostics,
        )
        await self._resolve_packages(
            fallback_packages.get(SourceScope.USER, []),
            SourceScope.USER,
            accumulator,
            diagnostics=diagnostics,
        )

        # 2. settings 直接资源列表 (project first)
        self._resolve_direct_entries(
            project_settings.extensions or [],
            ResourceType.EXTENSIONS,
            SourceScope.PROJECT,
            accumulator,
        )
        self._resolve_direct_entries(
            project_settings.skills or [],
            ResourceType.SKILLS,
            SourceScope.PROJECT,
            accumulator,
        )
        self._resolve_direct_entries(
            project_settings.prompts or [],
            ResourceType.PROMPTS,
            SourceScope.PROJECT,
            accumulator,
        )
        self._resolve_direct_entries(
            project_settings.themes or [],
            ResourceType.THEMES,
            SourceScope.PROJECT,
            accumulator,
        )
        self._resolve_direct_entries(
            project_settings.agents or [],
            ResourceType.AGENTS,
            SourceScope.PROJECT,
            accumulator,
        )
        self._resolve_direct_entries(
            project_settings.ui_blocks or [],
            ResourceType.UI_BLOCKS,
            SourceScope.PROJECT,
            accumulator,
        )
        self._resolve_direct_entries(
            global_settings.extensions or [],
            ResourceType.EXTENSIONS,
            SourceScope.USER,
            accumulator,
        )
        self._resolve_direct_entries(
            global_settings.skills or [],
            ResourceType.SKILLS,
            SourceScope.USER,
            accumulator,
        )
        self._resolve_direct_entries(
            global_settings.prompts or [],
            ResourceType.PROMPTS,
            SourceScope.USER,
            accumulator,
        )
        self._resolve_direct_entries(
            global_settings.themes or [],
            ResourceType.THEMES,
            SourceScope.USER,
            accumulator,
        )
        self._resolve_direct_entries(
            global_settings.agents or [],
            ResourceType.AGENTS,
            SourceScope.USER,
            accumulator,
        )
        self._resolve_direct_entries(
            global_settings.ui_blocks or [],
            ResourceType.UI_BLOCKS,
            SourceScope.USER,
            accumulator,
        )

        # 3. 自动发现 (project first)
        if self._is_project_trusted():
            self._resolve_auto_discovery(SourceScope.PROJECT, accumulator)
        self._resolve_auto_discovery(SourceScope.USER, accumulator)

        # 4. 排序并输出
        return ResolvedPaths(
            extensions=sort_resolved_resources(
                list(accumulator[ResourceType.EXTENSIONS].values())
            ),
            skills=sort_resolved_resources(
                list(accumulator[ResourceType.SKILLS].values())
            ),
            prompts=sort_resolved_resources(
                list(accumulator[ResourceType.PROMPTS].values())
            ),
            themes=sort_resolved_resources(
                list(accumulator[ResourceType.THEMES].values())
            ),
            tools=sort_resolved_resources(
                list(accumulator[ResourceType.TOOLS].values())
            ),
            agents=sort_resolved_resources(
                list(accumulator[ResourceType.AGENTS].values())
            ),
            ui_blocks=sort_resolved_resources(
                list(accumulator[ResourceType.UI_BLOCKS].values())
            ),
            diagnostics=diagnostics,
        )

    def resolve_extension_sources(
        self,
        sources: List[str],
        *,
        temporary: bool = False,
        local: bool = False,
    ) -> ResolvedPaths:
        """解析一组临时/CLI 传入的扩展 source。

        每个 source 如果本身就是合法扩展路径（文件 ``extension.py`` 或含 ``extension.py``
        的目录），直接作为扩展返回；否则视为包根目录，扫描其 ``extensions/`` 子目录。
        """
        scope = (
            SourceScope.TEMPORARY
            if temporary
            else SourceScope.PROJECT if local else SourceScope.USER
        )
        if scope == SourceScope.PROJECT and not self._is_project_trusted():
            raise ProjectNotTrustedError(self._cwd)

        accumulator: Dict[ResourceType, Dict[str, ResolvedResource]] = {
            rt: {} for rt in ResourceType
        }
        source_resolver = self._source_resolvers[scope]

        diagnostics: List[ResourceDiagnostic] = []

        for source in sources:
            try:
                source_obj = parse_source(source)
                local_dir = source_resolver.resolve(source_obj)
                resolved = str(Path(local_dir).resolve())

                # 如果 source 本身是目录且声明了 [tool.nova.extensions]，按 manifest 展开。
                manifest_entries = resolve_extension_entries(local_dir)
                if manifest_entries is not None:
                    from nova_harness.core.package.discovery import _collect_explicit

                    resolved_entries = _collect_explicit(
                        manifest_entries,
                        Path(local_dir),
                        resource_type=ResourceType.EXTENSIONS,
                    )
                    for entry_path in resolved_entries or []:
                        entry_obj = Path(entry_path)
                        base_dir = (
                            str(entry_obj.parent) if entry_obj.is_file() else entry_path
                        )
                        self._add_resource(
                            accumulator,
                            ResourceType.EXTENSIONS,
                            ResolvedResource(
                                path=entry_path,
                                enabled=True,
                                metadata=build_path_metadata(
                                    source=source_obj.spec,
                                    scope=scope,
                                    origin=SourceOrigin.TOP_LEVEL,
                                    base_dir=base_dir,
                                ),
                            ),
                        )
                    continue

                if is_extension_path(local_dir):
                    path_obj = Path(resolved)
                    base_dir = str(path_obj.parent) if path_obj.is_file() else resolved
                    self._add_resource(
                        accumulator,
                        ResourceType.EXTENSIONS,
                        ResolvedResource(
                            path=resolved,
                            enabled=True,
                            metadata=build_path_metadata(
                                source=source_obj.spec,
                                scope=scope,
                                origin=SourceOrigin.TOP_LEVEL,
                                base_dir=base_dir,
                            ),
                        ),
                    )
                    continue

                self._resolve_single_package_source(
                    source,
                    scope,
                    PackageFilter(),
                    accumulator,
                )
            except ProjectNotTrustedError:
                raise
            except Exception as exc:
                message = f"Skipping extension source {source}: {exc}"
                logger.warning(message, exc_info=True)
                diagnostics.append(
                    ResourceDiagnostic(
                        category="warning", message=str(exc), path=source
                    )
                )

        return ResolvedPaths(
            extensions=self._sort_and_dedup(
                list(accumulator[ResourceType.EXTENSIONS].values())
            ),
            skills=self._sort_and_dedup(
                list(accumulator[ResourceType.SKILLS].values())
            ),
            prompts=self._sort_and_dedup(
                list(accumulator[ResourceType.PROMPTS].values())
            ),
            themes=self._sort_and_dedup(
                list(accumulator[ResourceType.THEMES].values())
            ),
            tools=self._sort_and_dedup(list(accumulator[ResourceType.TOOLS].values())),
            agents=self._sort_and_dedup(
                list(accumulator[ResourceType.AGENTS].values())
            ),
            ui_blocks=self._sort_and_dedup(
                list(accumulator[ResourceType.UI_BLOCKS].values())
            ),
            diagnostics=diagnostics,
        )

    def _sort_and_dedup(
        self, resources: List[ResolvedResource]
    ) -> List[ResolvedResource]:
        """按优先级排序并按规范路径去重。"""
        sorted_resources = sort_resolved_resources(resources)
        seen: Set[str] = set()
        result: List[ResolvedResource] = []
        for resource in sorted_resources:
            canonical = str(Path(resource.path).resolve())
            if canonical in seen:
                continue
            seen.add(canonical)
            result.append(resource)
        return result

    def _scope_base_dir(self, scope: SourceScope) -> Path:
        """返回指定 scope 的资源基准目录。"""
        if scope == SourceScope.PROJECT:
            return Path(self._project_base_dir)
        return Path(self._agent_dir)

    # ------------------------------------------------------------------
    # Package source resolution
    # ------------------------------------------------------------------
    async def _resolve_packages(
        self,
        packages: Optional[List[PackageSourceSpec]],
        scope: SourceScope,
        accumulator: Dict[ResourceType, Dict[str, ResolvedResource]],
        diagnostics: Optional[List[ResourceDiagnostic]] = None,
    ) -> None:
        """解析一组 package source，将其资源写入 accumulator。

        解析失败时把诊断信息追加到 *diagnostics*，而不是静默跳过，便于上层
        （CLI/TUI）向用户展示原因。
        """
        if not packages:
            return

        for spec in packages:
            try:
                source, editable, filter_obj = _parse_package_source_spec(spec)
            except ValueError as exc:
                message = f"Invalid package spec: {exc}"
                logger.warning(message)
                if diagnostics is not None:
                    diagnostics.append(
                        ResourceDiagnostic(
                            category="error", message=str(exc), path=str(spec)
                        )
                    )
                continue

            try:
                await asyncio.to_thread(
                    self._resolve_single_package_source,
                    source,
                    scope,
                    filter_obj,
                    accumulator,
                    diagnostics=diagnostics,
                    editable=editable,
                )
            except Exception as exc:
                message = f"Skipping package {source}: {exc}"
                logger.warning(message)
                if diagnostics is not None:
                    diagnostics.append(
                        ResourceDiagnostic(
                            category="error", message=str(exc), path=source
                        )
                    )

    def _resolve_single_package_source(
        self,
        source: str,
        scope: SourceScope,
        filter_obj: PackageFilter,
        accumulator: Dict[ResourceType, Dict[str, ResolvedResource]],
        diagnostics: Optional[List[ResourceDiagnostic]] = None,
        editable: bool = False,
    ) -> None:
        """解析单个 package source 并收集其资源。"""
        source_obj = parse_source(source)
        source_obj.editable = editable

        # 对已安装的 path 包，优先使用 Nova 管理目录下的副本/符号链接，
        # 而不是原始源路径。这样普通模式复制安装才有意义，且原始源删除后仍能工作。
        local_dir: Optional[str] = None
        if source_obj.type == "path":
            meta = _find_installed_metadata_by_source(
                self._path_roots[scope],
                source_obj.spec,
                base_dir=str(self._scope_base_dir(scope)),
            )
            if meta is not None:
                local_dir = meta.install_path

        if local_dir is None:
            try:
                local_dir = self._source_resolvers[scope].resolve(source_obj)
            except Exception as exc:
                message = f"Failed to resolve package source {source}: {exc}"
                logger.warning(message, exc_info=True)
                if diagnostics is not None:
                    diagnostics.append(
                        ResourceDiagnostic(
                            category="error", message=str(exc), path=source
                        )
                    )
                return

        if local_dir is None or not Path(local_dir).exists():
            message = f"Package source not available: {source}"
            logger.warning(message)
            if diagnostics is not None:
                diagnostics.append(
                    ResourceDiagnostic(category="error", message=message, path=source)
                )
            return

        from nova_harness.core.package.metadata.pyproject import read_manifest

        manifest = read_manifest(local_dir)
        nova = manifest.nova
        pkg_name = manifest.name or os.path.basename(os.path.normpath(local_dir))

        # 所有通过 packages settings 引入的来源都视为 package origin，
        # package 资源优先级最低（参见 precedence.py）。
        origin = SourceOrigin.PACKAGE
        source_label = source_obj.spec

        for resource_type in ResourceType:
            filter_list = getattr(filter_obj, resource_type.value)
            if filter_list is None:
                # 未声明 filter：默认启用所有资源。
                entries = self._collect_package_entries(
                    local_dir, resource_type, nova, None
                )
                for entry_path in entries:
                    self._add_resource(
                        accumulator,
                        resource_type,
                        ResolvedResource(
                            path=str(Path(entry_path).resolve()),
                            enabled=True,
                            metadata=build_path_metadata(
                                source=source_label,
                                scope=scope,
                                origin=origin,
                                base_dir=local_dir,
                            ),
                        ),
                    )
                continue

            if len(filter_list) == 0:
                # 空数组表示显式禁用该资源类型：资源保留在结果中，但 enabled=False。
                entries = self._collect_package_entries(
                    local_dir, resource_type, nova, None
                )
                for entry_path in entries:
                    self._add_resource(
                        accumulator,
                        resource_type,
                        ResolvedResource(
                            path=str(Path(entry_path).resolve()),
                            enabled=False,
                            metadata=build_path_metadata(
                                source=source_label,
                                scope=scope,
                                origin=origin,
                                base_dir=local_dir,
                            ),
                        ),
                    )
                continue

            # 非空 filter：返回全部候选路径，命中的 enabled=True，未命中的 enabled=False。
            all_paths = self._collect_package_entries(
                local_dir, resource_type, nova, None
            )
            enabled_paths = apply_patterns(all_paths, filter_list, local_dir)
            for entry_path in all_paths:
                self._add_resource(
                    accumulator,
                    resource_type,
                    ResolvedResource(
                        path=str(Path(entry_path).resolve()),
                        enabled=str(Path(entry_path).resolve()) in enabled_paths,
                        metadata=build_path_metadata(
                            source=source_label,
                            scope=scope,
                            origin=origin,
                            base_dir=local_dir,
                        ),
                    ),
                )

    def _collect_package_entries(
        self,
        package_dir: str,
        resource_type: ResourceType,
        nova: Optional[NovaManifest],
        filter_list: Optional[List[str]],
    ) -> Set[str]:
        """Collect package entries and apply optional filter patterns."""
        all_paths = collect_package_entries(package_dir, resource_type, nova)
        if not filter_list:
            return set(all_paths)
        return apply_patterns(all_paths, filter_list, package_dir)

    # ------------------------------------------------------------------
    # Direct entries and auto-discovery
    # ------------------------------------------------------------------
    def _resolve_direct_entries(
        self,
        entries: List[str],
        resource_type: ResourceType,
        scope: SourceScope,
        accumulator: Dict[ResourceType, Dict[str, ResolvedResource]],
    ) -> None:
        """解析 settings 中的直接资源列表（如 extensions/skills/prompts/agents）。

        entries 被视为相对于 base_dir 的显式路径或 glob。普通路径/glob 用于包含
        资源；``!pattern`` 排除；``+path`` 强制包含精确路径；``-path`` 强制排除
        精确路径。所有发现的路径都保留在结果中，被排除的标记为
        ``enabled=False``，而不是从 accumulator 中移除。
        """
        if not entries:
            return

        base_dir = self._scope_base_dir(scope)

        # 拆分为普通路径/glob 和 override 模式
        plain_entries: List[str] = []
        pattern_entries: List[str] = []
        for entry in entries:
            if is_override_pattern(entry):
                pattern_entries.append(entry)
            else:
                plain_entries.append(entry)

        if plain_entries:
            all_paths = self._collect_files_from_paths(
                plain_entries, resource_type, str(base_dir)
            )
        else:
            # 只有 override 模式时：不扫描标准目录，由 auto-discovery 阶段处理。
            all_paths = []

        # 注意：仅含 override 模式时 all_paths 为空，apply_patterns 结果也为空，
        # 不会向 accumulator 写入任何内容。override 效果在 _resolve_auto_discovery
        # 中生效。
        enabled_paths = apply_patterns(all_paths, pattern_entries, str(base_dir))

        for path in all_paths:
            resolved = str(Path(path).resolve())
            self._add_resource(
                accumulator,
                resource_type,
                ResolvedResource(
                    path=resolved,
                    enabled=resolved in enabled_paths,
                    metadata=build_path_metadata(
                        source="local",
                        scope=scope,
                        origin=SourceOrigin.TOP_LEVEL,
                        base_dir=str(base_dir),
                    ),
                ),
            )

    def _resolve_auto_discovery(
        self,
        scope: SourceScope,
        accumulator: Dict[ResourceType, Dict[str, ResolvedResource]],
    ) -> None:
        """自动发现标准目录下的资源，并应用 settings 中的 override 模式。"""
        base_dir = self._scope_base_dir(scope)

        project_settings = (
            self._settings_manager.get_project_settings()
            if self._is_project_trusted()
            else Settings()
        )
        global_settings = self._settings_manager.get_global_settings()
        settings = project_settings if scope == SourceScope.PROJECT else global_settings

        for resource_type in ResourceType:
            if resource_type == ResourceType.TOOLS:
                # tools 不通过顶层目录自动发现，只来自已安装包或 settings 显式条目。
                continue

            std_dir = base_dir / RESOURCE_TYPE_DIRS[resource_type]
            all_paths: List[str] = []
            if std_dir.exists():
                # prompts/themes 与 agents/skills/extensions 一样递归扫描子目录，
                # 与 manifest 显式目录行为一致。
                all_paths = list(RESOURCE_DISCOVERY[resource_type](str(std_dir)))

            # project scope 额外收集祖先目录的 .agents/skills。
            if resource_type == ResourceType.SKILLS and scope == SourceScope.PROJECT:
                all_paths.extend(
                    collect_ancestor_agents_skills_dirs(
                        str(self._cwd), stop_at_git_root=True
                    )
                )

            # user scope 额外收集 ~/.agents/skills。
            if resource_type == ResourceType.SKILLS and scope == SourceScope.USER:
                user_agents_skills = Path.home() / ".agents" / "skills"
                if user_agents_skills.exists() and user_agents_skills.is_dir():
                    all_paths.append(str(user_agents_skills.resolve()))

            if not all_paths:
                continue

            pattern_base_dir = str(base_dir)

            # 应用 settings 中的 override 模式（!/+/-）到自动发现结果。
            overrides = [
                e
                for e in (getattr(settings, resource_type.value, None) or [])
                if is_override_pattern(e)
            ]
            enabled_paths = apply_patterns(all_paths, overrides, pattern_base_dir)

            for path in all_paths:
                self._add_resource(
                    accumulator,
                    resource_type,
                    ResolvedResource(
                        path=str(Path(path).resolve()),
                        enabled=path in enabled_paths,
                        metadata=build_path_metadata(
                            source="auto",
                            scope=scope,
                            origin=SourceOrigin.TOP_LEVEL,
                            base_dir=pattern_base_dir,
                        ),
                    ),
                )

    def _collect_files_from_paths(
        self,
        entries: List[str],
        resource_type: ResourceType,
        base_dir: str,
    ) -> List[str]:
        """把 entries 解析为显式路径/glob，并收集该资源类型的文件。

        支持：
        - 相对路径（相对于 base_dir）
        - 绝对路径
        - glob 通配符（用 pathlib.Path.glob 展开），结果会应用 base_dir 下的 ignore 规则
        - 文件路径直接加入
        - 目录路径递归扫描标准资源
        """
        base = Path(base_dir)
        result: List[str] = []
        seen: Set[str] = set()
        ignore_specs = load_ignore_specs(str(base.resolve())) if base.exists() else []

        for entry in entries:
            expanded = base / entry
            expanded = expanded.expanduser()

            # glob 模式：在 base_dir 下展开，再应用 ignore 过滤。
            if is_glob_pattern(entry):
                for matched in base.glob(entry):
                    abs_matched = str(matched.resolve())
                    if abs_matched in seen:
                        continue
                    rel_matched = matched.relative_to(base)
                    if is_ignored_by_specs(
                        str(rel_matched), is_dir=matched.is_dir(), specs=ignore_specs
                    ):
                        continue
                    seen.add(abs_matched)
                    result.append(abs_matched)
                continue

            # 非 glob：可能是文件或目录
            abs_path = str(expanded.resolve()) if expanded.exists() else str(expanded)
            if abs_path in seen:
                continue
            seen.add(abs_path)

            path_obj = Path(abs_path)
            if not path_obj.exists():
                # 路径不存在：跳过。
                continue

            if path_obj.is_file():
                result.append(abs_path)
            elif path_obj.is_dir():
                result.extend(
                    self._collect_files_from_directory(abs_path, resource_type, seen)
                )

        return result

    def _collect_files_from_directory(
        self,
        directory: str,
        resource_type: ResourceType,
        seen: Set[str],
    ) -> List[str]:
        """递归扫描目录，收集该资源类型的文件。"""
        results: List[str] = []
        for path in RESOURCE_DISCOVERY[resource_type](directory):
            abs_path = str(Path(path).resolve())
            if abs_path not in seen:
                seen.add(abs_path)
                results.append(abs_path)
        return results

    def _add_resource(
        self,
        accumulator: Dict[ResourceType, Dict[str, ResolvedResource]],
        resource_type: ResourceType,
        new_resource: ResolvedResource,
    ) -> None:
        """按资源优先级把新资源加入 accumulator。

        高优先级（precedence rank 更小）覆盖低优先级；同优先级 first-wins。
        key 使用规范路径（canonical），避免同一文件因 symlink、相对/绝对路径
        不同表示而出现重复。
        """
        target = accumulator[resource_type]
        canonical_key = str(Path(new_resource.path).resolve())
        existing = target.get(canonical_key)
        if existing is None:
            target[canonical_key] = new_resource
            return
        if resource_precedence_rank(new_resource.metadata) < resource_precedence_rank(
            existing.metadata
        ):
            target[canonical_key] = new_resource


__all__ = [
    "PackageResolver",
    "resource_precedence_rank",
    "sort_resolved_resources",
    "build_path_metadata",
]
