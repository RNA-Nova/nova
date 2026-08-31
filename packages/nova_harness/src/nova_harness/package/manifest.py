"""Unified ``pyproject.toml`` reading for Nova packages.

Handles:
- Loading and parsing ``pyproject.toml``
- Reading package metadata (name, version, description, authors) from Poetry or PEP 621
- Reading the ``[tool.nova]`` manifest section
- Reading Python dependencies from Poetry or PEP 621
- Reading optional ``requirements.txt``
"""

import json
import os
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from nova_harness.core.types.package import NovaManifest, PackageManifest

# “path 依赖解析出包根”警告的进程级去重表（按解析后的绝对路径）。
_warned_outside_paths: set = set()


def load_pyproject(path: str) -> Optional[Dict[str, Any]]:
    """Load and parse a ``pyproject.toml`` file."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return None


def load_package_json(path: str) -> Optional[Dict[str, Any]]:
    """Load and parse a ``package.json`` file（B 型纯 TS 包的身份证）。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
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
    data = load_pyproject(os.path.join(package_dir, "pyproject.toml"))

    if data is None:
        # B 型纯 TS 包：无 pyproject.toml，以包根 package.json 为身份证。
        # 顶层 "nova" 键承载 B 型的 nova 段（当前仅 requires——包间依赖
        # 与 A 型 [tool.nova] 对齐；B 型无 Python 能力类目可声明）。
        npm = load_package_json(os.path.join(package_dir, "package.json"))
        if npm is not None:
            nova_section = npm.get("nova")
            nova_manifest = None
            if isinstance(nova_section, dict):
                nova_manifest = NovaManifest.model_validate(nova_section)
            return PackageManifest(
                name=_read_npm_name(npm),
                version=_read_npm_version(npm),
                description=str(npm.get("description") or ""),
                author=_read_npm_author(npm),
                nova=nova_manifest,
            )
        data = {}

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


def _read_npm_name(data: Dict[str, Any]) -> Optional[str]:
    """B 型包名：package.json ``name``（去掉 npm scope 前缀）。"""
    name = data.get("name")
    if not name:
        return None
    name = str(name)
    # @scope/pkg → pkg（scope 是 npm registry 命名空间，不是包名的一部分）
    if name.startswith("@") and "/" in name:
        return name.rsplit("/", 1)[-1]
    return name


def _read_npm_version(data: Dict[str, Any]) -> str:
    version = data.get("version")
    return str(version) if version else "0.0.0"


def _read_npm_author(data: Dict[str, Any]) -> str:
    author = data.get("author")
    if isinstance(author, dict):
        return str(author.get("name", ""))
    if author:
        return str(author)
    return ""


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

    Poetry 语义：
    - caret：上界为**最左侧非零分量**加一（``^1.2.3`` → ``<2``，
      ``^0.1.2`` → ``<0.2``，``^0.0.3`` → ``<0.0.4``）；全零时加一最后一个
      分量（``^0.0`` → ``<0.1``）。
    - tilde：有 minor 时 minor 加一（``~1.2.3`` → ``<1.3``），仅 major 时
      major 加一（``~1`` → ``<2``）。
    """
    ver = ver.strip()
    if ver == "*":
        return ""

    def _numeric_parts(base: str) -> List[int]:
        nums: List[int] = []
        for part in base.split("."):
            if not part.isdigit():
                break
            nums.append(int(part))
        return nums

    if ver.startswith("^"):
        base = ver[1:].strip()
        nums = _numeric_parts(base)
        if not nums:
            return f">={base}"
        first_nz = next((i for i, n in enumerate(nums) if n != 0), None)
        inc_index = first_nz if first_nz is not None else len(nums) - 1
        upper_nums = nums[: inc_index + 1]
        upper_nums[inc_index] += 1
        upper = ".".join(str(n) for n in upper_nums)
        return f">={base},<{upper}"

    if ver.startswith("~"):
        base = ver[1:].strip()
        nums = _numeric_parts(base)
        if not nums:
            return f">={base}"
        if len(nums) >= 2:
            upper = f"{nums[0]}.{nums[1] + 1}"
        else:
            upper = f"{nums[0] + 1}"
        return f">={base},<{upper}"

    return ver


def _poetry_spec(name: str, spec: Any, package_dir: str) -> Optional[str]:
    """把 Poetry 依赖条目转换成 pip 安装参数。

    - ``extras`` 会保留为 ``name[extra1,extra2]`` 后缀（version/git 形态同样适用）；
    - ``optional = true`` 的依赖是 opt-in（由消费方 extras 按需启用），
      整体安装时不带入，返回 ``None``。
    """
    if isinstance(spec, str):
        converted = _convert_poetry_version(spec)
        if not converted:
            return name
        return f"{name}{converted}"

    if isinstance(spec, dict):
        if spec.get("optional"):
            return None

        extras = spec.get("extras")
        if isinstance(extras, list) and extras:
            name = "{}[{}]".format(name, ",".join(str(e) for e in extras))

        if "path" in spec:
            raw_path = spec["path"]
            abs_path = os.path.abspath(os.path.join(package_dir, raw_path))
            if raw_path.startswith("..") or not abs_path.startswith(
                os.path.abspath(package_dir)
            ):
                # 同一解析结果每进程只警告一次（pkgList/会话启动/reload 会反复
                # 读同一 manifest——不 dedupe 会把 TUI 刷屏）
                if abs_path not in _warned_outside_paths:
                    _warned_outside_paths.add(abs_path)
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
            converted = _poetry_spec(name, spec["version"], package_dir)
            # markers 是 PEP 508 环境标记，直接透传（之前被静默丢弃，
            # 会装到平台/版本不兼容的依赖）。
            markers = spec.get("markers")
            if converted and isinstance(markers, str) and markers.strip():
                return f"{converted}; {markers.strip()}"
            return converted

        # extras-only 条目（无 version/path/git）：返回带 extras 的裸名称；
        # 无 version 的 markers/python 约束条目暂不转换。
        if "[" in name:
            return name
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


def _read_setup_cfg_name(package_dir: str) -> Optional[str]:
    """从 ``setup.cfg`` 的 ``[metadata] name`` 读取包名（静态解析，不执行代码）。"""
    import configparser

    cfg_path = os.path.join(package_dir, "setup.cfg")
    if not os.path.exists(cfg_path):
        return None
    try:
        parser = configparser.ConfigParser()
        parser.read(cfg_path, encoding="utf-8")
        if parser.has_option("metadata", "name"):
            name = parser.get("metadata", "name").strip()
            return name or None
    except Exception:
        pass
    return None


def _read_setup_py_name(package_dir: str) -> Optional[str]:
    """用 AST 静态解析 ``setup.py`` 中 ``setup(name="...")`` 的字面量包名。

    只做字面量提取（不执行 setup.py）；变量引用或动态计算的 name 抓不到，
    返回 ``None``——此类老式动态项目不在自安装支持范围内。
    """
    import ast

    setup_path = os.path.join(package_dir, "setup.py")
    if not os.path.exists(setup_path):
        return None
    try:
        with open(setup_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            func_name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else ""
            )
            if func_name != "setup":
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "name"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    return kw.value.value
    except Exception:
        pass
    return None


def read_package_name(package_dir: str) -> Optional[str]:
    """读取 *package_dir* 声明的 Python 分发名。

    按优先级依次尝试：

    1. ``pyproject.toml``（Poetry ``[tool.poetry.name]`` / PEP 621 ``[project.name]``）；
    2. ``setup.cfg`` 的 ``[metadata] name``；
    3. ``setup.py`` 中 ``setup(name="...")`` 的 AST 字面量。
    """
    name = read_pyproject_name(package_dir)
    if name:
        return name
    return _read_setup_cfg_name(package_dir) or _read_setup_py_name(package_dir)


def is_installable_python_package(package_dir: str) -> bool:
    """Check whether *package_dir* can be installed as a Python package.

    A directory is considered installable if it has a ``setup.py`` or a
    ``pyproject.toml`` declaring a build system. ``setup.cfg`` alone is
    only a config file——without ``setup.py`` to drive it, pip cannot
    install the directory, so it does not count.
    """
    pkg_path = Path(package_dir)
    if (pkg_path / "setup.py").exists():
        return True
    pyproject = pkg_path / "pyproject.toml"
    if pyproject.exists():
        try:
            with pyproject.open("rb") as f:
                data = tomllib.load(f)
            if data.get("build-system", {}).get("requires"):
                return True
        except Exception:
            pass
    return False


def has_pyproject_dependencies(package_dir: str) -> bool:
    """Check whether ``pyproject.toml`` declares installable dependencies."""
    return bool(read_pyproject_dependencies(package_dir))


def _read_setup_cfg_dependencies(package_dir: str) -> List[str]:
    """从 ``setup.cfg`` 的 ``[options] install_requires`` 读取依赖（静态解析）。

    老式 ``setup.cfg`` 项目的 ``install_requires`` 本来就是 PEP 508 格式，
    逐行透传；仅作为无 pyproject 依赖时的回退。
    """
    import configparser

    cfg_path = os.path.join(package_dir, "setup.cfg")
    if not os.path.exists(cfg_path):
        return []
    try:
        parser = configparser.ConfigParser()
        parser.read(cfg_path, encoding="utf-8")
        if not parser.has_option("options", "install_requires"):
            return []
        raw = parser.get("options", "install_requires")
        return [
            line.strip()
            for line in raw.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    except Exception:
        return []


def resolve_package_dependencies(
    package_dir: str,
) -> tuple[List[str], Optional[str]]:
    """解析 Python 依赖规格与可选的 requirements.txt 路径。

    来源优先级：``pyproject.toml``（Poetry/PEP 621）→ ``setup.cfg``
    ``install_requires``（老式项目回退）→ 追加 ``requirements.txt``。
    """
    deps: List[str] = read_pyproject_dependencies(package_dir)
    if not deps:
        deps = _read_setup_cfg_dependencies(package_dir)

    requirements_path = os.path.join(package_dir, "requirements.txt")
    has_requirements = os.path.exists(requirements_path)
    if has_requirements:
        deps.extend(read_requirements(package_dir))

    return deps, requirements_path if has_requirements else None


__all__ = [
    "load_pyproject",
    "read_manifest",
    "read_package_name",
    "read_pyproject_dependencies",
    "read_pyproject_name",
    "read_requirements",
    "has_pyproject_dependencies",
    "is_installable_python_package",
    "resolve_extension_entries",
    "resolve_package_dependencies",
]
