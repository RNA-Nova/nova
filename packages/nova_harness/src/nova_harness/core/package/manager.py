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
from nova_harness.core.harness.project_trust.trust_store import ProjectTrustStore
from nova_harness.core.package.backend import uninstall_package
from nova_harness.core.package.installer import PackageInstaller
from nova_harness.core.package.resolver import PackageResolver
from nova_harness.core.package.source import (
    PackageSourceCollection,
    ResolvedScopedSources,
    _source_str,
    get_package_identity,
    normalize_package_source_for_settings,
    parse_package_source_spec,
    parse_source,
)
from nova_harness.core.package.store import (
    _find_installed_metadata_by_source,
    _install_path_for_source,
)
from nova_harness.core.package.updates import check_for_available_updates
from nova_harness.core.package.utils.offline import is_offline_mode_enabled
from nova_harness.core.types.config.settings import PackageSourceSpec
from nova_harness.core.types.package_manager import (
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
from nova_harness.core.types.project_trust import ProjectNotTrustedError


class PackageInstallError(RuntimeError):
    """批量自动安装时部分包失败。"""

    def __init__(self, failures: List[Tuple[str, BaseException]]) -> None:
        self.failures = failures
        messages = "\n".join(f"  - {source}: {exc}" for source, exc in failures)
        super().__init__(f"Failed to install {len(failures)} package(s):\n{messages}")


class PackageUpdateError(RuntimeError):
    """跨 scope 更新时部分 scope 失败。"""

    def __init__(self, successful, failures):
        self.successful = successful
        self.failures = failures
        super().__init__(
            "Update succeeded in {} but failed in {}.".format(
                ", ".join(scope for scope, _ in successful),
                ", ".join(scope for scope, _ in failures),
            )
        )


__all__ = ["PackageManager", "PackageInstallError", "PackageUpdateError"]

logger = logging.getLogger(__name__)


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

        # 默认信任项目，与 TS 行为一致；显式传入 False 可保持不信任。
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
        )
        self._project_installer = PackageInstaller(
            agent_dir=str(self.agent_dir),
            local=True,
            settings_manager=self.settings_manager,
            cwd=str(self.cwd),
            on_progress=self._on_progress,
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
        # 同步更新 resolver 及其内部所有 scope 的 SourceResolver，
        # 否则 resolve_extension_sources / git update 等进度事件会丢失。
        self._resolver._on_progress = on_progress
        for source_resolver in self._resolver._source_resolvers.values():
            source_resolver._on_progress = on_progress

    def trust_project(self, trusted: bool = True) -> None:
        """Persist the project trust decision for the current working directory."""
        try:
            trust_store = ProjectTrustStore.for_agent_dir(str(self.agent_dir))
            trust_store.set(str(self.cwd), trusted)
        except Exception as exc:
            raise RuntimeError(f"Failed to update project trust: {exc}") from exc
        self.settings_manager.set_project_trusted(trusted)

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

    def uninstall(self, name_or_source: str, *, local: bool = False) -> UninstallResult:
        """Remove an installed package by name or source spec.

        默认同时搜索全局（user）和项目（project）两个 scope 并卸载匹配的包；
        ``local=True`` 只卸载项目 scope。

        为避免跨 scope 共享的 Python 包被误删，每个 scope 的 bundle 卸载时先跳过
        Python 包卸载，最后再统计两个 scope 的引用数，仅当引用数为 0 时才卸载
        底层 Python 包。
        """
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
                    if meta and meta.package_name:
                        removed_package_names.add(meta.package_name)
            except ProjectNotTrustedError:
                message = f"{scope_name} scope skipped: project not trusted"
                logger.warning(message)
                messages.append(message)
            except AmbiguousPackageNameError as exc:
                logger.debug(
                    "Ambiguous package name in %s scope; skipping: %s",
                    scope_name,
                    exc,
                )
                continue
            except ValueError as exc:
                logger.debug("Package not found in %s scope: %s", scope_name, exc)

        for package_name in removed_package_names:
            remaining = self._user_installer.count_package_name_references(
                package_name
            ) + self._project_installer.count_package_name_references(package_name)
            if remaining == 0:
                uninstall_package(package_name)

        return UninstallResult(removed=any_ok, messages=messages)

    def add_source_to_settings(
        self, source: PackageSourceSpec, *, local: bool = False
    ) -> bool:
        """只把包源写入 settings，不执行安装。

        如果已存在相同 identity 的源，会合并 filters / editable 等字段并更新
        记录；返回 ``True`` 表示 settings 发生了写入（新增或替换），
        ``False`` 表示完全相同无需改动。

        对 project scope 的写入同样受 project trust 门控约束。
        """
        installer = self._installer(local)
        if local and not self.settings_manager.is_project_trusted():
            raise ProjectNotTrustedError(str(self.cwd))

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
        """只从 settings 中移除包源，不执行卸载。

        对 project scope 的写入同样受 project trust 门控约束。
        """
        installer = self._installer(local)
        if local and not self.settings_manager.is_project_trusted():
            raise ProjectNotTrustedError(str(self.cwd))

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

        当同一包在多个 scope 中存在时，各 scope 的更新在独立线程中并发执行
        （默认最多 4 个）。为避免同一 scope 内 pip/uv 安装冲突，同一 scope 内
        的多个包仍按顺序处理。

        Returns:
            更新成功的 ``PackageMetadata`` 列表。若某个 scope 失败，会抛出
            ``PackageUpdateError`` 并携带成功与失败详情，不会静默吞掉错误。
        """
        if local:
            return [self._project_installer.update(name_or_source)]

        targets = self._find_update_targets(name_or_source)
        if not targets:
            raise ValueError(
                f"Package '{name_or_source}' is not installed in any scope."
            )

        semaphore = asyncio.Semaphore(4)

        async def _update_scope(
            scope_local: bool, scope_name: str
        ) -> Tuple[str, Optional[PackageMetadata], Optional[Exception]]:
            try:
                async with semaphore:
                    meta = await asyncio.to_thread(
                        self._installer(scope_local).update, name_or_source
                    )
                return scope_name, meta, None
            except ProjectNotTrustedError:
                logger.debug(
                    "Project not trusted for %s scope update; skipping", scope_name
                )
                return scope_name, None, None
            except ValueError as exc:
                if isinstance(exc, AmbiguousPackageNameError):
                    raise
                logger.debug("Package not found in %s scope: %s", scope_name, exc)
                return scope_name, None, None
            except Exception as exc:
                logger.warning(
                    "Failed to update package in %s scope: %s", scope_name, exc
                )
                return scope_name, None, exc

        results = await asyncio.gather(
            *[_update_scope(local, name) for local, name in targets]
        )

        successful: List[Tuple[str, PackageMetadata]] = []
        failures: List[Tuple[str, Exception]] = []
        for scope_name, meta, exc in results:
            if meta is not None:
                successful.append((scope_name, meta))
            elif exc is not None:
                failures.append((scope_name, exc))

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
            _source_str(name_or_source)
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
            identity = get_package_identity(
                pkg.source, base_dir=str(self._project_installer.install_dir)
            )
            if identity in seen:
                continue
            seen.add(identity)
            result.append(pkg)
        for pkg in user_pkgs:
            identity = get_package_identity(
                pkg.source, base_dir=str(self._user_installer.install_dir)
            )
            if identity in seen:
                continue
            seen.add(identity)
            result.append(pkg)
        return result

    def list_with_resources(self, *, local: bool = False) -> Dict[str, PackageView]:
        """Return a view of packages with their resources.

        默认合并全局和项目两个 scope，project 优先去重；``local=True`` 只返回项目 scope。
        """
        if local:
            return self._project_installer.list_with_resources()

        project_views = self._project_installer.list_with_resources()
        user_views = self._user_installer.list_with_resources()
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
                source_str = _source_str(spec)
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
                install_path = _install_path_for_source(
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

        fallback_packages = {
            SourceScope.USER: self._user_installer.list_installed_fallback_sources(),
            SourceScope.PROJECT: (
                self._project_installer.list_installed_fallback_sources()
                if self.settings_manager.is_project_trusted()
                else []
            ),
        }
        return await self._resolver.resolve(
            fallback_packages=fallback_packages,
            scoped_packages=scoped_sources,
        )

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
            except ProjectNotTrustedError:
                raise
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

        A package is considered resolvable if either its original source is
        available, or a Nova-managed install copy exists with matching metadata.
        """
        installer = self._installer_for_scope(scope)
        source_obj = parse_source(source)
        if source_obj.type == "path":
            path_root = installer.path_root
            if (
                _find_installed_metadata_by_source(
                    path_root, source, base_dir=str(installer.install_dir)
                )
                is not None
            ):
                return True
        if source_obj.type == "git":
            # 避免在检查阶段触发网络请求：只要本地 git 缓存存在即可。
            # 显式 ref 的切换由 update() 处理，resolve 阶段不应因本地缺少分支
            # 而反复触发 reinstall（TS 同样只检查目录是否存在）。
            install_path = _install_path_for_source(
                source_obj, "", installer.path_root, installer.git_root
            )
            return (install_path / ".git").exists()

        try:
            installer.source_resolver.resolve(source_obj)
            return True
        except Exception:
            return False

    def _installer_for_scope(self, scope: SourceScope) -> PackageInstaller:
        """Return the installer responsible for *scope*."""
        if scope == SourceScope.PROJECT:
            return self._project_installer
        return self._user_installer

    def resolve_extension_sources(
        self,
        sources: List[str],
        *,
        temporary: bool = False,
        local: bool = False,
    ) -> ResolvedPaths:
        """Resolve a list of temporary/CLI extension sources."""
        return self._resolver.resolve_extension_sources(
            sources, temporary=temporary, local=local
        )
