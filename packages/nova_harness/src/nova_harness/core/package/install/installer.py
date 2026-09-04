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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from nova_harness.core.config.defaults import (
    GIT_PACKAGES_DIR_NAME,
    NPM_PACKAGES_DIR_NAME,
    PACKAGES_DIR_NAME,
    PATH_PACKAGES_DIR_NAME,
    get_agent_dir,
    get_project_base_dir,
)
from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.package.binaries import ensure_binary
from nova_harness.core.package.install.python_backend import (
    NoPipHostError,
    check_dependency_conflicts,
    install_dependencies,
    install_package,
    uninstall_package,
)
from nova_harness.core.package.install.store import (
    basename,
    derive_package_metadata,
    derive_python_package_name,
    dist_info_dir,
    install_path_for_source,
    looks_like_source,
    metadata_dedup_key,
    read_dist_info,
    sanitize_name,
    scan_installed_package_dirs,
    write_dist_info,
)
from nova_harness.core.package.manifest import (
    is_installable_python_package,
    load_package_json,
    read_manifest,
    read_package_name,
    resolve_package_dependencies,
)
from nova_harness.core.package.resolve.discovery import collect_all_package_entries
from nova_harness.core.package.source.resolver import SourceResolver
from nova_harness.core.package.source.spec import (
    PackageSource,
    get_package_identity,
    get_package_source_string,
    merge_package_source_specs,
    parse_package_source_spec,
    parse_source,
)
from nova_harness.core.package.utils import (
    copytree,
    ensure_symlink_dir,
    is_offline_mode_enabled,
    safe_remove,
)
from nova_harness.core.package.validation import (
    is_agent_file,
    is_extension_path,
    is_skill_path,
    is_tool_dir,
    is_user_tool_dir,
)
from nova_harness.core.types.config.settings import PackageSourceSpec
from nova_harness.core.types.package import (
    AmbiguousPackageNameError,
    NovaManifest,
    PackageFilter,
    PackageManifest,
    PackageMetadata,
    PackageView,
    ProgressEvent,
    ResourceMetadata,
)
from nova_harness.core.utils.binaries import binary_install_guidance, resolve_binary
from nova_harness.core.utils.telemetry import report_install_telemetry

logger = logging.getLogger(__name__)


def _is_pure_ts_package(root: Path) -> bool:
    """B 型纯 TS 包判定：package.json 身份证 + ``tui/`` 宿主段（包根即前端半区）。"""
    return (root / "package.json").exists() and (root / "tui").is_dir()


def _npm_manifest_dir(root: Path) -> Path:
    """npm 清单探测目录（安装流程第 4 阶段的触发点）。

    两段式包结构下 npm 清单在前端半区：B 型纯 TS 包的根即前端半区
    （``package.json`` 在包根）；A 型复合包的前端半区在 ``frontend/``
    （``package.json`` 在 ``frontend/`` 下）。不做双轨——A 型包根的
    遗留 ``package.json`` 不再触发。
    """
    return root if _is_pure_ts_package(root) else root / "frontend"


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
        requires_checker=None,
    ) -> None:
        self.local = local
        self.cwd = Path(cwd or os.getcwd()).resolve()
        self._on_progress = on_progress
        # 包间依赖校验回调（PackageManager 注入，合并 user/project 视图）：
        # 输入 requires 名清单，返回缺失名清单；None = 不校验。
        self._requires_checker = requires_checker

        # 安装目标目录（包实际存放位置）
        # project scope 使用 <cwd>/.nova；user scope 使用 ~/.nova/agent。
        # install_dir 统一 resolve（跟随 symlink）——路径基准必须和
        # resolve_managed_path / 磁盘扫描一致，否则 /tmp vs /private/tmp
        # 这类 symlink 差异会让同一安装路径出现两种字符串形式，去重失效。
        self.install_dir = (
            get_project_base_dir(self.cwd).resolve()
            if local
            else Path(agent_dir).resolve() if agent_dir else get_agent_dir().resolve()
        )

        # SettingsManager 始终使用全局 agent_dir，避免 local 模式下把
        # <cwd>/.nova/settings.json 误当作全局 settings。
        self.agent_dir = Path(agent_dir).resolve() if agent_dir else get_agent_dir()

        self.packages_dir = self.install_dir / PACKAGES_DIR_NAME
        self.git_root = self.packages_dir / GIT_PACKAGES_DIR_NAME
        self.path_root = self.packages_dir / PATH_PACKAGES_DIR_NAME
        self.npm_root = self.packages_dir / NPM_PACKAGES_DIR_NAME

        self.settings_manager = settings_manager or SettingsManager.create(
            cwd=str(self.cwd),
            agent_dir=str(self.agent_dir),
            project_trusted=True,
        )
        self.source_resolver = SourceResolver(
            self.install_dir, cwd=self.cwd, on_progress=self._on_progress
        )

    def _ensure_install_dirs(self) -> None:
        """延迟创建包管理目录，直到真正需要写入时。"""
        self.git_root.mkdir(parents=True, exist_ok=True)
        self.path_root.mkdir(parents=True, exist_ok=True)
        self.npm_root.mkdir(parents=True, exist_ok=True)
        # settings.json 可提交共享（团队配置随仓库走），但安装产物
        # （packages/ 下的副本与 git 缓存）必须不被 git 追踪——对齐
        # pi 的 ensureGitIgnore，在安装根写 .gitignore 排除全部内容。
        gitignore = self.packages_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")

    def set_progress_callback(self, on_progress) -> None:
        """设置安装/更新进度回调。"""
        self._on_progress = on_progress
        self.source_resolver.set_progress_callback(on_progress)

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
        self._ensure_install_dirs()

        source = self._ensure_editable_spec(source, editable)
        source_str, editable, _ = parse_package_source_spec(source)
        source_obj = parse_source(source_str)
        if editable and source_obj.type != "path":
            raise ValueError("Editable mode only supports path sources")
        source_obj.editable = editable
        # install/update 流程：git 缓存存在时同步到配置 ref 的最新状态。
        abs_src = self.source_resolver.resolve(source_obj, update=True)
        manifest = read_manifest(abs_src)

        # 与 pip 行为一致：path/editable 包在同一 scope 内同名时后装覆盖先装。
        # git 包使用 host/repo 派生的目录，不存在同名冲突，保留多个 ref。
        if not dry_run and source_obj.type == "path":
            pkg_name = manifest.name or basename(abs_src)
            if not pkg_name:
                raise ValueError("Cannot determine package name from source.")
            target_path = install_path_for_source(
                source_obj, pkg_name, self.path_root, self.git_root
            ).resolve()
            resolved_src = Path(abs_src).resolve()
            # 源目录包含安装目标时，复制会递归进自身（copytree 祖先→后代），
            # 直接拒绝——典型场景是把 Nova 管理目录本身或其祖先当包安装。
            if resolved_src in target_path.parents:
                raise ValueError(
                    f"Cannot install from '{resolved_src}': it contains the "
                    f"install target '{target_path}' (copying a directory "
                    f"into itself would recurse)."
                )
            # 如果源目录本身就是 Nova 管理目录下的安装副本，不要自删。
            if not (resolved_src == target_path or target_path in resolved_src.parents):
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
        pkg_name = manifest.name or basename(abs_src)
        if not pkg_name:
            raise ValueError("Cannot determine package name from source.")

        # 包间依赖校验（requires）：在任何副作用（依赖安装/复制/下载）之前
        # 拒绝——缺失即不可安装，错误文本附安装提示。
        requires = (manifest.nova.requires if manifest.nova else None) or []
        if requires and self._requires_checker is not None:
            missing = self._requires_checker(requires)
            if missing:
                raise ValueError(
                    f"Package '{pkg_name}' requires "
                    f"{', '.join(repr(n) for n in missing)} — "
                    "install them first (nova-pkg install <source>)."
                )

        self._emit_progress(
            "start",
            "install",
            source_obj.spec,
            f"Reading manifest for '{pkg_name}'...",
            percent=0.05,
        )

        entries = collect_all_package_entries(abs_src)
        # B 型纯 TS 包（package.json 身份证 + tui/ 宿主段）无能力类目，合法放行；
        # 其余空包直接拒绝。
        is_pure_ts_package = _is_pure_ts_package(Path(abs_src))
        if not any(entries.values()) and not is_pure_ts_package:
            raise ValueError(
                f"Package '{pkg_name}' has no agents, tools, skills, extensions, or prompts to install."
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
        ) = self._prepare_install(abs_src)

        # 二进制依赖（wheel 条目，如 rg → ripgrep）并入 pip 依赖，
        # 随 Phase 1 一起安装——二进制随 wheel 落进当前环境的 bin/。
        if manifest.nova and manifest.nova.binary_dependencies:
            deps = list(deps) + [
                pkg for pkg in manifest.nova.binary_dependencies.values() if pkg
            ]
        # 仅声明自管理/系统二进制（无 pip 依赖）的包也必须进入 Phase 1
        has_binary_deps = bool(
            manifest.nova
            and (
                manifest.nova.binary_managed_dependencies
                or manifest.nova.binary_system_dependencies
            )
        )

        # Phase 1: 解析并安装/检查 Python 依赖与二进制依赖。
        # 所有 Python 依赖都安装到 Nova 自身运行的 Python 环境；user/project
        # scope 只影响 Nova 管理的资源目录分组。
        if self._should_install_deps(manifest, no_deps) and (
            deps or requirements_path or has_binary_deps
        ):
            self._emit_progress(
                "progress",
                "install",
                source_obj.spec,
                f"Installing Python dependencies for '{pkg_name}'...",
                percent=0.30,
            )
            if dry_run:
                if deps or requirements_path:
                    output = install_dependencies(
                        deps,
                        requirements_path=requirements_path,
                        dry_run=True,
                    )
                    if not quiet and output:
                        logger.info("Python dependency dry-run: OK\n%s", output)
            else:
                if deps or requirements_path:
                    try:
                        check_dependency_conflicts(
                            deps,
                            requirements_path=requirements_path,
                            install_dir=str(self.install_dir),
                        )
                        install_dependencies(
                            deps,
                            requirements_path=requirements_path,
                            install_dir=str(self.install_dir),
                        )
                    except NoPipHostError as exc:
                        # 冻结形态无 pip 宿主：包装好、依赖待补（装配/加载时
                        # 给指引），不阻断安装本身
                        logger.warning("%s", exc)
                        self._emit_progress(
                            "progress",
                            "install",
                            source_obj.spec,
                            f"警告：{exc}",
                            percent=0.35,
                        )
                self._ensure_managed_binaries(manifest, pkg_name, source_obj)
                self._verify_binary_dependencies(manifest, pkg_name)

        editable = source_obj.editable

        # Phase 2: 将包复制/链接到 Nova 管理目录。
        self._emit_progress(
            "progress",
            "install",
            source_obj.spec,
            f"{'Linking' if editable else 'Copying'} '{pkg_name}' to Nova directory...",
            percent=0.60,
        )
        install_path = install_path_for_source(
            source_obj, pkg_name, self.path_root, self.git_root
        )
        if dry_run:
            final_install_path = str(install_path)
        else:
            final_install_path = self._materialize_package(
                abs_src, install_path, source_obj, pkg_name
            )

        # Phase 3: 当包自身是可安装的 Python 包（name + build-system）时，
        # 将其安装到当前 Python 环境，使 executor/extension 能通过标准
        # import 引用包内共享模块。冻结形态跳过——sys.path 挂载替代
        # （runtime_paths.ensure_package_paths，pip -e 的 .pth 等价物）。
        if install_python_package and not dry_run and not getattr(sys, "frozen", False):
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

        # Phase 4: 前端半区存在 package.json 时装配 npm 依赖（node_modules 每包自含）。
        # 探测点：A 型复合包为 <包根>/frontend/package.json；B 型纯 TS 包的根即
        # 前端半区，探测包根 package.json（不做双轨，A 型包根遗留 package.json
        # 不再触发）。editable 在源目录执行（与 pip -e 同语义），copy 模式在安装
        # 副本内执行；失败/离线/npm 缺失仅警告——TS 资产降级，能力部分不受影响
        # （失败解耦），Node 层加载时会自愈补装。
        if dry_run:
            if (_npm_manifest_dir(Path(abs_src)) / "package.json").exists():
                self._emit_progress(
                    "progress",
                    "install",
                    source_obj.spec,
                    "Would install npm dependencies in package frontend half...",
                    percent=0.95,
                )
        else:
            self._ensure_npm_dependencies(
                abs_src=abs_src,
                editable=editable,
                final_install_path=final_install_path,
                pkg_name=pkg_name,
                source_obj=source_obj,
            )

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
                    "[dry-run] Would reference %s in place at %s with %d agent(s), %d tool(s), %d skill(s), %d extension(s), %d prompt(s), %d user tool(s)%s",
                    pkg_name,
                    abs_src,
                    len(entries.get("agents", [])),
                    len(entries.get("tools", [])),
                    len(entries.get("skills", [])),
                    len(entries.get("extensions", [])),
                    len(entries.get("prompts", [])),
                    len(entries.get("user_tools", [])),
                    pkg_hint,
                )
            else:
                logger.info(
                    "[dry-run] Would install %s with %d agent(s), %d tool(s), %d skill(s), %d extension(s), %d prompt(s), %d user tool(s)%s",
                    pkg_name,
                    len(entries.get("agents", [])),
                    len(entries.get("tools", [])),
                    len(entries.get("skills", [])),
                    len(entries.get("extensions", [])),
                    len(entries.get("prompts", [])),
                    len(entries.get("user_tools", [])),
                    pkg_hint,
                )

        # 安装事实写入 dist-info（机制写入、只读追加）：direct_url.json
        # 记录 PEP 610 格式的 source（path 源为绝对路径 file:// URI，
        # git 源为 remote URL + requested ref），package_name 与 installed_at
        # 同步快照。返回的 meta 与 settings 的 source 形态保持一致：
        # path 源为绝对路径（identity 对绝对路径不看 base_dir，恒正确），
        # git 源保持原 spec（identity 由 host/repo 决定，与路径无关）。
        if not dry_run:
            write_dist_info(
                final_install_path,
                source_obj,
                abs_src,
                editable=editable,
                package_name=package_name if install_python_package else "",
            )
        if source_obj.type == "path":
            recorded_source = abs_src
        else:
            recorded_source = source_obj.spec
        dist = read_dist_info(final_install_path) if not dry_run else None
        return PackageMetadata(
            name=pkg_name,
            version=manifest.version,
            description=manifest.description,
            source=recorded_source,
            install_path=final_install_path,
            author=manifest.author,
            package_name=package_name if install_python_package else "",
            editable=editable,
            installed_at=dist.installed_at if dist is not None else "",
            dependencies=deps,
            requires=list((manifest.nova.requires if manifest.nova else None) or []),
        )

    def _prepare_install(
        self,
        abs_src: str,
    ) -> tuple[List[str], Optional[str], str, bool]:
        """准备安装所需的元数据，返回依赖、Python 包名等。"""
        deps, requirements_path = resolve_package_dependencies(abs_src)
        package_name = read_package_name(abs_src) or ""
        # 自安装边界只认客观事实——**这个包是不是一个可安装的 Python 包**。
        # 作者把共享代码抽象成包结构（name + build-system）时，安装它让
        # executor/extension 能通过标准 import 引用包内模块；纯资源包或
        # executor 自包含的包没有包结构，不做自安装（executor 若 import
        # 包内模块而无包结构，运行时 ModuleNotFoundError 会清晰暴露）。
        install_python_package = bool(
            package_name and is_installable_python_package(abs_src)
        )

        return deps, requirements_path, package_name, install_python_package

    def _materialize_package(
        self,
        abs_src: str,
        install_path: Path,
        source_obj: PackageSource,
        pkg_name: str,
    ) -> str:
        """将包复制或链接到 Nova 管理目录，返回最终安装路径。"""
        if source_obj.editable:
            symlink_path = str(self.path_root / sanitize_name(pkg_name))
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

    def _ensure_npm_dependencies(
        self,
        *,
        abs_src: str,
        editable: bool,
        final_install_path: str,
        pkg_name: str,
        source_obj: Any,
    ) -> None:
        """装配前端半区 package.json 声明的 npm 依赖（安装流程第 4 阶段）。

        - 探测点为前端半区的 ``package.json``（只检测存在性，不读清单内容）：
          B 型纯 TS 包的根即前端半区（包根 ``package.json``）；A 型复合包
          探测 ``<包根>/frontend/package.json``（不做双轨，包根遗留
          ``package.json`` 不再触发）；
        - 有 ``package-lock.json`` 用 ``npm ci``（可复现），否则 ``npm install``；
        - editable 在源目录执行（与 pip -e 同语义），copy 模式在安装副本内执行；
        - npm 缺失/离线/执行失败仅警告不阻断——TS 资产降级，能力部分不受影响；
          Node 层加载时发现 node_modules 缺失会自愈补装（packages/lifecycle）。
        """
        root_dir = Path(abs_src if editable else final_install_path)
        frontend_dir = _npm_manifest_dir(root_dir)
        if not (frontend_dir / "package.json").exists():
            return
        if is_offline_mode_enabled():
            logger.warning(
                "Package '%s' 含 package.json，但处于离线模式（NOVA_OFFLINE），"
                "跳过 npm 依赖安装；TS 资产将在 Node 层加载时自动补装。",
                pkg_name,
            )
            return
        npm = shutil.which("npm")
        if npm is None:
            logger.warning(
                "Package '%s' 含 package.json，但未找到 npm——"
                "请安装 Node.js（含 npm）后重装本包，或由 Node 层加载时自动补装。",
                pkg_name,
            )
            return

        use_ci = (frontend_dir / "package-lock.json").exists()
        cmd = [
            npm,
            "ci" if use_ci else "install",
            "--omit=dev",
            "--no-audit",
            "--no-fund",
        ]
        self._emit_progress(
            "progress",
            "install",
            source_obj.spec,
            f"Installing npm dependencies for '{pkg_name}'...",
            percent=0.95,
        )
        try:
            subprocess.run(
                cmd,
                cwd=str(frontend_dir),
                check=True,
                capture_output=True,
                text=True,
                timeout=600,
                env={**os.environ, "CI": "1"},
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            logger.warning(
                "Package '%s' 的 npm 依赖安装失败（%s）——TS 资产不可用，"
                "能力部分不受影响；也可在 %s 内手动执行 npm install。",
                pkg_name,
                exc,
                frontend_dir,
            )

    def _ensure_managed_binaries(
        self, manifest: PackageManifest, pkg_name: str, source_obj: Any
    ) -> None:
        """按注册表下载安装自管理二进制（pin 版本 + sha256）。

        失败仅警告：相关工具链有纯 Python 兜底，不阻断安装。
        """
        nova = manifest.nova
        if nova is None or not nova.binary_managed_dependencies:
            return
        for name in nova.binary_managed_dependencies:

            def _progress(message: str, _name: str = name) -> None:
                self._emit_progress(
                    "progress",
                    "install",
                    source_obj.spec,
                    f"[{_name}] {message}",
                )

            path = ensure_binary(name, on_progress=_progress)
            if path is None:
                logger.warning(
                    "Package '%s' 的自管理二进制 '%s' 未能就绪"
                    "（离线/平台不支持/下载失败），相关工具将降级。\n"
                    "也可手动安装: %s",
                    pkg_name,
                    name,
                    binary_install_guidance(name),
                )

    def _verify_binary_dependencies(
        self, manifest: PackageManifest, pkg_name: str
    ) -> None:
        """安装后校验二进制依赖可解析；缺失仅警告，不阻断安装。

        - ``binary_dependencies``（wheel 条目）：经 ``resolve_binary``
          （env bin → PATH）校验，正常应已被 Phase 1 装好；
        - ``binary_system_dependencies``：仅查 PATH，缺失提示用户自行安装。
        """
        nova = manifest.nova
        if nova is None:
            return
        missing: List[str] = []
        for cmd in nova.binary_dependencies or {}:
            if resolve_binary(cmd) is None:
                missing.append(cmd)
        for cmd in nova.binary_system_dependencies or []:
            if shutil.which(cmd) is None:
                missing.append(cmd)
        if missing:
            guidance = "\n".join(
                f"  - {cmd}: {binary_install_guidance(cmd)}" for cmd in missing
            )
            logger.warning(
                "Package '%s' 的二进制依赖未全部就绪，可手动安装:\n%s",
                pkg_name,
                guidance,
            )

    def _uninstall_same_name_packages(
        self, source_obj: PackageSource, pkg_name: str
    ) -> None:
        """移除本 scope 内会与新包安装到同一目录的旧包。

        直接按目标 install_path 清理，避免递归调用 ``self.uninstall`` 带来的
        部分失败风险和状态不一致。旧副本的 Python 分发名在删除前取
        dist-info 快照（缺失时从目录内容重算），引用计数为零时一并从环境
        卸载——否则新包改名或不再是可安装 Python 包时，旧 Python 包会成为
        无人引用的孤儿。
        """
        target_path = install_path_for_source(
            source_obj, pkg_name, self.path_root, self.git_root
        )

        # 删除前取旧包的 Python 分发名（dist-info 快照优先，推导兜底），
        # 并清理 settings 里指向同一 install_path 的过时记录。
        replaced_package_names: set = set()
        if target_path.exists():
            old_dist = read_dist_info(str(target_path))
            old_name = (
                old_dist.package_name
                if old_dist is not None and old_dist.package_name
                else derive_python_package_name(str(target_path))
            )
            if old_name:
                replaced_package_names.add(old_name)

        base_dir = str(self.install_dir)
        target_resolved = str(target_path)
        for spec in self.settings_manager.get_package_sources(
            local=self.local, base_dir=base_dir
        ):
            try:
                meta = self._metadata_for_source(spec)
            except Exception:
                meta = None
            if meta is not None and str(Path(meta.install_path)) == target_resolved:
                try:
                    self.settings_manager.remove_package_source(
                        get_package_source_string(spec),
                        local=self.local,
                        base_dir=base_dir,
                        cwd=str(self.cwd),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to remove stale settings entry for '%s': %s",
                        spec,
                        exc,
                    )

        # 清理目标目录与其 dist-info。
        safe_remove(str(target_path))
        safe_remove(str(dist_info_dir(str(target_path))))

        # 旧包的 Python 包在本 scope 内无其他引用时卸载。同名 package_name 的
        # 新包会在安装流程 Phase 3 重新装入环境，此处卸载不会造成最终缺失。
        for old_name in replaced_package_names:
            if self.count_package_name_references(old_name) == 0:
                uninstall_package(old_name)

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
        self._ensure_install_dirs()

        # 消歧规则与 uninstall/info 一致：包名优先，名字查不到才按 source。
        if not isinstance(name_or_source, dict):
            spec = self.find_spec_by_name(name_or_source)
            if spec is not None:
                return self.install_and_persist(spec)

        if isinstance(name_or_source, dict) or looks_like_source(
            get_package_source_string(name_or_source)
        ):
            return self._update_by_source(name_or_source)

        raise ValueError(f"Package '{name_or_source}' is not installed.")

    def _update_by_source(self, source: PackageSourceSpec) -> PackageMetadata:
        source_str, _, _ = parse_package_source_spec(source)
        identity = get_package_identity(source, base_dir=str(self.install_dir))

        # settings 是 filters/editable 的唯一权威记录点（list() 的
        # PackageMetadata.source 是纯字符串，filters/editable 拆在独立字段，
        # 不能用它重建 spec）。按 identity 在 settings 中找原 spec；找不到
        # （磁盘-only 包）则按新 source 处理，editable 默认 False。
        old_spec = self._find_settings_spec_by_identity(identity)
        if old_spec is None:
            spec: PackageSourceSpec = source
        else:
            # 用新 source 覆盖，同时继承旧 spec 的 editable/filters。
            spec = merge_package_source_specs(old_spec, {"source": source_str})

        return self.install_and_persist(spec)

    def _find_settings_spec_by_identity(
        self, identity: str
    ) -> Optional[PackageSourceSpec]:
        """在本 scope 的 settings 中按 package identity 找原始 source spec。"""
        base_dir = str(self.install_dir)
        for spec in self.settings_manager.get_package_sources(
            local=self.local, base_dir=base_dir
        ):
            try:
                if get_package_identity(spec, base_dir=base_dir) == identity:
                    return spec
            except Exception:
                continue
        return None

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
        self._emit_progress(
            "start",
            "remove",
            name_or_source,
            f"Removing '{name_or_source}'...",
            percent=0.05,
        )
        try:
            # 消歧规则：包名优先。裸名称恰好与 cwd 下某目录同名时
            # （looks_like_source 会判 True），已安装包的名字应优先命中，
            # 避免同名目录劫持按名卸载；名字查不到才按 source 处理。
            meta = self.find_by_name(name_or_source)
            if meta is None and looks_like_source(name_or_source):
                result = self._uninstall_by_source(
                    name_or_source,
                    uninstall_python_package=uninstall_python_package,
                )
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

            # 删除 sibling dist-info 目录。
            safe_remove(str(dist_info_dir(meta.install_path)))

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
        for install_dir in scan_installed_package_dirs(self.path_root, self.git_root):
            dist = read_dist_info(str(install_dir))
            name = (
                dist.package_name
                if dist is not None and dist.package_name
                else derive_python_package_name(str(install_dir))
            )
            if name == package_name:
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

    def _uninstall_by_source(
        self, source: str, *, uninstall_python_package: bool = True
    ) -> bool:
        identity = get_package_identity(source, base_dir=str(self.install_dir))
        for pkg in self.list():
            if (
                get_package_identity(pkg.source, base_dir=str(self.install_dir))
                == identity
            ):
                # 透传 uninstall_python_package 标志：跨 scope 卸载流程依赖
                # 它推迟 Python 包卸载到引用计数统计之后，不能在这里丢失。
                return self.uninstall(
                    pkg.name, uninstall_python_package=uninstall_python_package
                )
        return False

    # ------------------------------------------------------------------
    # List / info / validate
    # ------------------------------------------------------------------
    def list(self) -> List[PackageMetadata]:
        """Return installed packages.

        settings 是唯一 source 记录点；安装事实（name/version/editable/
        package_name）全部从副本内容推导。先按 settings 推导（source 与
        filters 权威），再扫描安装目录兜底（settings 条目丢失的包仍能被
        列出与卸载）。两侧按 install_path 对齐去重。
        """
        results: List[PackageMetadata] = []
        seen_paths: set = set()
        base_dir = str(self.install_dir)

        # 1. settings 中记录的源（对 source spec 和 filters 具有权威性）。
        for spec in self.settings_manager.get_package_sources(
            local=self.local, base_dir=base_dir
        ):
            try:
                meta = self._metadata_for_source(spec)
            except Exception as exc:
                logger.warning("Failed to resolve installed package %s: %s", spec, exc)
                continue
            if meta is None:
                continue
            key = str(Path(meta.install_path))
            if key in seen_paths:
                continue
            seen_paths.add(key)
            results.append(meta)

        # 2. 扫描安装目录兜底：settings 条目被删除（或从未写入）的包。
        for install_dir in scan_installed_package_dirs(self.path_root, self.git_root):
            key = str(install_dir)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            try:
                results.append(derive_package_metadata(install_dir))
            except Exception as exc:
                logger.warning(
                    "Failed to derive metadata for installed package %s: %s",
                    install_dir,
                    exc,
                )

        return results

    def list_with_resources(self) -> Dict[str, PackageView]:
        """Return a view of packages with their agents, tools, skills and extensions.

        The returned dictionary is keyed by package identity (source-based) rather
        than display name, so two packages with the same ``manifest.name`` do not
        overwrite each other.
        """
        result: Dict[str, PackageView] = {}
        for pkg in self.list():
            identity = metadata_dedup_key(pkg, str(self.install_dir))
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
                        name=basename(p),
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
                install_path=pkg.install_path,
                scope="project" if self.local else "user",
                agents=_resource_metadata(entries.get("agents", []), "agent"),
                tools=_resource_metadata(entries.get("tools", []), "tool"),
                skills=_resource_metadata(entries.get("skills", []), "skill"),
                extensions=_resource_metadata(
                    entries.get("extensions", []), "extension"
                ),
                prompts=_resource_metadata(entries.get("prompts", []), "prompt"),
                user_tools=_resource_metadata(
                    entries.get("user_tools", []), "user_tool"
                ),
                personas=_resource_metadata(entries.get("personas", []), "persona"),
            )

        return result

    def info(self, name_or_source: str) -> Optional[PackageMetadata]:
        """Get metadata for a single installed package by name or source spec.

        包名优先于 source 判定（与 uninstall 的消歧规则一致）。
        """
        meta = self.find_by_name(name_or_source)
        if meta is not None:
            return meta
        if looks_like_source(name_or_source):
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

    def validate(self, source: str) -> List[str]:
        """Validate a package directory and return a list of issues (empty if OK).

        git 源会以 ``update=True`` 解析——``nova-pkg validate <git源>`` 是
        用户安装前的显式检查动作，允许 clone 到安装缓存（与 install 同一
        缓存，后续安装直接复用）。
        """
        source_obj = parse_source(source)
        try:
            local_dir = self.source_resolver.resolve(source_obj, update=True)
        except ValueError as exc:
            return [str(exc)]

        entries = collect_all_package_entries(local_dir)
        has_any = any(entries.values())

        # B 型纯 TS 包：无能力类目，以包根 package.json 为身份证 + tui/ 宿主段。
        # 合法条件：package.json 可解析且 tui/ 目录存在。
        if not has_any:
            pkg_json = Path(local_dir) / "package.json"
            tui_dir = Path(local_dir) / "tui"
            if pkg_json.exists() and tui_dir.is_dir():
                if load_package_json(str(pkg_json)) is None:
                    return [f"Invalid package.json: {pkg_json}"]
                return []
            return [
                "Package must declare agents, tools, skills, extensions, prompts, or user_tools in manifest, "
                "or contain agents/ / tools/ / skills/ / extensions/ / prompts/ / user_tools/ directories, "
                "or be a pure-TS package (package.json + tui/ directory)"
            ]

        issues: List[str] = []
        for src_path in entries.get("agents", []):
            if not is_agent_file(src_path):
                issues.append(f"Not a valid agent: {src_path}")
        for src_path in entries.get("tools", []):
            if not is_tool_dir(src_path):
                issues.append(f"Not a valid tool: {src_path}")
        for src_path in entries.get("skills", []):
            if not is_skill_path(src_path):
                issues.append(f"Not a valid skill: {src_path}")
        for src_path in entries.get("extensions", []):
            if not is_extension_path(src_path):
                issues.append(f"Not a valid extension: {src_path}")
        for src_path in entries.get("user_tools", []):
            if not is_user_tool_dir(src_path):
                issues.append(f"Not a valid user tool: {src_path}")

        return issues

    def find_spec_by_name(self, name: str) -> Optional[PackageSourceSpec]:
        """Find the original source spec for an installed package by name.

        settings 是 source 的唯一记录点；settings 条目丢失的包（磁盘-only）
        没有可返回的 spec——按名匹配只覆盖 settings 分支。
        """
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
        return None

    def find_by_name(self, name: str) -> Optional[PackageMetadata]:
        """Find a single installed package by name."""
        matches = [pkg for pkg in self.list() if pkg.name == name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousPackageNameError(name)
        return None

    def _metadata_for_source(
        self, source: PackageSourceSpec
    ) -> Optional[PackageMetadata]:
        """从 settings 的 source spec 推导安装事实。

        git 源的 install path 由 host/repo 直接确定；path 源先从原源读
        manifest 得包名再算 install path，原源不可用时按 source basename
        猜测（兜底交给磁盘扫描分支）。副本不存在（未安装）时返回 None——
        不会触发网络 clone。
        """
        source_str = get_package_source_string(source)
        source_obj = parse_source(source_str)
        filtered, filters = self._filter_info(source)

        if source_obj.type == "git":
            install_path = install_path_for_source(
                source_obj, "", self.path_root, self.git_root
            )
            if not install_path.exists():
                return None
        else:
            pkg_name: Optional[str] = None
            try:
                resolved_dir = self.source_resolver.resolve(source_obj)
                manifest = read_manifest(resolved_dir)
                pkg_name = manifest.name or basename(resolved_dir)
            except Exception:
                # 原源不可用（可能已删除）：按 source basename 猜测安装目录名。
                pkg_name = sanitize_name(basename(source_obj.path or ""))
            install_path = install_path_for_source(
                source_obj, pkg_name, self.path_root, self.git_root
            )
            if not install_path.exists():
                return None

        _, editable_flag, _ = parse_package_source_spec(source)
        meta = derive_package_metadata(
            install_path,
            source=source_str,
            editable=True if editable_flag else None,
        )
        meta.filtered = filtered
        meta.filters = filters
        return meta

    def _filter_info(self, source: PackageSourceSpec) -> tuple[bool, "PackageFilter"]:
        """从 source spec 中提取是否带过滤器以及过滤器内容。"""
        _, _, filters = parse_package_source_spec(source)
        filtered = isinstance(source, dict) and any(
            getattr(filters, field) is not None
            for field in (
                "extensions",
                "skills",
                "prompts",
                "tools",
                "agents",
            )
        )
        return filtered, filters


__all__ = ["PackageInstaller"]
