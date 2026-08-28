"""Validation helpers for Nova package resource directories."""

import os


def is_agent_dir(path: str) -> bool:
    """Check whether *path* looks like a valid agent config directory."""
    if not os.path.isdir(path):
        return False
    markers = [
        "agent.yaml",
        "description.md",
    ]
    for m in markers:
        if os.path.exists(os.path.join(path, m)):
            return True
    if os.path.isdir(os.path.join(path, "sections")):
        return True
    return False


def is_tool_dir(path: str) -> bool:
    """Check whether *path* looks like a valid tool package."""
    if not os.path.isdir(path):
        return False
    markers = [
        "schema.json",
        "executor.py",
    ]
    for m in markers:
        if os.path.exists(os.path.join(path, m)):
            return True
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


def is_ui_block_dir(path: str) -> bool:
    """Check whether *path* looks like a valid UI block package.

    UI block 目录应包含 ``schema.py`` 或 ``schema.json``。
    """
    if not os.path.isdir(path):
        return False
    markers = ["schema.py", "schema.json"]
    for m in markers:
        if os.path.exists(os.path.join(path, m)):
            return True
    return False


__all__ = [
    "is_agent_dir",
    "is_extension_path",
    "is_skill_path",
    "is_tool_dir",
    "is_ui_block_dir",
]
