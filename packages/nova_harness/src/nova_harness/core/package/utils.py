"""包管理器的通用辅助：离线模式、文件系统操作、ignore 规则。

三类工具集中于此：

- 离线模式：``NOVA_OFFLINE`` 环境变量检测；
- 文件系统：安装时的目录复制 / 符号链接 / 安全删除；
- ignore 规则：``.gitignore`` / ``.ignore`` / ``.fdignore`` 的收集与匹配，
  供包内扫描与顶层自动发现共用。
"""

import os
import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import pathspec

# ---------------------------------------------------------------------------
# 离线模式
# ---------------------------------------------------------------------------

OFFLINE_TRUTHY = {"1", "true", "yes"}


def is_offline_mode_enabled() -> bool:
    """Return True when offline mode is enabled via ``NOVA_OFFLINE``."""
    return os.environ.get("NOVA_OFFLINE", "").lower() in OFFLINE_TRUTHY


# ---------------------------------------------------------------------------
# 文件系统操作
# ---------------------------------------------------------------------------


_IGNORED_COPY_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".pixi",
    ".venv",
    ".tox",
    ".pytest_cache",
    "build",
    "dist",
    ".eggs",
    ".mypy_cache",
    ".ruff_cache",
    ".DS_Store",
}


def _default_copy_ignore(src: str, names: List[str]) -> Set[str]:
    """默认忽略规则：跳过常见的依赖/构建/VCS 目录与缓存文件。"""
    ignored: Set[str] = set()
    for name in names:
        if name in _IGNORED_COPY_NAMES:
            ignored.add(name)
        elif name.endswith(".egg-info"):
            ignored.add(name)
        elif name.endswith(".pyc") or name.endswith(".pyo"):
            ignored.add(name)
    return ignored


def copytree(src: str, dst: str) -> None:
    """Copy a directory tree, overwriting the destination if it exists.

    默认忽略 ``.git``、``node_modules``、``__pycache__`` 等目录，避免把无关
    的构建产物或依赖复制到 Nova 管理目录。
    """
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=_default_copy_ignore, symlinks=True)


def safe_remove(path: str) -> None:
    """Remove a file, directory tree, or symlink if it exists, ignoring errors.

    使用 ``os.path.lexists`` 以正确处理指向不存在目标的 broken symlink。
    """
    if not os.path.lexists(path):
        return
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError:
        pass


def ensure_symlink_dir(target: str, link_path: str) -> None:
    """Create or refresh a directory symlink at *link_path* pointing to *target*."""
    safe_remove(link_path)
    os.symlink(os.path.abspath(target), link_path, target_is_directory=True)


# ---------------------------------------------------------------------------
# ignore 规则
# ---------------------------------------------------------------------------

IGNORE_FILE_NAMES = (".gitignore", ".ignore", ".fdignore")

# 目录遍历时统一跳过的目录/文件名（. 开头条目另有统一规则）：
# node_modules 是 JS 依赖目录；__pycache__/venv/env 是 Python 侧的
# 依赖/缓存目录对应物。包发现、ignore 收集、skills 加载共用此名单。
SKIP_ENTRY_NAMES = frozenset({"node_modules", "__pycache__", "venv", "env"})

IgnoreSpecWithPrefix = Tuple[Optional[pathspec.PathSpec], str]


def load_ignore_specs(root_directory: str) -> List[IgnoreSpecWithPrefix]:
    """递归收集 *root_directory* 下所有 ignore 文件规则。

    每个规则条目包含 ``(PathSpec, relative_prefix)``：
    - ``PathSpec`` 是从该目录 ignore 文件解析出的规则集合；
    - ``relative_prefix`` 是规则文件所在目录相对于 *root_directory* 的相对路径
      （为空字符串表示就在根目录），用于把规则中的相对路径转换为
      相对于 *root_directory* 的路径。

    收集顺序为自顶向下，和 gitignore 的嵌套语义一致。
    """
    root = Path(root_directory).resolve()
    if not root.exists() or not root.is_dir():
        return []

    specs: List[IgnoreSpecWithPrefix] = []

    def collect(current: Path, prefix: str) -> None:
        patterns: List[str] = []
        for name in IGNORE_FILE_NAMES:
            path = current / name
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8")
                    for line in content.splitlines():
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#"):
                            patterns.append(stripped)
                except (OSError, UnicodeDecodeError):
                    continue
        if patterns:
            specs.append((pathspec.PathSpec.from_lines("gitignore", patterns), prefix))

        for entry in sorted(current.iterdir(), key=lambda p: p.name):
            if entry.name.startswith(".") or entry.name in SKIP_ENTRY_NAMES:
                continue
            if entry.is_dir():
                next_prefix = f"{prefix}{entry.name}/" if prefix else f"{entry.name}/"
                collect(entry, next_prefix)

    collect(root, "")
    return specs


def is_ignored_by_specs(
    rel_path: str, is_dir: bool, specs: List[IgnoreSpecWithPrefix]
) -> bool:
    """判断相对于资源根目录的 *rel_path* 是否被任意 ignore 规则匹配。

    规则按自顶向下顺序应用，同一文件内与嵌套目录之间**后命中者覆盖先命中者**
    （gitignore 语义）——因此子级 ignore 文件里的 ``!`` 反选可以把被上层
    忽略的路径重新纳入，而不是只增不减。
    """
    target = rel_path + "/" if is_dir else rel_path
    ignored = False
    for spec, prefix in specs:
        # 只考虑可能匹配该路径的规则前缀范围
        if prefix and not (target == prefix or target.startswith(prefix)):
            continue
        relative_to_spec = target[len(prefix) :] if prefix else target
        if not relative_to_spec:
            continue
        candidates = [relative_to_spec]
        if is_dir:
            # 某些规则写成 file 而非 file/，对目录也尝试无斜杠匹配
            candidates.append(relative_to_spec.rstrip("/"))
        for pattern in spec.patterns:
            if any(pattern.match_file(candidate) for candidate in candidates):
                # gitignore 中 include=True 表示忽略，False 表示 ``!`` 反选恢复
                ignored = bool(pattern.include)
    return ignored


def iter_sorted_entries(directory: str) -> Iterable[Path]:
    """迭代目录下排序后的条目，跳过隐藏条目与依赖/缓存目录。"""
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return
    for entry in sorted(path.iterdir(), key=lambda p: p.name):
        if entry.name.startswith(".") or entry.name in SKIP_ENTRY_NAMES:
            continue
        yield entry


__all__ = [
    "IGNORE_FILE_NAMES",
    "IgnoreSpecWithPrefix",
    "OFFLINE_TRUTHY",
    "SKIP_ENTRY_NAMES",
    "copytree",
    "ensure_symlink_dir",
    "is_ignored_by_specs",
    "is_offline_mode_enabled",
    "iter_sorted_entries",
    "load_ignore_specs",
    "safe_remove",
]
