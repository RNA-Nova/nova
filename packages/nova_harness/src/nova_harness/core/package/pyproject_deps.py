"""Read Python dependencies from a bundle's ``pyproject.toml``.

Supports Poetry ``[tool.poetry.dependencies]`` and PEP 621
``[project.dependencies]``. Path dependencies declared with
``develop = true`` are returned as editable-install specs.
"""

import os
import re
from typing import Any, Dict, List, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


def _load_pyproject(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return None


def _convert_poetry_version(ver: str) -> str:
    """Convert common Poetry version operators to PEP 440 for pip.

    仅处理 ``*``、``^``、``~``；其他 operator（>=、== 等）直接透传。
    """
    ver = ver.strip()
    if ver == "*":
        return ""

    # ^1.2.3 -> >=1.2.3,<2.0.0；^0.1.2 -> >=0.1.2,<0.2.0
    if ver.startswith("^"):
        base = ver[1:].strip()
        parts = base.split(".")
        if parts and parts[0] == "0" and len(parts) >= 2:
            upper = f"0.{int(parts[1]) + 1}.0"
        else:
            upper = f"{int(parts[0]) + 1}.0.0"
        return f">={base},<{upper}"

    # ~1.2.3 -> >=1.2.3,<1.3.0
    if ver.startswith("~"):
        base = ver[1:].strip()
        parts = base.split(".")
        if len(parts) >= 2:
            upper = f"{parts[0]}.{int(parts[1]) + 1}.0"
            return f">={base},<{upper}"
        return f">={base}"

    return ver


def _poetry_spec(name: str, spec: Any, package_dir: str) -> Optional[str]:
    """把 Poetry 依赖条目转换成 pip 安装参数。"""
    if isinstance(spec, str):
        converted = _convert_poetry_version(spec)
        if not converted:
            return name
        return f"{name}{converted}"

    if isinstance(spec, dict):
        if "path" in spec:
            abs_path = os.path.abspath(os.path.join(package_dir, spec["path"]))
            if spec.get("develop"):
                return f"-e {abs_path}"
            return abs_path

        if "git" in spec:
            url = spec["git"]
            ref = spec.get("branch") or spec.get("tag") or spec.get("rev", "")
            if ref:
                return f"{name} @ git+{url}@{ref}"
            return f"{name} @ git+{url}"

        if "version" in spec:
            return _poetry_spec(name, spec["version"], package_dir)

        # 其他形式（extras、optional 等）暂不转换。
        return None

    return None


def _read_poetry_dependencies(data: Dict[str, Any], package_dir: str) -> List[str]:
    deps: List[str] = []
    poetry = data.get("tool", {}).get("poetry", {})
    raw = poetry.get("dependencies", {})
    if not isinstance(raw, dict):
        return deps

    for name, spec in raw.items():
        # python 是 Poetry 虚拟环境约束，不是可安装依赖。
        if name.lower() == "python":
            continue
        pip_spec = _poetry_spec(name, spec, package_dir)
        if pip_spec:
            deps.append(pip_spec)
    return deps


def _read_pep621_dependencies(data: Dict[str, Any]) -> List[str]:
    project = data.get("project")
    if not isinstance(project, dict):
        return []

    deps: List[str] = []
    raw = project.get("dependencies")
    if isinstance(raw, list):
        deps.extend(str(d) for d in raw)
    return deps


def read_pyproject_dependencies(package_dir: str) -> List[str]:
    """Return pip-installable dependency specs from ``pyproject.toml``.

    优先识别 Poetry 格式 ``[tool.poetry.dependencies]``；不存在时回退到
    PEP 621 ``[project.dependencies]``。

    Path 依赖若带 ``develop = true``，会返回 ``-e /abs/path`` 形式的
    editable 安装参数。
    """
    path = os.path.join(package_dir, "pyproject.toml")
    data = _load_pyproject(path)
    if data is None:
        return []

    if "poetry" in data.get("tool", {}):
        poetry_deps = _read_poetry_dependencies(data, package_dir)
        if poetry_deps:
            return poetry_deps

    pep621_deps = _read_pep621_dependencies(data)
    if pep621_deps:
        return pep621_deps

    return []


def has_pyproject_dependencies(package_dir: str) -> bool:
    """Check whether ``pyproject.toml`` declares installable dependencies."""
    return bool(read_pyproject_dependencies(package_dir))


__all__ = ["read_pyproject_dependencies", "has_pyproject_dependencies"]
