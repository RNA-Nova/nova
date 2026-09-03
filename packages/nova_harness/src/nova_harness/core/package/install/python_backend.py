"""Python dependency/package installation backend.

All installs unconditionally target the same Python environment that Nova itself
is running in. User/project scope only affects where Nova keeps its own resource
directories; it does NOT create or select separate Python environments.

Priority:
1. uv, when available.
2. plain ``python -m pip`` as the universal fallback.
"""

import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Protocol

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


def get_backend() -> PackageBackend:
    """Return the best available installer backend for Nova's Python env."""
    uv = find_uv()
    if uv:
        return UvBackend(uv)
    return PipBackend()


def install_dependencies(
    dependencies: List[str],
    requirements_path: Optional[str] = None,
    dry_run: bool = False,
) -> str:
    """Install dependencies into Nova's Python environment.

    Args:
        dependencies: List of pip-style dependency specs.
        requirements_path: Optional path to a ``requirements.txt`` file.
        dry_run: If True, simulate the install and return the output.

    Returns:
        Combined command output when ``dry_run`` is True; empty string otherwise.
    """
    if not dependencies and not requirements_path:
        return ""

    backend = get_backend()
    return backend.install(
        list(dependencies),
        requirements_path=requirements_path,
        dry_run=dry_run,
    )


def check_dependency_conflicts(
    dependencies: List[str],
    requirements_path: Optional[str] = None,
) -> str:
    """Dry-run install dependencies and return the output.

    Raises ``subprocess.CalledProcessError`` on conflict.
    """
    return install_dependencies(
        dependencies,
        requirements_path=requirements_path,
        dry_run=True,
    )


def install_package(
    package_dir: str,
    dry_run: bool = False,
    editable: bool = False,
) -> str:
    """Install *package_dir* as a regular or editable Python package.

    Uses ``--no-deps`` because dependencies are handled separately by
    ``install_dependencies``. When *editable* is True, installs with ``-e``
    so the original directory is referenced in place.
    """
    backend = get_backend()
    return backend.install(
        [package_dir],
        editable=editable,
        dry_run=dry_run,
        no_deps=True,
    )


def uninstall_package(package_name: str) -> None:
    """Uninstall a previously installed Python package.

    Errors are ignored because the package may already be removed.
    """
    backend = get_backend()
    backend.uninstall(package_name)


__all__ = [
    "find_uv",
    "get_backend",
    "install_dependencies",
    "check_dependency_conflicts",
    "install_package",
    "uninstall_package",
]
