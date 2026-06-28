"""Python dependency installation / uninstallation for Nova packages.

Tries ``uv pip install`` first (fast, common in modern Python workflows), then
falls back to ``python -m pip install``. Installations target the Python
interpreter that is running Nova.
"""

import json
import os
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple


def find_uv() -> Optional[str]:
    """Return the path to the ``uv`` executable if available."""
    return shutil.which("uv")


def _read_pyproject_name(path: str) -> Optional[str]:
    """Read package name from a directory's pyproject.toml (Poetry or PEP 621)."""
    toml_path = os.path.join(path, "pyproject.toml")
    if not os.path.exists(toml_path):
        return None
    try:
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover
            import tomli as tomllib
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        poetry = data.get("tool", {}).get("poetry", {})
        if "name" in poetry:
            return poetry["name"]
        project = data.get("project", {})
        if "name" in project:
            return project["name"]
    except Exception:
        pass
    return None


def extract_package_name(spec: str) -> Optional[str]:
    """Extract the canonical package name from a pip dependency spec.

    Examples::

        "requests>=2.0"          -> "requests"
        "requests[socks]>=2.0"   -> "requests"
        "-e /path/to/pkg"        -> read pyproject.toml or basename
        "pkg @ git+..."          -> "pkg"
    """
    spec = spec.strip()
    if not spec:
        return None

    # Editable local path: -e /path/to/pkg or /path/to/pkg
    if spec.startswith("-e "):
        path = spec[3:].strip()
        abs_path = os.path.abspath(path)
        name = _read_pyproject_name(abs_path)
        if name:
            return name
        return os.path.basename(abs_path).replace("_", "-").lower()

    # pkg @ url
    if " @ " in spec:
        return spec.split(" @ ", 1)[0].strip().lower()

    # Direct path without -e
    if os.path.sep in spec and not spec.startswith(("http", "git+")):
        abs_path = os.path.abspath(spec)
        name = _read_pyproject_name(abs_path)
        if name:
            return name
        return os.path.basename(abs_path).replace("_", "-").lower()

    # Strip extras and version specifiers
    s = spec
    if "[" in s:
        s = s.split("[", 1)[0]
    for op in (
        "===",
        "==",
        "!=",
        ">=",
        "<=",
        "~=",
        ">",
        "<",
        ";",
    ):
        if op in s:
            s = s.split(op, 1)[0]
            break
    s = s.strip().lower()
    return s or None


def _python_executable(python_executable: Optional[str] = None) -> str:
    return python_executable or sys.executable


def install_dependencies(
    dependencies: List[str],
    requirements_path: Optional[str] = None,
    python_executable: Optional[str] = None,
    quiet: bool = True,
    dry_run: bool = False,
) -> str:
    """Install the given pip dependencies into the current environment.

    Args:
        dependencies: List of pip-style dependency specs (e.g. ``requests>=2.0``).
        requirements_path: Optional path to a ``requirements.txt`` file.
        python_executable: Python interpreter to target. Defaults to ``sys.executable``.
        quiet: Whether to suppress most output.
        dry_run: If True, only simulate the install and return the output.

    Returns:
        Command stdout/stderr combined text when ``dry_run`` is True; empty string otherwise.

    Raises:
        subprocess.CalledProcessError: If the install command fails.
    """
    if not dependencies and not requirements_path:
        return ""

    exe = _python_executable(python_executable)
    uv = find_uv()

    if uv:
        args = [uv, "pip", "install", "--python", exe]
        if quiet and not dry_run:
            args.append("--quiet")
        if dry_run:
            # uv 旧版本可能不支持 --dry-run，此时捕获错误并 fallback 到 pip。
            args.append("--dry-run")
        if requirements_path:
            args.extend(["--requirements", requirements_path])
        args.extend(dependencies)
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            return result.stdout + result.stderr
        except subprocess.CalledProcessError as e:
            if dry_run and "--dry-run" in str(e.stderr):
                pass
            else:
                raise
        # fallthrough to pip for dry-run if uv doesn't support it

    args = [exe, "-m", "pip", "install"]
    if quiet and not dry_run:
        args.append("--quiet")
    if dry_run:
        args.append("--dry-run")
    if requirements_path:
        args.extend(["--requirement", requirements_path])
    args.extend(dependencies)
    result = subprocess.run(args, capture_output=dry_run, text=True, check=True)
    return result.stdout + result.stderr if dry_run else ""


def uninstall_dependencies(
    dependencies: List[str],
    python_executable: Optional[str] = None,
    quiet: bool = True,
) -> None:
    """Uninstall the given pip dependencies from the current environment.

    Args:
        dependencies: List of package names to uninstall.
        python_executable: Python interpreter to target. Defaults to ``sys.executable``.
        quiet: Whether to suppress most output.

    Raises:
        subprocess.CalledProcessError: If the uninstall command fails.
    """
    if not dependencies:
        return

    exe = _python_executable(python_executable)
    uv = find_uv()

    if uv:
        args = [uv, "pip", "uninstall", "--python", exe, "-y"]
        if quiet:
            args.append("--quiet")
        args.extend(dependencies)
        try:
            subprocess.run(args, check=True)
            return
        except subprocess.CalledProcessError:
            pass

    args = [exe, "-m", "pip", "uninstall", "-y"]
    if quiet:
        args.append("--quiet")
    args.extend(dependencies)
    subprocess.run(args, check=True)


def list_installed_packages(python_executable: Optional[str] = None) -> List[dict]:
    """Return installed pip packages as a list of dicts with ``name`` and ``version``."""
    exe = _python_executable(python_executable)
    try:
        result = subprocess.run(
            [exe, "-m", "pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except Exception:
        return []


def check_dependency_conflicts(
    dependencies: List[str],
    requirements_path: Optional[str] = None,
    python_executable: Optional[str] = None,
) -> Tuple[bool, str]:
    """Dry-run install dependencies and report conflicts.

    Returns:
        ``(ok, output)`` where ``ok`` is True if no conflicts were detected.
    """
    try:
        output = install_dependencies(
            dependencies,
            requirements_path=requirements_path,
            python_executable=python_executable,
            quiet=True,
            dry_run=True,
        )
        return True, output
    except subprocess.CalledProcessError as e:
        return False, e.stdout + e.stderr


__all__ = [
    "install_dependencies",
    "uninstall_dependencies",
    "list_installed_packages",
    "check_dependency_conflicts",
    "extract_package_name",
    "find_uv",
]
