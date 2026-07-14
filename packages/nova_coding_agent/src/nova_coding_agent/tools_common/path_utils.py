"""路径解析与校验辅助函数。"""

import os
from typing import Optional


def resolve_path(path: str, cwd: Optional[str] = None) -> str:
    """把相对路径解析为绝对路径（默认以当前工作目录为基准）。"""
    if not path:
        return ""
    if cwd is None:
        cwd = os.getcwd()
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(cwd, path))


def is_path_traversal(path: str) -> bool:
    """检查路径是否包含 ``..`` 或指向系统根目录之外。"""
    if not path:
        return False
    normalized = os.path.normpath(path)
    parts = normalized.split(os.sep)
    return ".." in parts
