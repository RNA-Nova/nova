"""PackageInstaller — install / update / uninstall packages within one scope.

Typical usage::

    >>> from nova_harness.core.package import PackageInstaller
    >>> installer = PackageInstaller()
    >>>
    >>> # Install a bundle (not persisted)
    >>> meta = installer.install("/path/to/nova_coding_agent")
    >>>
    >>> # Install and persist to settings
    >>> meta = installer.install_and_persist("/path/to/nova_coding_agent")
    >>>
    >>> # Uninstall by name or source spec
    >>> installer.uninstall("nova-coding-agent")
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Union

from nova_harness.core.config.defaults import (
    AGENTS_DIR_NAME,
    EXTENSIONS_DIR_NAME,
    GIT_PACKAGES_DIR_NAME,
    PACKAGES_DIR_NAME,
    PATH_PACKAGES_DIR_NAME,
    PROMPTS_DIR_NAME,
    SKILLS_DIR_NAME,
    THEMES_DIR_NAME,
    get_agent_dir,
    get_project_base_dir,
)
from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.config.telemetry import report_install_telemetry
from nova_harness.core.package.backend import (
    check_dependency_conflicts,
    install_dependencies,
    install_package,
    uninstall_package,
)
from nova_harness.core.package.discovery import collect_all_package_entries
from nova_harness.core.package.locator import SourceResolver
from nova_harness.core.package.metadata.pyproject import (
    read_manifest,
    read_pyproject_name,
    resolve_package_dependencies,
)
from nova_harness.core.package.metadata.validation import (
    is_agent_dir,
    is_extension_path,
    is_tool_dir,
)
from nova_harness.core.package.source import (
    PackageSource,
    _source_str,
    get_package_identity,
    merge_package_source_specs,
    parse_package_source_spec,
    parse_source,
)
from nova_harness.core.package.store import (
    _basename,
    _find_installed_metadata_by_name,
    _find_installed_metadata_by_source,
    _install_path_for_source,
    _is_skill_path,
    _looks_like_source,
    _metadata_file_path,
    _read_package_metadata,
    _sanitize_name,
    _scan_installed_metadata,
    _write_package_metadata,
)
from nova_harness.core.package.utils.fs import (
    copytree,
    ensure_symlink_dir,
    now_iso,
    safe_remove,
)
from nova_harness.core.types.config.settings import PackageSourceSpec
from nova_harness.core.types.package_manager import (
    AmbiguousPackageNameError,
    NovaManifest,
    PackageFilter,
    PackageMetadata,
    PackageView,
    ProgressEvent,
    ResourceMetadata,
)
from nova_harness.core.types.project_trust import ProjectNotTrustedError

logger = logging.getLogger(__name__)


class PackageInstaller:
    """Manage whole packages under a Nova scope directory.

    Each installer instance targets exactly one scope:

    - ``local=False`` (default): user/global scope under ``~/.nova/agent``.
    - ``local=True``: project scope under ``<cwd>/.nova``.

    Packages are kept intact after installation:

    - git sources -> ``<base_dir>/packages/git/<host>/<path>/``
    - path sources -> ``<base_dir>/packages/path/<name>/``

    Resources inside a package (agents, tools, skills, extensions, prompts) are
    resolved directly from the package directory and are never copied out to
    ``agents/`` or ``tools/``.
    """

    def __init__(
        self,
        agent_dir: Optional[str] = None,
        local: bool = False,
        settings_manager: Optional[SettingsManager] = None,
        cwd: Optional[Union[str, Path]] = None,
        on_progress=None,
    ) -> None:
        self.local = local
        self.cwd = Path(cwd or os.getcwd()).resolve()
        self._on_progress = on_progress

        # 安装目标目录（包实际存放位置）
        # project scope 使用 <cwd>/.nova；user scope 使用 ~/.nova/agent。
        self.install_dir = (
            get_project_base_dir(self.cwd)
            if local
            else Path(agent_dir) if agent_dir else get_agent_dir()
        )

        # SettingsManager 始终使用全局 agent_dir，避免 local 模式下把
        # <cwd>/.nova/settings.json 误当作全局 settings。
        self.agent_dir = Path(agent_dir) if agent_dir else get_agent_dir()

        self.packages_dir = self.install_dir / PACKAGES_DIR_NAME
        self.git_root = self.packages_dir / GIT_PACKAGES_DIR_NAME
        self.path_root = self.packages_dir / PATH_PACKAGES_DIR_NAME
        self.agents_dir = self.install_dir / AGENTS_DIR_NAME
        self.prompts_dir = self.install_dir / PROMPTS_DIR_NAME
        self.skills_dir = self.install_dir / SKILLS_DIR_NAME
        self.extensions_dir = self.install_dir / EXTENSIONS_DIR_NAME
        self.themes_dir = self.install_dir / THEMES_DIR_NAME

        self.settings_manager = settings_manager or SettingsManager.create(
            cwd=str(self.cwd),
            agent_dir=str(self.agent_dir),
            project_trusted=True,
        )
        base_dir = str(self.install_dir)
        self.source_resolver = SourceResolver(
            self.install_dir, cwd=self.cwd, on_progress=self._on_progress
        )

    def _ensure_install_dirs(self) -> None:
        """延迟创建包管理目录，直到真正需要写入时。"""
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self.path_root.mkdir(parents=True, exist_ok=True)
        self.git_root.mkdir(parents=True, exist_ok=True)
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.extensions_dir.mkdir(parents=True, exist_ok=True)
        self.themes_dir.mkdir(parents=True, exist_ok=True)

    def set_progress_callback(self, on_progress) -> None:
        """设置安装/更新进度回调。"""
        self._on_progress = on_progress
        # 确保 SourceResolver 使用同一个回调，
        # 否则 git clone/update 的进度事件会在创建 PackageManager 后设置回调时丢失。
        self.source_resolver._on_progress = on_progress

    # ------------------------------------------------------------------
    # Public install / update / uninstall
    # ------------------------------------------------------------------
    def _ensure_editable_spec(
        self, source: PackageSourceSpec, editable: bool
    ) -> PackageSourceSpec:
        """若请求 editable 安装且 source 是字符串/字典，统一转换为含 editable 标记的 dict。"""
        if not editable:
            return source
        if isinstance(source, str):
            return {"source": source, "editable": True}
        if isinstance(source, dict) and not source.get("editable"):
            return {**source, "editable": True}
        return source

    def install(
        self,
        source: PackageSourceSpec,
        no_deps: bool = False,
        dry_run: bool = False,
        quiet: bool = False,
        editable: bool = False,
    ) -> PackageMetadata:
        """Install a package from *source* but do not persist it to settings."""
        self._assert_project_trusted_for_write()
        self._ensure_install_dirs()

        source = self._ensure_editable_spec(source, editable)
        source_str, editable, _ = parse_package_source_spec(source)
        source_obj = parse_source(source_str)
        if editable and source_obj.type != "path":
            raise ValueError("Editable mode only supports path sources")
        source_obj.editable = editable
        abs_src = self.source_resolver.resolve(source_obj)
        manifest = read_manifest(abs_src)

        # 与 pip 行为一致：path/editable 包在同一 scope 内同名时后装覆盖先装。
        # git 包使用 host/repo 派生的目录，不存在同名冲突，保留多个 ref。
        if not dry_run and source_obj.type == "path":
            pkg_name = manifest.name or _basename(abs_src)
            if not pkg_name:
                raise ValueError("Cannot determine package name from source.")
            target_path = _install_path_for_source(
                source_obj, pkg_name, self.path_root, self.git_root
            ).resolve()
            resolved_src = Path(abs_src).resolve()
            # 如果源目录本身就是 Nova 管理目录下的安装副本，不要自删。
            if not (
                resolved_src == target_path
                or target_path in resolved_src.parents
                or resolved_src in target_path.parents
            ):
                self._uninstall_same_name_packages(source_obj, pkg_name)

        return self._run_install_operation(
            abs_src,
            source_obj,
            manifest=manifest,
            no_deps=no_deps,
            dry_run=dry_run,
            quiet=quiet,
        )

    def _run_install_operation(
        self,
        abs_src: str,
        source_obj: PackageSource,
        manifest: Optional[NovaManifest] = None,
        no_deps: bool = False,
        dry_run: bool = False,
        quiet: bool = False,
    ) -> PackageMetadata:
        """安装一个包并返回其元数据。"""
        manifest = manifest or read_manifest(abs_src)
        pkg_name = manifest.name or _basename(abs_src)
        if not pkg_name:
            raise ValueError("Cannot determine package name from source.")

        self._emit_progress(
            "start",
            "install",
            source_obj.spec,
            f"Reading manifest for '{pkg_name}'...",
            percent=0.05,
        )

        entries = collect_all_package_entries(abs_src)
        if not any(entries.values()):
            raise ValueError(
                f"Package '{pkg_name}' has no agents, tools, skills, extensions, prompts, or themes to install."
            )

        self._emit_progress(
            "progress",
            "install",
            source_obj.spec,
            f"Preparing package '{pkg_name}'...",
            percent=0.10,
        )
        (
            deps,
            requirements_path,
            package_name,
            install_python_package,
        ) = self._prepare_install(abs_src, manifest, entries)

        # Phase 1: 解析并安装/检查 Python 依赖。
        # 所有 Python 依赖都安装到 Nova 自身运行的 Python 环境；user/project
        # scope 只影响 Nova 管理的资源目录分组。
        if self._should_install_deps(manifest, no_deps) and (deps or requirements_path):
            self._emit_progress(
                "progress",
                "install",
                source_obj.spec,
                f"Installing Python dependencies for '{pkg_name}'...",
                percent=0.30,
            )
            if dry_run:
                output = install_dependencies(
                    deps,
                    requirements_path=requirements_path,
                    dry_run=True,
                )
                if not quiet and output:
                    logger.info("Python dependency dry-run: OK\n%s", output)
            else:
                check_dependency_conflicts(deps, requirements_path=requirements_path)
                install_dependencies(deps, requirements_path=requirements_path)

        editable = source_obj.editable

        # Phase 2: 将包复制/链接到 Nova 管理目录。
        self._emit_progress(
            "progress",
            "install",
            source_obj.spec,
            f"{'Linking' if editable else 'Copying'} '{pkg_name}' to Nova directory...",
            percent=0.60,
        )
        install_path = _install_path_for_source(
            source_obj, pkg_name, self.path_root, self.git_root
        )
        if dry_run:
            final_install_path = str(install_path)
        else:
            final_install_path = self._materialize_package(
                abs_src, install_path, source_obj, pkg_name
            )

        # Phase 3: 当包包含 tools 或 extensions 且为可构建 Python 包时，
        # 将包自身安装到当前 Python 环境。
        if install_python_package and not dry_run:
            self._emit_progress(
                "progress",
                "install",
                source_obj.spec,
                f"Installing Python package '{package_name}' into environment...",
                percent=0.85,
            )
            # 普通安装从 Nova 管理目录的副本安装，保证原源删除后仍可用；
            # editable 安装直接从原始源安装。
            pip_src = abs_src if editable else final_install_path
            install_package(pip_src, editable=editable)

        self._emit_progress(
            "complete",
            "install",
            source_obj.spec,
            f"Installation of '{pkg_name}' complete.",
            percent=1.0,
        )

        if dry_run and not quiet:
            pkg_hint = (
                f" and editable Python package '{package_name}'"
                if editable and install_python_package
                else (
                    f" and Python package '{package_name}'"
                    if install_python_package
                    else ""
                )
            )
            if editable:
                logger.info(
                    "[dry-run] Would reference %s in place at %s with %d agent(s), %d tool(s), %d skill(s), %d extension(s), %d prompt(s), %d theme(s)%s",
                    pkg_name,
                    abs_src,
                    len(entries.get("agents", [])),
                    len(entries.get("tools", [])),
                    len(entries.get("skills", [])),
                    len(entries.get("extensions", [])),
                    len(entries.get("prompts", [])),
                    len(entries.get("themes", [])),
                    pkg_hint,
                )
            else:
                logger.info(
                    "[dry-run] Would install %s with %d agent(s), %d tool(s), %d skill(s), %d extension(s), %d prompt(s), %d theme(s)%s",
                    pkg_name,
                    len(entries.get("agents", [])),
                    len(entries.get("tools", [])),
                    len(entries.get("skills", [])),
                    len(entries.get("extensions", [])),
                    len(entries.get("prompts", [])),
                    len(entries.get("themes", [])),
                    pkg_hint,
                )

        meta = PackageMetadata(
            name=pkg_name,
            version=manifest.version,
            description=manifest.description,
            source=source_obj.spec,
            install_path=final_install_path,
            installed_at=now_iso(),
            author=manifest.author,
            package_name=package_name if install_python_package else "",
            editable=editable,
            dependencies=deps,
        )

        if not dry_run:
            _write_package_metadata(final_install_path, meta)

        return meta

    def _prepare_install(
        self,
        abs_src: str,
        manifest: NovaManifest,
        entries: Dict[str, List[str]],
    ) -> tuple[List[str], Optional[str], str, bool]:
        """准备安装所需的元数据，返回依赖、Python 包名等。"""
        deps, requirements_path = resolve_package_dependencies(abs_src)
        package_name = read_pyproject_name(abs_src) or ""
        install_python_package = bool(
            (entries.get("tools") or entries.get("extensions"))
            and package_name
            and self._is_installable_python_package(abs_src)
        )
        has_tools_or_extensions = bool(
            entries.get("tools") or entries.get("extensions")
        )
        if has_tools_or_extensions and not package_name:
            display_name = manifest.name or Path(abs_src).name
            raise ValueError(
                f"Package '{display_name}' contains tools or extensions but does not "
                "declare a Python package name. Please set project.name in pyproject.toml "
                "so that tool/extension helper modules can be imported."
            )

        return deps, requirements_path, package_name, install_python_package

    def _is_installable_python_package(self, package_dir: str) -> bool:
        """Check whether *package_dir* can be installed as a Python package.

        A directory is considered installable if it has a ``setup.py``, a
        ``setup.cfg``, or a ``pyproject.toml`` declaring a build system.
        """
        pkg_path = Path(package_dir)
        if (pkg_path / "setup.py").exists():
            return True
        if (pkg_path / "setup.cfg").exists():
            return True
        pyproject = pkg_path / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib

                with pyproject.open("rb") as f:
                    data = tomllib.load(f)
                if data.get("build-system", {}).get("requires"):
                    return True
            except Exception:
                pass
        return False

    def _materialize_package(
        self,
        abs_src: str,
        install_path: Path,
        source_obj: PackageSource,
        pkg_name: str,
    ) -> str:
        """将包复制或链接到 Nova 管理目录，返回最终安装路径。"""
        if source_obj.editable:
            symlink_path = str(self.path_root / _sanitize_name(pkg_name))
            ensure_symlink_dir(abs_src, symlink_path)
            return symlink_path

        # 普通 path / git 源：复制整个包到 install_path。
        # git 源下 resolver 已经把内容放到 install_path，跳过自复制。
        if abs_src != str(install_path):
            safe_remove(str(install_path))
            copytree(abs_src, str(install_path))
        return str(install_path)

    def _should_install_deps(self, manifest: NovaManifest, no_deps: bool) -> bool:
        """根据 manifest 与 no_deps 标志判断是否需要安装依赖。"""
        if no_deps:
            return False
        if manifest.nova is not None and not manifest.nova.auto_install_dependencies:
            return False
        return True

    def _uninstall_same_name_packages(
        self, source_obj: PackageSource, pkg_name: str
    ) -> None:
        """移除本 scope 内会与新包安装到同一目录的旧包。

        直接按目标 install_path 清理，避免递归调用 ``self.uninstall`` 带来的
        部分失败风险和状态不一致。
        """
        target_path = _install_path_for_source(
            source_obj, pkg_name, self.path_root, self.git_root
        )

        # 清理所有实际安装到同一 target_path 的 settings 记录。
        for meta in _scan_installed_metadata(self.path_root):
            if Path(meta.install_path) == target_path:
                try:
                    self.settings_manager.remove_package_source(
                        meta.source,
                        local=self.local,
                        base_dir=str(self.install_dir),
                        cwd=str(self.cwd),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to remove stale settings entry for '%s': %s",
                        meta.source,
                        exc,
                    )

        # 清理目标目录与 sibling 元数据文件。
        safe_remove(str(target_path))
        safe_remove(str(_metadata_file_path(str(target_path))))

    def install_and_persist(
        self,
        source: PackageSourceSpec,
        no_deps: bool = False,
        dry_run: bool = False,
        quiet: bool = False,
        editable: bool = False,
    ) -> PackageMetadata:
        """Install a package and persist its source to settings."""
        source = self._ensure_editable_spec(source, editable)

        meta = self.install(
            source,
            no_deps=no_deps,
            dry_run=dry_run,
            quiet=quiet,
        )
        if not dry_run:
            self.settings_manager.add_package_source(
                source,
                local=self.local,
                base_dir=str(self.install_dir),
                cwd=str(self.cwd),
            )
            source_str, _, _ = parse_package_source_spec(source)
            report_install_telemetry(
                self.settings_manager,
                event="package_install",
                payload={
                    "source": source_str,
                    "editable": meta.editable,
                    "name": meta.name,
                    "version": meta.version,
                    "package_name": meta.package_name,
                },
            )
        return meta

    def update(
        self,
        name_or_source: PackageSourceSpec,
    ) -> PackageMetadata:
        """Re-install or switch a package from its recorded or given source."""
        self._assert_project_trusted_for_write()
        self._ensure_install_dirs()

        if isinstance(name_or_source, dict) or _looks_like_source(
            _source_str(name_or_source)
        ):
            return self._update_by_source(name_or_source)

        spec = self.find_spec_by_name(name_or_source)
        if spec is None:
            raise ValueError(f"Package '{name_or_source}' is not installed.")

        source_str = _source_str(spec)
        meta = self.info(source_str)
        if meta is None:
            raise ValueError(
                f"Package '{name_or_source}' has no recorded source; cannot update."
            )
        return self.install_and_persist(spec)

    def _update_by_source(self, source: PackageSourceSpec) -> PackageMetadata:
        source_str, _, _ = parse_package_source_spec(source)
        source_obj = parse_source(source_str)
        identity = get_package_identity(source, base_dir=str(self.install_dir))

        # 保留原安装的 editable 状态与 filters；先查 settings 中的包，再扫描已安装 metadata。
        old_spec: Optional[PackageSourceSpec] = None
        for pkg in self.list():
            if (
                get_package_identity(pkg.source, base_dir=str(self.install_dir))
                == identity
            ):
                old_spec = pkg.source
                break
        else:
            for root in (self.path_root, self.git_root):
                if not root.exists():
                    continue
                for meta in _scan_installed_metadata(root):
                    if (
                        get_package_identity(
                            meta.source, base_dir=str(self.install_dir)
                        )
                        == identity
                    ):
                        old_spec = meta.source
                        break
                if old_spec is not None:
                    break

        if old_spec is None:
            # 没有任何记录时按新 source 处理，editable 默认 False。
            spec: PackageSourceSpec = source
        else:
            # 用新 source 覆盖，同时继承旧 spec 的 editable/filters。
            spec = merge_package_source_specs(old_spec, {"source": source_str})

        return self.install_and_persist(spec)

    def _emit_progress(
        self,
        event_type: str,
        action: str,
        source: str,
        message: str,
        percent: Optional[float] = None,
    ) -> None:
        """Emit a progress event if a callback is registered."""
        if self._on_progress is not None:
            self._on_progress(
                ProgressEvent(
                    type=event_type,
                    action=action,
                    source=source,
                    message=message,
                    percent=percent,
                )
            )

    def uninstall(
        self, name_or_source: str, *, uninstall_python_package: bool = True
    ) -> bool:
        """Remove an installed package by name or source spec."""
        self._assert_project_trusted_for_write()
        self._emit_progress(
            "start",
            "remove",
            name_or_source,
            f"Removing '{name_or_source}'...",
            percent=0.05,
        )
        try:
            if _looks_like_source(name_or_source):
                result = self._uninstall_by_source(name_or_source)
                self._emit_progress(
                    "complete" if result else "error",
                    "remove",
                    name_or_source,
                    (
                        f"Removed '{name_or_source}'"
                        if result
                        else f"Package '{name_or_source}' not found"
                    ),
                    percent=1.0 if result else None,
                )
                return result

            meta = self.find_by_name(name_or_source)
            if meta is None:
                # Fallback: scan installed metadata when source is gone.
                meta = _find_installed_metadata_by_name(
                    self.path_root, name_or_source, self.git_root
                )
            if meta is None:
                self._emit_progress(
                    "error",
                    "remove",
                    name_or_source,
                    f"Package '{name_or_source}' not found",
                )
                return False

            source_label = meta.source or name_or_source

            # 卸载 editable 包时只删除 symlink，保留原始源目录；
            # 非 editable 包则删除 Nova 管理目录下的完整副本。
            self._emit_progress(
                "progress",
                "remove",
                source_label,
                f"Removing package files for '{meta.name}'...",
                percent=0.40,
            )
            safe_remove(meta.install_path)

            # 同时删除 Nova 管理的元数据文件（与 install dir 为同级文件）。
            metadata_path = _metadata_file_path(meta.install_path)
            safe_remove(str(metadata_path))

            # 清理空目录（全局或项目级），对 git 包递归向上清理中间空目录。
            self._prune_empty_parents(Path(meta.install_path))

            if meta.package_name and uninstall_python_package:
                # 只有当前 scope 内没有其他包再引用同名 Python 包时才卸载，避免破坏共享
                # 该 Python 包的其他 Nova bundle。
                if self.count_package_name_references(meta.package_name) == 0:
                    self._emit_progress(
                        "progress",
                        "remove",
                        source_label,
                        f"Uninstalling Python package '{meta.package_name}'...",
                        percent=0.75,
                    )
                    uninstall_package(meta.package_name)

            if meta.source:
                self.settings_manager.remove_package_source(
                    meta.source,
                    local=self.local,
                    base_dir=str(self.install_dir),
                    cwd=str(self.cwd),
                )

            self._emit_progress(
                "complete",
                "remove",
                source_label,
                f"Removed '{meta.name}'",
                percent=1.0,
            )
            return True
        except Exception as exc:
            self._emit_progress(
                "error",
                "remove",
                name_or_source,
                f"Failed to remove '{name_or_source}': {exc}",
            )
            raise

    def count_package_name_references(self, package_name: str) -> int:
        """Return the number of installed packages in this scope that reference *package_name*."""
        count = 0
        for root in (self.path_root, self.git_root):
            if not root.exists():
                continue
            for installed in _scan_installed_metadata(root):
                if installed.package_name == package_name:
                    count += 1
        return count

    def _prune_empty_parents(self, install_path: Path) -> None:
        """从 install_path 的父目录向上清理空目录，直到 install_dir 根。"""
        # 判断停止边界：path_root 或 git_root 本身。
        stop_roots = {self.path_root, self.git_root}
        current = install_path.parent
        while current not in stop_roots and current != current.parent:
            if not current.exists():
                current = current.parent
                continue
            try:
                if not any(current.iterdir()):
                    current.rmdir()
                else:
                    break
            except OSError:
                break
            current = current.parent

    def _uninstall_by_source(self, source: str) -> bool:
        identity = get_package_identity(source, base_dir=str(self.install_dir))
        for pkg in self.list():
            if (
                get_package_identity(pkg.source, base_dir=str(self.install_dir))
                == identity
            ):
                return self.uninstall(pkg.name)
        return False

    def _assert_project_trusted_for_write(self) -> None:
        """Project scope 写操作前校验项目信任状态。

        任何可能修改 ``<cwd>/.nova`` 的操作（install / update / uninstall）都必须先
        通过信任门控。
        """
        if not self.local:
            return
        if not self.settings_manager.is_project_trusted():
            raise ProjectNotTrustedError(str(self.cwd))

    # ------------------------------------------------------------------
    # List / info / validate
    # ------------------------------------------------------------------
    def list(self) -> List[PackageMetadata]:
        """Return installed packages.

        合并 settings 中记录的包源与 Nova 管理目录下实际存在 metadata 的包。
        这样即使某个包的 settings 条目被删除、或安装时未持久化到 settings，
        ``list()`` / ``uninstall()`` 仍能看到它。
        """
        results: List[PackageMetadata] = []
        seen_identities: set = set()
        base_dir = str(self.install_dir)

        # 1. settings 中记录的源（对 source spec 和 filters 具有权威性）。
        for source in self.settings_manager.get_package_sources(
            local=self.local, base_dir=base_dir
        ):
            source_str = _source_str(source) if source else ""
            if not source_str:
                continue
            identity = get_package_identity(source_str, base_dir=base_dir)
            if identity in seen_identities:
                continue
            seen_identities.add(identity)

            try:
                meta = self._metadata_for_source(source)
            except Exception as exc:
                logger.warning(
                    "Failed to resolve installed package %s: %s", source_str, exc
                )
                continue

            if meta is not None:
                results.append(meta)

        # 2. 扫描 Nova 管理目录下的 metadata，补充 settings 中没有的包。
        for root in (self.path_root, self.git_root):
            if not root.exists():
                continue
            for meta in _scan_installed_metadata(root):
                identity = get_package_identity(meta.source, base_dir=base_dir)
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
                results.append(meta)

        return results

    def list_with_resources(self) -> Dict[str, PackageView]:
        """Return a view of packages with their agents, tools, skills and extensions.

        The returned dictionary is keyed by package identity (source-based) rather
        than display name, so two packages with the same ``manifest.name`` do not
        overwrite each other.
        """
        result: Dict[str, PackageView] = {}
        for pkg in self.list():
            identity = get_package_identity(pkg.source, str(self.install_dir))
            try:
                entries = collect_all_package_entries(pkg.install_path)
            except Exception as exc:
                logger.warning(
                    "Failed to read package entries for %s: %s", pkg.name, exc
                )
                entries = {}

            def _resource_metadata(paths, resource_type):
                return [
                    ResourceMetadata(
                        name=_basename(p),
                        resource_type=resource_type,
                        source=pkg.source,
                        install_path=pkg.install_path,
                    )
                    for p in paths
                ]

            result[identity] = PackageView(
                name=pkg.name,
                version=pkg.version,
                description=pkg.description,
                agents=_resource_metadata(entries.get("agents", []), "agent"),
                tools=_resource_metadata(entries.get("tools", []), "tool"),
                skills=_resource_metadata(entries.get("skills", []), "skill"),
                extensions=_resource_metadata(
                    entries.get("extensions", []), "extension"
                ),
                prompts=_resource_metadata(entries.get("prompts", []), "prompt"),
                themes=_resource_metadata(entries.get("themes", []), "theme"),
            )

        return result

    def info(self, name_or_source: str) -> Optional[PackageMetadata]:
        """Get metadata for a single installed package by name or source spec."""
        if _looks_like_source(name_or_source):
            identity = get_package_identity(
                name_or_source, base_dir=str(self.install_dir)
            )
            for pkg in self.list():
                if (
                    get_package_identity(pkg.source, base_dir=str(self.install_dir))
                    == identity
                ):
                    return pkg
            return None
        return self.find_by_name(name_or_source)

    def validate(self, source: str) -> List[str]:
        """Validate a package directory and return a list of issues (empty if OK)."""
        source_obj = parse_source(source)
        try:
            local_dir = self.source_resolver.resolve(source_obj)
        except ValueError as exc:
            return [str(exc)]

        entries = collect_all_package_entries(local_dir)
        has_any = any(entries.values())

        if not has_any:
            return [
                "Package must declare agents, tools, skills, extensions, prompts, or themes in manifest, "
                "or contain agents/ / tools/ / skills/ / extensions/ / prompts/ / themes/ directories"
            ]

        issues: List[str] = []
        for src_path in entries.get("agents", []):
            if not is_agent_dir(src_path):
                issues.append(f"Not a valid agent: {src_path}")
        for src_path in entries.get("tools", []):
            if not is_tool_dir(src_path):
                issues.append(f"Not a valid tool: {src_path}")
        for src_path in entries.get("skills", []):
            if not _is_skill_path(src_path):
                issues.append(f"Not a valid skill: {src_path}")
        for src_path in entries.get("extensions", []):
            if not is_extension_path(src_path):
                issues.append(f"Not a valid extension: {src_path}")

        return issues

    def find_spec_by_name(self, name: str) -> Optional[PackageSourceSpec]:
        """Find the original source spec for an installed package by name."""
        base_dir = str(self.install_dir)
        for spec in self.settings_manager.get_package_sources(
            local=self.local, base_dir=base_dir
        ):
            try:
                meta = self._metadata_for_source(spec)
            except Exception:
                continue
            if meta is not None and meta.name == name:
                return spec
        # Fallback: scan installed metadata when source is no longer resolvable.
        meta = _find_installed_metadata_by_name(self.path_root, name, self.git_root)
        if meta is not None:
            if meta.editable:
                return {"source": meta.source, "editable": True}
            return meta.source
        return None

    def find_by_name(self, name: str) -> Optional[PackageMetadata]:
        """Find a single installed package by name."""
        matches = [pkg for pkg in self.list() if pkg.name == name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousPackageNameError(name)
        # Fallback: scan installed metadata when the original source is gone.
        return _find_installed_metadata_by_name(self.path_root, name, self.git_root)

    def _metadata_for_source(
        self, source: PackageSourceSpec
    ) -> Optional[PackageMetadata]:
        """Resolve a source spec to its package metadata.

        For git sources, the install path is deterministic from host/repo_path.
        When the package is already installed locally we read the manifest from
        the install directory, avoiding network side effects during ``list()``.

        For path sources, if the original source is no longer available, we fall
        back to the persisted ``.nova-package.json`` in the Nova install directory
        so the package remains listable/uninstallable.
        """
        base_dir = str(self.install_dir)
        source_str = _source_str(source)
        source_obj = parse_source(source_str)
        filtered, filters = self._filter_info(source)

        # Git install path does not depend on the manifest name, so we can check
        # it directly without resolving the source. If the package is not installed
        # locally, return None: list/info/uninstall should not trigger network clones.
        if source_obj.type == "git":
            install_path = _install_path_for_source(
                source_obj, "", self.path_root, self.git_root
            )
            if install_path.exists():
                meta = _read_package_metadata(str(install_path))
                if meta is not None:
                    meta.filtered = filtered
                    meta.filters = filters
                    return meta
            return None

        # For path installs the Nova-managed copy is the source of truth once the
        # original source has been removed. The install directory name is derived
        # from the package name, so we cannot compute it from the source spec
        # alone; instead we scan the persisted metadata files for a matching source.
        if source_obj.type == "path":
            meta = _find_installed_metadata_by_source(
                self.path_root,
                source_str,
                base_dir=base_dir,
            )
            if meta is not None:
                meta.filtered = filtered
                meta.filters = filters
                return meta

        try:
            resolved_dir = self.source_resolver.resolve(source_obj)
        except Exception:
            return None

        return self._build_metadata_from_dir(
            resolved_dir, source_str, filtered=filtered, filters=filters
        )

    def _filter_info(self, source: PackageSourceSpec) -> tuple[bool, "PackageFilter"]:
        """从 source spec 中提取是否带过滤器以及过滤器内容。"""
        _, _, filters = parse_package_source_spec(source)
        filtered = isinstance(source, dict) and any(
            getattr(filters, field) is not None
            for field in (
                "extensions",
                "skills",
                "prompts",
                "themes",
                "tools",
                "agents",
            )
        )
        return filtered, filters

    def _build_metadata_from_dir(
        self,
        package_dir: str,
        source_str: str,
        *,
        filtered: bool = False,
        filters: Optional[PackageFilter] = None,
    ) -> PackageMetadata:
        """Build ``PackageMetadata`` from an installed/local package directory."""
        source_obj = parse_source(source_str)
        # Prefer existing persisted metadata if present; it has accurate
        # install_path/installed_at and avoids recomputing package_name.
        install_path = _install_path_for_source(
            source_obj, "", self.path_root, self.git_root
        )
        if install_path.exists():
            meta = _read_package_metadata(str(install_path))
            if meta is not None:
                meta.filtered = filtered
                meta.filters = filters or PackageFilter()
                return meta

        manifest = read_manifest(package_dir)
        pkg_name = manifest.name or _basename(package_dir)

        install_path = str(
            _install_path_for_source(
                source_obj, pkg_name, self.path_root, self.git_root
            )
        )

        # 当包包含 tools 或 extensions 时，安装阶段可能已经安装了 Python 包，
        # 因此按与安装阶段相同的方式计算 package_name。
        entries = collect_all_package_entries(package_dir)
        pyproject_name = read_pyproject_name(package_dir)
        package_name = (
            pyproject_name
            if ((entries.get("tools") or entries.get("extensions")) and pyproject_name)
            else ""
        )
        deps, _ = resolve_package_dependencies(package_dir)

        return PackageMetadata(
            name=pkg_name,
            version=manifest.version,
            description=manifest.description,
            source=source_str,
            install_path=install_path,
            installed_at="",
            author=manifest.author,
            package_name=package_name,
            dependencies=deps,
            filtered=filtered,
            filters=filters or PackageFilter(),
        )

    def list_installed_fallback_sources(self) -> List[str]:
        """扫描已安装包的 metadata，返回未在 settings 中记录的 source。

        同时扫描 path 与 git 安装根目录，确保两类已安装但未持久化到 settings 的包
        仍能被资源解析器纳入。
        """
        base_dir = str(self.install_dir)
        settings_sources = {
            get_package_identity(s, base_dir)
            for s in self.settings_manager.get_package_sources(
                local=self.local, base_dir=base_dir
            )
        }
        fallback: List[str] = []
        for root in (self.path_root, self.git_root):
            if not root.exists():
                continue
            for meta in _scan_installed_metadata(root):
                identity = get_package_identity(meta.source, base_dir)
                if identity not in settings_sources:
                    fallback.append(meta.source)
        return fallback


__all__ = ["PackageInstaller"]
