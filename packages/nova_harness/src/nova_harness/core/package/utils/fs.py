"""Filesystem helpers for the package manager."""

import os
import shutil
from datetime import datetime, timezone
from typing import List, Optional, Set


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


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


def ensure_dir(path: str) -> str:
    """确保目录存在，不存在时创建，返回目录路径。"""
    if path:
        os.makedirs(path, exist_ok=True)
    return path


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


__all__ = [
    "copytree",
    "ensure_dir",
    "ensure_symlink_dir",
    "now_iso",
    "safe_remove",
]
