"""Git 仓库相关工具函数。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def find_git_root(start_dir: str) -> Optional[Path]:
    """查找 *start_dir* 所在 git 仓库的根目录；未找到时返回 None。"""
    path = Path(start_dir).resolve()
    root = Path(os.path.abspath(os.sep))

    while True:
        if (path / ".git").exists():
            return path
        if path == root:
            return None
        parent = path.parent
        if parent == path:
            return None
        path = parent


__all__ = ["find_git_root"]
