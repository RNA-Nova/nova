"""Unified ``pyproject.toml`` reading for Nova packages.

Handles:
- Loading and parsing ``pyproject.toml``
- Reading package metadata (name, version, description, authors) from Poetry or PEP 621
- Reading the ``[tool.nova]`` manifest section
- Reading Python dependencies from Poetry or PEP 621
- Reading optional ``requirements.txt``
"""

import os
import warnings
from typing import Any, Dict, List, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from nova_harness.core.types.package_manager import NovaManifest, PackageManifest


def load_pyproject(path: str) -> Optional[Dict[str, Any]]:
    """Load and parse a ``pyproject.toml`` file."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return None


def _read_poetry_authors(data: Dict[str, Any]) -> List[str]:
    """Read the Poetry authors list and return it as strings."""
    poetry = data.get("tool", {}).get("poetry", {})
    authors = poetry.get("authors")
    if isinstance(authors, list):
        return [str(a) for a in authors]
    return []


def _first_author(data: Dict[str, Any]) -> str:
    """Return the first author from Poetry or PEP 621 authors."""
    authors = _read_poetry_authors(data)
    if authors:
        return authors[0]

    project = data.get("project")
    if isinstance(project, dict):
        authors = project.get("authors")
        if isinstance(authors, list) and authors:
            first = authors[0]
            if isinstance(first, dict):
                return str(first.get("name", ""))
            return str(first)

    return ""


def _read_name(data: Dict[str, Any]) -> Optional[str]:
    """Read the package name from Poetry or PEP 621."""
    poetry = data.get("tool", {}).get("poetry", {})
    name = poetry.get("name")
    if name:
        return str(name)

    project = data.get("project")
    if isinstance(project, dict):
        name = project.get("name")
        if name:
            return str(name)

    return None


def _read_version(data: Dict[str, Any]) -> str:
    """Read the version from Poetry or PEP 621."""
    poetry = data.get("tool", {}).get("poetry", {})
    version = poetry.get("version")
    if version:
        return str(version)

    project = data.get("project")
    if isinstance(project, dict):
        version = project.get("version")
        if version:
            return str(version)

    return "0.0.0"


def _read_description(data: Dict[str, Any]) -> str:
    """Read the description from Poetry or PEP 621."""
    poetry = data.get("tool", {}).get("poetry", {})
    description = poetry.get("description")
    if description:
        return str(description)

    project = data.get("project")
    if isinstance(project, dict):
        description = project.get("description")
        if description:
            return str(description)

    return ""


def read_manifest(package_dir: str) -> PackageManifest:
    """Read and normalize a Nova package manifest from *package_dir*.

    Reads ``pyproject.toml`` and extracts Nova-specific configuration from the
    ``[tool.nova]`` section. Metadata comes from ``[tool.poetry]`` (preferred) or
    PEP 621 ``[project]``.
    """
    data = load_pyproject(os.path.join(package_dir, "pyproject.toml")) or {}

    nova_data = data.get("tool", {}).get("nova")
    if nova_data is None:
        return PackageManifest(
            name=_read_name(data),
            version=_read_version(data),
            description=_read_description(data),
            author=_first_author(data),
        )

    return PackageManifest(
        name=_read_name(data),
        version=_read_version(data),
        description=_read_description(data),
        author=_first_author(data),
        nova=NovaManifest.model_validate(nova_data),
    )


def read_requirements(package_dir: str) -> List[str]:
    """Read ``requirements.txt`` lines, ignoring blanks and comments."""
    req_path = os.path.join(package_dir, "requirements.txt")
    if not os.path.exists(req_path):
        return []
    try:
        with open(req_path, "r", encoding="utf-8") as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    except Exception:
        return []


def resolve_extension_entries(package_dir: str) -> Optional[List[str]]:
    """读取 *package_dir* 下 ``pyproject.toml`` 的 ``[tool.nova.extensions]`` 入口。

    返回值语义：
    - ``None``：没有 ``pyproject.toml`` 或未声明 ``extensions``；
    - ``[]``：显式声明为空列表（禁用该目录的扩展发现）；
    - ``List[str]``：显式声明的相对路径列表。
    """
    data = load_pyproject(os.path.join(package_dir, "pyproject.toml"))
    if data is None:
        return None

    nova = data.get("tool", {}).get("nova")
    if not isinstance(nova, dict):
        return None

    extensions = nova.get("extensions")
    if extensions is None:
        return None
    if not isinstance(extensions, list):
        return None

    return [str(entry) for entry in extensions]


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
            raw_path = spec["path"]
            abs_path = os.path.abspath(os.path.join(package_dir, raw_path))
            if raw_path.startswith("..") or not abs_path.startswith(
                os.path.abspath(package_dir)
            ):
                warnings.warn(
                    f"Path dependency resolves outside the bundle root: {raw_path} -> {abs_path}. "
                    "Relative path dependencies often break when installing from a remote source. "
                    "Consider publishing the dependency to a package index or using a git dependency.",
                    UserWarning,
                    stacklevel=3,
                )
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
    """Read Poetry ``[tool.poetry.dependencies]`` as pip-installable specs."""
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
    """Read PEP 621 ``[project.dependencies]`` as pip-installable specs."""
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
    data = load_pyproject(path)
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


def read_pyproject_name(package_dir: str) -> Optional[str]:
    """Return the declared package name from ``pyproject.toml`` if present.

    Recognizes Poetry ``[tool.poetry.name]`` and PEP 621 ``[project.name]``.
    """
    data = load_pyproject(os.path.join(package_dir, "pyproject.toml"))
    if data is None:
        return None

    poetry = data.get("tool", {}).get("poetry")
    if isinstance(poetry, dict):
        name = poetry.get("name")
        if name:
            return str(name)

    project = data.get("project")
    if isinstance(project, dict):
        name = project.get("name")
        if name:
            return str(name)

    return None


def has_pyproject_dependencies(package_dir: str) -> bool:
    """Check whether ``pyproject.toml`` declares installable dependencies."""
    return bool(read_pyproject_dependencies(package_dir))


def resolve_package_dependencies(
    package_dir: str,
) -> tuple[List[str], Optional[str]]:
    """解析 Python 依赖规格与可选的 requirements.txt 路径。"""
    deps: List[str] = read_pyproject_dependencies(package_dir)

    requirements_path = os.path.join(package_dir, "requirements.txt")
    has_requirements = os.path.exists(requirements_path)
    if has_requirements:
        deps.extend(read_requirements(package_dir))

    return deps, requirements_path if has_requirements else None


__all__ = [
    "load_pyproject",
    "read_manifest",
    "read_pyproject_dependencies",
    "read_pyproject_name",
    "read_requirements",
    "has_pyproject_dependencies",
    "resolve_extension_entries",
    "resolve_package_dependencies",
]
