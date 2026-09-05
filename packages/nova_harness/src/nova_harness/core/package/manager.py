"""PackageManager — 统一安装与资源解析入口。

本模块将安装能力（``PackageInstaller``）与运行时资源解析能力
（``PackageResolver``）聚合到一个 facade 中。调用方（如
``AgentSessionServices``、``ResourceLoader``）只需持有 ``PackageManager``
即可获得完整的包生命周期管理与资源发现能力。

Typical usage::

    >>> from nova_harness.core.package import PackageManager
    >>> pm = PackageManager(agent_dir="~/.nova/agent")
    >>>
    >>> # Install a bundle globally and persist it to settings
    >>> pm.install_and_persist("/path/to/nova_coding_agent")
    >>>
    >>> # Install a bundle into the current project
    >>> pm.install_and_persist("/path/to/nova_coding_agent", local=True)
    >>>
    >>> # Resolve all runtime resources across both scopes
    >>> paths = await pm.resolve_resources()
    >>>
    >>> # List installed packages across scopes (project wins on duplicates)
    >>> for pkg in pm.list():
    ...     print(f"{pkg.name} @ {pkg.version}")
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Set, Tuple, Union

from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.package.install.installer import PackageInstaller
from nova_harness.core.package.install.python_backend import uninstall_package
from nova_harness.core.package.install.store import (
    basename,
    install_path_for_source,
    metadata_dedup_key,
    read_dist_info,
    sanitize_name,
)
from nova_harness.core.package.install.updates import check_for_available_updates
from nova_harness.core.package.manifest import read_manifest
from nova_harness.core.package.resolve.resolver import PackageResolver
from nova_harness.core.package.source.spec import (
    PackageSourceCollection,
    ResolvedScopedSources,
    get_package_identity,
    get_package_source_string,
    normalize_package_source_for_settings,
    parse_package_source_spec,
    parse_source,
)
from nova_harness.core.package.utils import is_offline_mode_enabled
from nova_harness.core.types.config.settings import PackageSourceSpec
from nova_harness.core.types.package import (
    AmbiguousPackageNameError,
    ConfiguredPackage,
    MissingSourceAction,
    PackageMetadata,
    PackageUpdate,
    PackageView,
    ResolvedPaths,
    SourceScope,
    UninstallResult,
)
from nova_harness.core.types.package.errors import (
    PackageInstallError,
    PackageUpdateError,
)

__all__ = ["PackageManager", "PackageInstallError", "PackageUpdateError"]

logger = logging.getLogger(__name__)

# 官方基础包（nova-base：会话基础设施——slash 命令 + question/todo 工具 +
# UI 原语糖库）：卸载即失去基本功能，任何安装形态（内建/用户安装）下都不可卸载
_PROTECTED_PACKAGES = frozenset({"nova-base", "nova_base"})


def _is_protected_package(name_or_source: str) -> bool:
    """按包名或任意源形态（path/npm/git）判定是否受保护的基础包。"""
    try:
        source = parse_source(name_or_source.strip())
    except ValueError:
        return False
    if source.type == "npm":
        return (source.npm_name or "") in _PROTECTED_PACKAGES
    if source.type == "git":
        return (source.repo_path or "").rsplit("/", 1)[-1] in _PROTECTED_PACKAGES
    # path 族（含裸包名——parse_source 缺省按 path 解析）
    tail = (source.path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return tail in _PROTECTED_PACKAGES


class PackageManager:
    """Nova 包管理器 facade。

    内部聚合 user/project 两个 ``PackageInstaller``、一个 ``PackageResolver``，
    对外统一暴露安装/卸载/更新/列表/验证以及资源解析能力。

    所有写操作（install/uninstall/update）默认作用于全局 user scope；传入
    ``local=True`` 可切换到当前项目的 project scope。
    """

    def __init__(
        self,
        agent_dir: Optional[Union[str, Path]] = None,
        cwd: Optional[Union[str, Path]] = None,
        settings_manager: Optional[SettingsManager] = None,
        project_trusted: Optional[bool] = None,
        install_missing_packages: bool = True,
        on_progress=None,
    ) -> None:
        self.cwd = Path(cwd or os.getcwd()).resolve()
        self.agent_dir = Path(agent_dir).resolve() if agent_dir else get_agent_dir()
        self._on_progress = on_progress

        # project_trusted 只控制**读取侧**门控（resolver 是否解析 project
        # scope、settings 是否加载 project 配置）；包管理的写操作不做信任
        # 检查——装/卸包是用户的主动行为，trust 决策归运行时（会话启动）。
        # SDK/CLI 场景默认视为信任，运行时由 AgentSessionServices 传入决议结果。
        effective_trusted = True if project_trusted is None else project_trusted

        self.settings_manager = settings_manager or SettingsManager.create(
            cwd=str(self.cwd),
            agent_dir=str(self.agent_dir),
            project_trusted=effective_trusted,
        )
        # 如果外部传入了 settings_manager，同步 project_trusted 状态，避免
        # PackageManager 与 settings_manager 对 project 设置的加载不一致。
        if settings_manager is not None and project_trusted is not None:
            self.settings_manager.set_project_trusted(effective_trusted)

        self._user_installer = PackageInstaller(
            agent_dir=str(self.agent_dir),
            local=False,
            settings_manager=self.settings_manager,
            cwd=str(self.cwd),
            on_progress=self._on_progress,
            requires_checker=self._requires_missing,
        )
        self._project_installer = PackageInstaller(
            agent_dir=str(self.agent_dir),
            local=True,
            settings_manager=self.settings_manager,
            cwd=str(self.cwd),
            on_progress=self._on_progress,
            requires_checker=self._requires_missing,
        )
        self._resolver = PackageResolver(
            cwd=str(self.cwd),
            agent_dir=str(self.agent_dir),
            settings_manager=self.settings_manager,
            project_trusted=effective_trusted,
            on_progress=self._on_progress,
        )
        self._install_missing_packages = install_missing_packages

        self._source_collection = PackageSourceCollection(
            user_base_dir=str(self._user_installer.install_dir),
            project_base_dir=str(self._project_installer.install_dir),
        )

    def set_progress_callback(self, on_progress) -> None:
        """设置安装/更新进度回调。

        回调会收到 ``ProgressEvent``，包含 ``type``、``action``、``source``、
        ``message`` 与可选 ``percent``。
        """
        self._on_progress = on_progress
        self._user_installer.set_progress_callback(on_progress)
        self._project_installer.set_progress_callback(on_progress)
        self._resolver.set_progress_callback(on_progress)

    def _installer(self, local: bool = False) -> PackageInstaller:
        """Return the installer for the requested scope."""
        return self._project_installer if local else self._user_installer

    @property
    def path_root(self) -> Path:
        """Path root for installed path-source packages (user scope)."""
        return self._user_installer.path_root

    @property
    def git_root(self) -> Path:
        """Git cache root for installed git-source packages (user scope)."""
        return self._user_installer.git_root

    @property
    def source_resolver(self):
        """Source resolver used by the user-scope installer."""
        return self._user_installer.source_resolver

    # ------------------------------------------------------------------
    # Install / uninstall / update / list / validate (scope-aware)
    # ------------------------------------------------------------------
    def install(
        self,
        source: str,
        *,
        local: bool = False,
        no_deps: bool = False,
        dry_run: bool = False,
        quiet: bool = False,
        editable: bool = False,
    ) -> PackageMetadata:
        """Install a package from *source* into the requested scope."""
        return self._installer(local).install(
            source,
            no_deps=no_deps,
            dry_run=dry_run,
            quiet=quiet,
            editable=editable,
        )

    def install_and_persist(
        self,
        source: str,
        *,
        local: bool = False,
        no_deps: bool = False,
        dry_run: bool = False,
        quiet: bool = False,
        editable: bool = False,
    ) -> PackageMetadata:
        """Install a package and persist its source to settings in the requested scope."""
        return self._installer(local).install_and_persist(
            source,
            no_deps=no_deps,
            dry_run=dry_run,
            quiet=quiet,
            editable=editable,
        )

    def _requires_missing(self, requires: List[str]) -> List[str]:
        """包间依赖校验：返回 requires 中未安装的名字（user/project 合并视图）。

        惰性计算（安装流程调用时才枚举已安装包）；构造期注入两个 installer
        共享同一回调——scope 归属不影响"已安装"的判定。
        """
        installed = {pkg.name for pkg in self.list() if pkg.name}
        return [name for name in requires if name not in installed]

    def _guard_requires_on_uninstall(self, name_or_source: str) -> None:
        """卸载守护：被其他已安装包 requires 引用的包拒绝卸载。

        读取合并视图中每个包的副本 manifest（requires 与 name/version 同源——
        内容事实在副本上，不进 dist-info）；命中即 ValueError 并列出依赖方。
        """
        from nova_harness.core.package.manifest import read_manifest

        all_pkgs = self.list()
        # 目标包的 manifest 名集合（按 name 或 source identity 命中）
        target_names: Set[str] = set()
        for pkg in all_pkgs:
            if pkg.name == name_or_source or pkg.source == name_or_source:
                if pkg.name:
                    target_names.add(pkg.name)
        if not target_names:
            return  # 未命中已安装包——按原"找不到"路径处理

        dependents: List[str] = []
        for pkg in all_pkgs:
            if pkg.name in target_names:
                continue
            try:
                manifest = read_manifest(pkg.install_path)
            except Exception:
                continue
            requires = (manifest.nova.requires if manifest.nova else None) or []
            if any(name in requires for name in target_names):
                dependents.append(pkg.name or pkg.source)
        if dependents:
            raise ValueError(
                f"Cannot uninstall '{name_or_source}': required by "
                f"{', '.join(sorted(dependents))}. Uninstall them first."
            )

    def uninstall(self, name_or_source: str, *, local: bool = False) -> UninstallResult:
        """Remove an installed package by name or source spec.

        默认同时搜索全局（user）和项目（project）两个 scope 并卸载匹配的包；
        ``local=True`` 只卸载项目 scope。

        为避免跨 scope 共享的 Python 包被误删，每个 scope 的 bundle 卸载时先跳过
        Python 包卸载，最后再统计两个 scope 的引用数，仅当引用数为 0 时才卸载
        底层 Python 包。
        """
        # 官方基础包守护：nova-base 是会话基础设施（slash 命令/question/todo/
        # UI 原语）——卸载即失去基本功能，任何安装形态下都不可卸载
        if _is_protected_package(name_or_source):
            raise ValueError(
                f"'{name_or_source}' 是官方基础包（nova-base 提供会话基础设施："
                "21 个 slash 命令、question/todo 工具、UI 原语糖库），"
                "卸载将失去基本功能，不可卸载。"
            )
        # 包间依赖守护：被其他已安装包 requires 引用时拒绝卸载
        self._guard_requires_on_uninstall(name_or_source)

        if local:
            removed = self._project_installer.uninstall(name_or_source)
            return UninstallResult(removed=removed)

        removed_package_names: Set[str] = set()
        any_ok = False
        messages: List[str] = []

        for scope_local, scope_name in [(True, "project"), (False, "user")]:
            installer = self._installer(scope_local)
            try:
                meta = installer.info(name_or_source)
                if installer.uninstall(name_or_source, uninstall_python_package=False):
                    any_ok = True
                    messages.append(f"Removed from {scope_name} scope.")
                    if meta and meta.package_name:
                        removed_package_names.add(meta.package_name)
            except AmbiguousPackageNameError as exc:
                logger.debug(
                    "Ambiguous package name in %s scope; skipping: %s",
                    scope_name,
                    exc,
                )
                messages.append(f"Skipped {scope_name} scope: {exc}")
            except ValueError as exc:
                logger.debug("Package not found in %s scope: %s", scope_name, exc)

        for package_name in removed_package_names:
            remaining = self._user_installer.count_package_name_references(
                package_name
            ) + self._project_installer.count_package_name_references(package_name)
            if remaining == 0:
                uninstall_package(package_name)
                messages.append(f"Uninstalled Python package '{package_name}'.")

        return UninstallResult(removed=any_ok, messages=messages)

    def add_source_to_settings(
        self, source: PackageSourceSpec, *, local: bool = False
    ) -> bool:
        """只把包源写入 settings，不执行安装。

        如果已存在相同 identity 的源，会合并 filters / editable 等字段并更新
        记录；返回 ``True`` 表示 settings 发生了写入（新增或替换），
        ``False`` 表示完全相同无需改动。
        """
        installer = self._installer(local)
        base_dir = str(installer.install_dir)

        normalized = normalize_package_source_for_settings(
            source, base_dir, cwd=str(self.cwd)
        )
        identity = get_package_identity(normalized, base_dir)
        existing_sources = self.settings_manager.get_package_sources(
            local=local, base_dir=base_dir
        )
        norm_source, norm_editable, norm_filters = parse_package_source_spec(normalized)
        for existing in existing_sources:
            if get_package_identity(existing, base_dir) != identity:
                continue
            existing_normalized = normalize_package_source_for_settings(
                existing, base_dir, cwd=str(self.cwd)
            )
            exist_source, exist_editable, exist_filters = parse_package_source_spec(
                existing_normalized
            )
            if (
                exist_source == norm_source
                and exist_editable == norm_editable
                and exist_filters == norm_filters
            ):
                return False
            break

        self.settings_manager.add_package_source(
            source,
            local=local,
            base_dir=base_dir,
            cwd=str(self.cwd),
        )
        return True

    def remove_source_from_settings(
        self, source: PackageSourceSpec, *, local: bool = False
    ) -> bool:
        """只从 settings 中移除包源，不执行卸载。"""
        installer = self._installer(local)
        return self.settings_manager.remove_package_source(
            source,
            local=local,
            base_dir=str(installer.install_dir),
            cwd=str(self.cwd),
        )

    async def update(
        self,
        name_or_source: PackageSourceSpec,
        *,
        local: bool = False,
    ) -> List[PackageMetadata]:
        """Re-install or switch a package from its recorded or given source.

        默认先搜索全局和项目两个 scope，只在实际安装了该包的 scope 中执行更新。
        传入 ``local=True`` 则只更新项目 scope。

        各 scope 的更新**串行**执行：所有 scope 共享同一个 Python 环境，
        并发跑多个 pip/uv 进程写同一环境是不安全的。

        Returns:
            更新成功的 ``PackageMetadata`` 列表。若某个 scope 失败，会抛出
            ``PackageUpdateError`` 并携带成功与失败详情，不会静默吞掉错误。
        """
        if local:
            return [
                await asyncio.to_thread(self._project_installer.update, name_or_source)
            ]

        targets = self._find_update_targets(name_or_source)
        if not targets:
            raise ValueError(
                f"Package '{name_or_source}' is not installed in any scope."
            )

        successful: List[Tuple[str, PackageMetadata]] = []
        failures: List[Tuple[str, Exception]] = []
        for scope_local, scope_name in targets:
            try:
                meta = await asyncio.to_thread(
                    self._installer(scope_local).update, name_or_source
                )
                successful.append((scope_name, meta))
            except ValueError as exc:
                if isinstance(exc, AmbiguousPackageNameError):
                    raise
                logger.debug("Package not found in %s scope: %s", scope_name, exc)
            except Exception as exc:
                logger.warning(
                    "Failed to update package in %s scope: %s", scope_name, exc
                )
                failures.append((scope_name, exc))

        if failures:
            raise PackageUpdateError(successful, failures)

        return [meta for _, meta in successful]

    async def update_all(self) -> List[PackageMetadata]:
        """更新 settings 中配置的所有包（path 源除外）。

        path 源是本地目录，没有"从远端更新"的概念（对齐 TS：全量更新跳过
        local 源）；git 源（含 pinned ref，用于 reconcile 缓存与配置 ref）
        逐个按现有 ``update()`` 逻辑拉取/重装。单包失败不中断其他包，
        最后汇总抛出 ``PackageUpdateError``。
        """
        configured = self.list_configured_packages()
        git_sources: List[str] = []
        for pkg in configured:
            if parse_source(pkg.source).type == "git":
                git_sources.append(pkg.source)
        if not git_sources:
            return []

        successful: List[Tuple[str, PackageMetadata]] = []
        failures: List[Tuple[str, Exception]] = []
        for source in git_sources:
            try:
                metas = await self.update(source)
                successful.extend((source, meta) for meta in metas)
            except PackageUpdateError as exc:
                successful.extend(exc.successful)
                failures.extend(exc.failures)
            except Exception as exc:
                logger.warning("Failed to update package %s: %s", source, exc)
                failures.append((source, exc))

        if failures:
            raise PackageUpdateError(successful, failures)
        return [meta for _, meta in successful]

    def _find_update_targets(
        self, name_or_source: PackageSourceSpec
    ) -> List[Tuple[bool, str]]:
        """返回实际安装了 *name_or_source* 的 scope 列表。

        对 source spec 按 package identity 匹配；对名称按 display name 匹配。
        若某个 scope 中名称模糊（多个同名包），直接向上抛出 ``AmbiguousPackageNameError``，
        避免静默跳过。
        """
        lookup_key = (
            get_package_source_string(name_or_source)
            if isinstance(name_or_source, dict)
            else name_or_source
        )
        targets: List[Tuple[bool, str]] = []
        for scope_local, scope_name in [(True, "project"), (False, "user")]:
            installer = self._installer(scope_local)
            try:
                meta = installer.info(lookup_key)
            except AmbiguousPackageNameError:
                raise
            except Exception:
                meta = None
            if meta is not None:
                targets.append((scope_local, scope_name))
        return targets

    async def check_for_available_updates(self) -> List[PackageUpdate]:
        """Return packages that have updates available.

        扫描 settings 中已配置的包，跳过 path 源与固定 commit 的 git 源，检查
        git 远程是否有新提交。离线模式下返回空列表。
        """
        if is_offline_mode_enabled():
            return []

        project_specs = self.settings_manager.get_package_sources(
            local=True, base_dir=str(self._project_installer.install_dir)
        )
        user_specs = self.settings_manager.get_package_sources(
            local=False, base_dir=str(self._user_installer.install_dir)
        )

        scoped = self._source_collection.resolve(user_specs, project_specs)

        scoped_sources: List[Tuple[SourceScope, PackageSourceSpec, Path, Path]] = []
        for spec in scoped.project:
            scoped_sources.append(
                (
                    SourceScope.PROJECT,
                    spec,
                    self._project_installer.path_root,
                    self._project_installer.git_root,
                )
            )
        for spec in scoped.user:
            scoped_sources.append(
                (
                    SourceScope.USER,
                    spec,
                    self._user_installer.path_root,
                    self._user_installer.git_root,
                )
            )

        return await check_for_available_updates(scoped_sources)

    def list(self, *, local: bool = False) -> List[PackageMetadata]:
        """Return installed packages.

        默认合并全局和项目两个 scope，project 优先去重；``local=True`` 只返回项目 scope。
        """
        if local:
            return self._project_installer.list()

        project_pkgs = self._project_installer.list()
        user_pkgs = self._user_installer.list()
        seen: Set[str] = set()
        result: List[PackageMetadata] = []
        for pkg in project_pkgs:
            identity = metadata_dedup_key(
                pkg, base_dir=str(self._project_installer.install_dir)
            )
            if identity in seen:
                continue
            seen.add(identity)
            result.append(pkg)
        for pkg in user_pkgs:
            identity = metadata_dedup_key(
                pkg, base_dir=str(self._user_installer.install_dir)
            )
            if identity in seen:
                continue
            seen.add(identity)
            result.append(pkg)
        return result

    def list_with_resources(self, *, local: bool = False) -> Dict[str, PackageView]:
        """Return a view of packages with their resources.

        默认合并全局和项目两个 scope，project 优先去重；``local=True`` 只返回项目 scope。
        项目不被信任时 project 级整组剔除（与 resolver 读取门控、前端
        partitionByTrust 同语义）——否则 project 副本按身份去重挤掉 user
        副本后又被信任门过滤，出现"装了却一个都不加载"的窗口。
        """
        if local:
            return self._project_installer.list_with_resources()

        user_views = self._user_installer.list_with_resources()
        if not self.settings_manager.is_project_trusted():
            return user_views
        project_views = self._project_installer.list_with_resources()
        result: Dict[str, PackageView] = dict(project_views)
        for identity, view in user_views.items():
            if identity not in result:
                result[identity] = view
        return result

    def list_configured_packages(
        self, *, local: bool = False
    ) -> List[ConfiguredPackage]:
        """Return all package sources configured in settings.

        默认合并全局和项目两个 scope；``local=True`` 只返回项目 scope。
        结果包含每个配置源的安装路径（``installed_path``），若包尚未安装则为
        ``None``。
        """
        result: List[ConfiguredPackage] = []
        seen: Set[str] = set()

        scopes: List[Tuple[bool, SourceScope]] = [
            (True, SourceScope.PROJECT),
            (False, SourceScope.USER),
        ]
        if local:
            scopes = [(True, SourceScope.PROJECT)]

        for scope_local, scope_enum in scopes:
            base_dir = str(self._installer(scope_local).install_dir)
            for spec in self.settings_manager.get_package_sources(
                local=scope_local, base_dir=base_dir
            ):
                source_str = get_package_source_string(spec)
                identity = get_package_identity(source_str, base_dir)
                if identity in seen:
                    continue
                seen.add(identity)

                filtered = isinstance(spec, dict)
                installed_path = self.get_installed_path(source_str, local=scope_local)
                result.append(
                    ConfiguredPackage(
                        source=source_str,
                        scope=scope_enum,
                        filtered=filtered,
                        installed_path=installed_path,
                    )
                )

        return result

    def info(
        self, name_or_source: str, *, local: bool = False
    ) -> Optional[PackageMetadata]:
        """Get metadata for a single installed package.

        默认优先搜索项目 scope，再搜索全局 scope；``local=True`` 只搜索项目 scope。
        """
        if local:
            return self._project_installer.info(name_or_source)

        meta = self._project_installer.info(name_or_source)
        if meta is not None:
            return meta
        return self._user_installer.info(name_or_source)

    def get_installed_path(self, source: str, *, local: bool = False) -> Optional[str]:
        """Return the Nova-managed install path for *source* if it exists.

        对 git 源直接计算缓存目录；对 path 源先尝试查询已持久化的 metadata。
        若包尚未安装或路径不存在，返回 ``None``。
        """
        installer = self._installer(local)
        try:
            source_obj = parse_source(source)
            if source_obj.type == "git":
                install_path = install_path_for_source(
                    source_obj, "", installer.path_root, installer.git_root
                )
                return str(install_path) if install_path.exists() else None
            if source_obj.type == "path":
                meta = installer.info(source)
                if meta is None:
                    return None
                return meta.install_path if Path(meta.install_path).exists() else None
        except Exception:
            pass
        return None

    def validate(self, source: str, *, local: bool = False) -> List[str]:
        """Validate a package directory and return a list of issues in the requested scope."""
        return self._installer(local).validate(source)

    # ------------------------------------------------------------------
    # Resource resolution
    # ------------------------------------------------------------------
    async def resolve_resources(
        self,
        *,
        install_missing_packages: Optional[bool] = None,
        on_missing=None,
    ) -> ResolvedPaths:
        """Resolve all runtime resource paths according to settings and discovery."""
        should_install = (
            install_missing_packages
            if install_missing_packages is not None
            else self._install_missing_packages
        )

        scoped_sources = self._settings_package_sources_by_scope()

        if should_install or on_missing is not None:
            await self._ensure_packages_installed(
                scoped_sources.user, SourceScope.USER, on_missing=on_missing
            )
            await self._ensure_packages_installed(
                scoped_sources.project, SourceScope.PROJECT, on_missing=on_missing
            )

        # settings 是唯一选择层：只有写入 settings 的包才会被解析加载；
        # 仅物化到 Nova 目录（install 不 persist）的包对运行时不可见。
        return await self._resolver.resolve(scoped_packages=scoped_sources)

    def _settings_package_sources_by_scope(self) -> ResolvedScopedSources:
        """Collect resolved package source specs from settings, split by scope.

        Project scope wins over user scope for the same package identity.
        The returned specs preserve ``editable`` flags and filters so that
        auto-install can reinstall them correctly.
        """
        user_raw = self.settings_manager.get_package_sources(
            local=False, base_dir=str(self._user_installer.install_dir)
        )
        project_raw = (
            self.settings_manager.get_package_sources(
                local=True, base_dir=str(self._project_installer.install_dir)
            )
            if self.settings_manager.is_project_trusted()
            else []
        )
        return self._source_collection.resolve(user_raw, project_raw)

    async def _ensure_packages_installed(
        self,
        sources: List[PackageSourceSpec],
        scope: SourceScope,
        on_missing: Optional[Callable[[str], Awaitable[MissingSourceAction]]] = None,
    ) -> None:
        """Install any package source in *sources* that cannot currently be resolved.

        离线模式下跳过自动安装。多个包按顺序逐个安装，任一失败立即停止，避免
        后续包依赖未完成的安装或留下不一致状态。
        """
        if is_offline_mode_enabled():
            return

        failures: List[Tuple[str, Exception]] = []
        for spec in sources:
            source_str, editable, _ = parse_package_source_spec(spec)
            if self._is_package_resolvable(source_str, scope):
                continue

            try:
                if on_missing is not None:
                    action = await on_missing(source_str)
                    if action == "skip":
                        continue
                    if action == "error":
                        raise ValueError(f"Missing source: {source_str}")
                    # action == "install" falls through
                await asyncio.to_thread(
                    self._installer_for_scope(scope).install,
                    source_str,
                    editable=editable,
                    quiet=True,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to install missing package %s: %s", source_str, exc
                )
                failures.append((source_str, exc))
                break

        if failures:
            raise PackageInstallError(failures)

    def _is_package_resolvable(self, source: str, scope: SourceScope) -> bool:
        """Return True if *source* can be resolved to a local directory.

        “可解析”= 安装流程已完成：安装副本存在 **且** dist-info 快照存在
        （dist-info 在安装末尾写入，是安装完成标志）。只 clone 未走完安装
        （如 validate）、安装中途失败留下的幽灵副本、以及 dist-info 时代
        之前的旧安装，都会触发一次自愈重装——复制、依赖与元数据在重装中
        补齐。

        例外：path 源的原源已不可用时无法重装，副本存在即视为可解析
        （原样使用副本，避免 dangling settings 条目杀死会话启动）。
        """
        installer = self._installer_for_scope(scope)
        source_obj = parse_source(source)

        if source_obj.type == "path":
            try:
                resolved_dir = installer.source_resolver.resolve(source_obj)
            except Exception:
                # 原源不可用（可能已删除）：无法重装，按 basename 猜测安装
                # 目录，副本存在即视为可解析。
                pkg_name = sanitize_name(
                    os.path.basename(os.path.normpath(source_obj.path or ""))
                )
                install_path = install_path_for_source(
                    source_obj, pkg_name, installer.path_root, installer.git_root
                )
                return install_path.exists()
            pkg_name = read_manifest(resolved_dir).name or basename(resolved_dir)
        elif source_obj.type == "git":
            # git 源的安装路径由 host/repo 直接确定，缓存可随时重装
            # （clone/fetch），统一要求安装完成标志，不触发网络。
            pkg_name = ""
        else:
            return False

        install_path = install_path_for_source(
            source_obj, pkg_name, installer.path_root, installer.git_root
        )
        return install_path.exists() and read_dist_info(str(install_path)) is not None

    def _installer_for_scope(self, scope: SourceScope) -> PackageInstaller:
        """Return the installer responsible for *scope*."""
        if scope == SourceScope.PROJECT:
            return self._project_installer
        return self._user_installer
