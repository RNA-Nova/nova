"""Python dependency/package installation backend.

All installs unconditionally target the same Python environment that Nova itself
is running in. User/project scope only affects where Nova keeps its own resource
directories; it does NOT create or select separate Python environments.

Priority:
1. uv, when available.
2. plain ``python -m pip`` as the universal fallback.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from nova_harness.core.config.defaults import PACKAGES_DIR_NAME
from nova_harness.core.utils.output_guard import is_stdout_taken_over


class PackageBackend(Protocol):
    """Abstract installer backend for Python packages and dependencies."""

    def install(
        self,
        targets: List[str],
        *,
        editable: bool = False,
        dry_run: bool = False,
        no_deps: bool = False,
        requirements_path: Optional[str] = None,
    ) -> str:
        """Install *targets* and return combined stdout/stderr in dry-run mode."""
        ...

    def uninstall(self, package_name: str) -> None:
        """Uninstall a package, ignoring errors if it is already removed."""
        ...


def find_uv() -> Optional[str]:
    """Return the path to the ``uv`` executable if available."""
    return shutil.which("uv")


def _stdio_kwargs(*, capture_output: bool = False) -> Dict[str, Any]:
    """Return subprocess stdio kwargs aligned with output-guard state.

    When Nova's stdout is taken over by the RPC output guard, child processes
    must not write to stdout (which belongs to the JSON-RPC protocol). In that
    case we redirect child stdout/stderr to Nova's stderr and close stdin.
    Otherwise we let the child inherit Nova's stdio for progress visibility.
    """
    if capture_output:
        return {"capture_output": True, "text": True}
    if is_stdout_taken_over():
        return {"stdin": subprocess.DEVNULL, "stdout": sys.stderr, "stderr": sys.stderr}
    return {}


def _expand_targets(targets: List[str]) -> List[str]:
    """Expand targets, splitting ``-e <path>`` strings into two argv entries.

    Poetry path dependencies with ``develop = true`` are emitted as ``-e <path>``
    strings by ``read_pyproject_dependencies``. pip/uv need them as separate
    ``-e`` and ``<path>`` arguments.
    """
    expanded: List[str] = []
    for target in targets:
        if target.startswith("-e "):
            expanded.extend(["-e", target[3:].strip()])
        else:
            expanded.append(target)
    return expanded


class UvBackend:
    """Use ``uv pip`` for fast installs into Nova's Python environment."""

    def __init__(self, uv: str) -> None:
        self.uv = uv
        self.python = sys.executable

    def install(
        self,
        targets: List[str],
        *,
        editable: bool = False,
        dry_run: bool = False,
        no_deps: bool = False,
        requirements_path: Optional[str] = None,
    ) -> str:
        cmd = [self.uv, "pip", "install", "--python", self.python]
        if dry_run:
            cmd.append("--dry-run")
        if no_deps:
            cmd.append("--no-deps")
        if requirements_path:
            cmd.extend(["-r", requirements_path])
        if editable:
            cmd.append("-e")
        cmd.extend(_expand_targets(targets))
        result = subprocess.run(
            cmd, check=True, **_stdio_kwargs(capture_output=dry_run)
        )
        return (result.stdout + result.stderr) if dry_run else ""

    def uninstall(self, package_name: str) -> None:
        try:
            subprocess.run(
                [self.uv, "pip", "uninstall", "--python", self.python, package_name],
                check=True,
                **_stdio_kwargs(),
            )
        except subprocess.CalledProcessError:
            pass


class PipBackend:
    """Fallback to ``python -m pip`` using Nova's Python interpreter."""

    def __init__(self) -> None:
        self.python = sys.executable

    def install(
        self,
        targets: List[str],
        *,
        editable: bool = False,
        dry_run: bool = False,
        no_deps: bool = False,
        requirements_path: Optional[str] = None,
    ) -> str:
        cmd = [self.python, "-m", "pip", "install"]
        if dry_run:
            cmd.append("--dry-run")
        if no_deps:
            cmd.append("--no-deps")
        if requirements_path:
            cmd.extend(["-r", requirements_path])
        if editable:
            cmd.append("-e")
        cmd.extend(_expand_targets(targets))
        result = subprocess.run(
            cmd, check=True, **_stdio_kwargs(capture_output=dry_run)
        )
        return (result.stdout + result.stderr) if dry_run else ""

    def uninstall(self, package_name: str) -> None:
        try:
            subprocess.run(
                [self.python, "-m", "pip", "uninstall", "-y", package_name],
                check=True,
                **_stdio_kwargs(),
            )
        except subprocess.CalledProcessError:
            pass


class NoPipHostError(RuntimeError):
    """冻结形态下找不到可用的 pip 宿主（系统 python3 + pip，大版本对齐）。"""


def find_host_python() -> Optional[str]:
    """探测 pip 宿主解释器（冻结形态安装第三方依赖用）。

    链：``NOVA_PYTHON`` 环境变量 > PATH 的 python3。宿主必须能跑
    ``python -m pip`` 且大版本与当前运行时一致（编译型 wheel 按
    Python 大版本构建——3.12 的运行时装 cp312 的产物）。
    """

    def _usable(python: str) -> bool:
        try:
            version = subprocess.run(
                [
                    python,
                    "-c",
                    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if version != f"{sys.version_info.major}.{sys.version_info.minor}":
                return False
            subprocess.run(
                [python, "-m", "pip", "--version"],
                check=True,
                capture_output=True,
            )
            return True
        except (OSError, subprocess.CalledProcessError):
            return False

    env_python = os.environ.get("NOVA_PYTHON")
    if env_python and _usable(env_python):
        return env_python
    system_python = shutil.which("python3")
    if system_python and _usable(system_python):
        return system_python
    return None


class FrozenSiteBackend:
    """冻结形态的依赖安装后端：``pip install --target <…>/packages/.site/``。

    依赖装进 Nova 用户目录的 ``.site/``（不归任何环境管理器），装配时由
    runtime_paths 挂进内嵌解释器的 sys.path。包的"自安装"（pip -e）在冻结
    形态无意义（挂载替代），editable=True 的调用静默跳过。
    """

    def __init__(self, site_dir: Path) -> None:
        self.site_dir = site_dir

    def _require_host(self) -> str:
        host = find_host_python()
        if host is None:
            raise NoPipHostError(
                "该包带第三方 Python 依赖，但本机没有可用的 Python "
                f"{sys.version_info.major}.{sys.version_info.minor} + pip 宿主"
                "（冻结二进制不内嵌 pip）。安装系统 Python 后重试，"
                "或用 NOVA_PYTHON 显式指定解释器。"
            )
        return host

    def install(
        self,
        targets: List[str],
        *,
        editable: bool = False,
        dry_run: bool = False,
        no_deps: bool = False,
        requirements_path: Optional[str] = None,
    ) -> str:
        # 包自安装（pip -e）在冻结形态由 sys.path 挂载替代——零动作
        if editable:
            return ""
        host = self._require_host()
        cmd = [host, "-m", "pip", "install", "--target", str(self.site_dir)]
        if dry_run:
            cmd.append("--dry-run")
        if no_deps:
            cmd.append("--no-deps")
        if requirements_path:
            cmd.extend(["-r", requirements_path])
        cmd.extend(_expand_targets(targets))
        result = subprocess.run(
            cmd, check=True, **_stdio_kwargs(capture_output=dry_run)
        )
        return (result.stdout + result.stderr) if dry_run else ""

    def uninstall(self, package_name: str) -> None:
        """从 .site 删除该发行版的目录与 dist-info（共享依赖残留不清理——
        多包共用时引用计数不值当，磁盘代价可忽略）。"""
        if not self.site_dir.is_dir():
            return
        normalized = package_name.replace("-", "_").lower()
        for child in self.site_dir.iterdir():
            name = child.name.lower()
            if (
                name == normalized
                or name.startswith(f"{normalized}-")
                or (name.startswith(normalized) and name.endswith(".dist-info"))
            ):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)


def get_backend(install_dir: Optional[str] = None) -> PackageBackend:
    """Return the best available installer backend for Nova's Python env.

    冻结形态（PyInstaller 无环境可写）：返回 FrozenSiteBackend——依赖装到
    ``<install_dir>/packages/.site/``（pip --target，宿主 python 经
    ``find_host_python()`` 探测），包装配目录挂载由 runtime_paths 负责。
    """
    if getattr(sys, "frozen", False):
        from nova_harness.core.config.defaults import get_agent_dir

        base = Path(install_dir) if install_dir else Path(get_agent_dir())
        return FrozenSiteBackend(base / PACKAGES_DIR_NAME / ".site")
    uv = find_uv()
    if uv:
        return UvBackend(uv)
    return PipBackend()


def install_dependencies(
    dependencies: List[str],
    requirements_path: Optional[str] = None,
    dry_run: bool = False,
    install_dir: Optional[str] = None,
) -> str:
    """Install dependencies into Nova's Python environment.

    冻结形态（无环境可写）落到 ``<install_dir>/packages/.site/``
    （pip --target，宿主 python 经 find_host_python 探测）。

    Args:
        dependencies: List of pip-style dependency specs.
        requirements_path: Optional path to a ``requirements.txt`` file.
        dry_run: If True, simulate the install and return the output.
        install_dir: 冻结形态的落点基准（包存储根的上级；None 取 user 级默认）。

    Returns:
        Combined command output when ``dry_run`` is True; empty string otherwise.
    """
    if not dependencies and not requirements_path:
        return ""

    backend = get_backend(install_dir)
    return backend.install(
        list(dependencies),
        requirements_path=requirements_path,
        dry_run=dry_run,
    )


def check_dependency_conflicts(
    dependencies: List[str],
    requirements_path: Optional[str] = None,
    install_dir: Optional[str] = None,
) -> str:
    """Dry-run install dependencies and return the output.

    Raises ``subprocess.CalledProcessError`` on conflict.
    """
    return install_dependencies(
        dependencies,
        requirements_path=requirements_path,
        dry_run=True,
        install_dir=install_dir,
    )


def install_package(
    package_dir: str,
    dry_run: bool = False,
    editable: bool = False,
    install_dir: Optional[str] = None,
) -> str:
    """Install *package_dir* as a regular or editable Python package.

    Uses ``--no-deps`` because dependencies are handled separately by
    ``install_dependencies``. When *editable* is True, installs with ``-e``
    so the original directory is referenced in place（冻结形态由
    FrozenSiteBackend 静默跳过——sys.path 挂载替代）。
    """
    backend = get_backend(install_dir)
    return backend.install(
        [package_dir],
        editable=editable,
        dry_run=dry_run,
        no_deps=True,
    )


def uninstall_package(package_name: str, install_dir: Optional[str] = None) -> None:
    """Uninstall a previously installed Python package.

    Errors are ignored because the package may already be removed.
    冻结形态从 .site 删该发行版的目录与 dist-info（共享依赖残留不清）。
    """
    backend = get_backend(install_dir)
    backend.uninstall(package_name)


__all__ = [
    "find_uv",
    "find_host_python",
    "get_backend",
    "install_dependencies",
    "check_dependency_conflicts",
    "install_package",
    "uninstall_package",
    "NoPipHostError",
    "FrozenSiteBackend",
]
