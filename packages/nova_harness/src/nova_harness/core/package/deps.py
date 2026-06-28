"""Python dependency installation for Nova packages.

Tries ``uv pip install`` first (fast, common in modern Python workflows), then
falls back to ``python -m pip install``. Installations target the Python
interpreter that is running Nova.
"""

import shutil
import subprocess
import sys
from typing import List, Optional


def find_uv() -> Optional[str]:
    """Return the path to the ``uv`` executable if available."""
    return shutil.which("uv")


def install_dependencies(
    dependencies: List[str],
    requirements_path: Optional[str] = None,
    python_executable: Optional[str] = None,
    quiet: bool = True,
) -> None:
    """Install the given pip dependencies into the current environment.

    Args:
        dependencies: List of pip-style dependency specs (e.g. ``requests>=2.0``).
        requirements_path: Optional path to a ``requirements.txt`` file.
        python_executable: Python interpreter to target. Defaults to ``sys.executable``.
        quiet: Whether to suppress most output.

    Raises:
        subprocess.CalledProcessError: If the install command fails.
    """
    if not dependencies and not requirements_path:
        return

    exe = python_executable or sys.executable
    uv = find_uv()

    if uv:
        args = [uv, "pip", "install", "--python", exe]
        if quiet:
            args.append("--quiet")
        if requirements_path:
            args.extend(["--requirements", requirements_path])
        args.extend(dependencies)
        subprocess.run(args, check=True)
        return

    args = [exe, "-m", "pip", "install"]
    if quiet:
        args.append("--quiet")
    if requirements_path:
        args.extend(["--requirement", requirements_path])
    args.extend(dependencies)
    subprocess.run(args, check=True)


__all__ = ["install_dependencies", "find_uv"]
