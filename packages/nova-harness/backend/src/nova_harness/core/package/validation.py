"""Validation helpers for Nova package resource directories."""

import os


def is_agent_file(path: str) -> bool:
    """agent 组合声明：单个 yaml 文件（``agents/<name>.yaml``——一文件一 agent）。"""
    return os.path.isfile(path) and path.endswith((".yaml", ".yml"))


def is_tool_dir(path: str) -> bool:
    """Check whether *path* looks like a valid tool entry.

    支持 ``<name>.py`` 单文件形态，或含 ``executor.py`` 的目录形态。
    """
    if os.path.isfile(path):
        return os.path.splitext(path)[1] == ".py"
    if os.path.isdir(path):
        return os.path.exists(os.path.join(path, "executor.py"))
    return False


def is_user_tool_dir(path: str) -> bool:
    """Check whether *path* looks like a valid user tool entry.

    支持 ``<name>.py`` 单文件形态，或含 ``executor.py`` 的目录形态。
    """
    if os.path.isfile(path):
        return os.path.splitext(path)[1] == ".py"
    if os.path.isdir(path):
        return os.path.exists(os.path.join(path, "executor.py"))
    return False


def is_extension_path(path: str) -> bool:
    """Check whether *path* looks like a valid extension entry.

    支持任意 ``.py`` 文件，或包含 ``extension.py`` / ``__init__.py`` 的目录。
    """
    if os.path.isfile(path):
        return os.path.splitext(path)[1] == ".py"
    if os.path.isdir(path):
        return os.path.exists(os.path.join(path, "extension.py")) or os.path.exists(
            os.path.join(path, "__init__.py")
        )
    return False


def is_skill_path(path: str) -> bool:
    """Check whether *path* looks like a valid skill entry.

    支持包含 ``SKILL.md`` 的目录，或根目录下的 ``SKILL.md`` 文件。
    """
    if os.path.isfile(path):
        return os.path.basename(path) == "SKILL.md"
    if os.path.isdir(path):
        return os.path.exists(os.path.join(path, "SKILL.md"))
    return False


def is_persona_dir(path: str) -> bool:
    """personas 资源条目：**目录本身即资源**（personas 根——命名根随条目走，
    递归展开与逐文件命名归 loader）；空目录也合法（零 persona）。"""
    return os.path.isdir(path)


__all__ = [
    "is_agent_file",
    "is_extension_path",
    "is_persona_dir",
    "is_skill_path",
    "is_tool_dir",
]
