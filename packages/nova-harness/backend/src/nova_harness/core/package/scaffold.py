"""Scaffold ``[tool.nova]`` in ``pyproject.toml`` for a Nova package directory.

根据标准目录结构自动扫描 ``agents/``、``tools/``、``skills/``、``extensions/``、``prompts/``、
``user_tools/``、``personas/``，生成对应的资源清单并写入 ``pyproject.toml`` 的 ``[tool.nova]`` 段。
"""

import os
import re
from typing import Dict, List, Optional

from nova_harness.core.config.defaults import (
    AGENTS_DIR_NAME,
    EXTENSIONS_DIR_NAME,
    PERSONAS_DIR_NAME,
    PROMPTS_DIR_NAME,
    SKILLS_DIR_NAME,
    TOOLS_DIR_NAME,
    USER_TOOLS_DIR_NAME,
)
from nova_harness.core.package.validation import (
    is_agent_file,
    is_extension_path,
    is_skill_path,
    is_tool_dir,
    is_user_tool_dir,
)


def _is_hidden_entry(name: str) -> bool:
    """跳过隐藏文件、目录以及 Python/构建缓存目录。"""
    return name.startswith(".") or name.startswith("__") or name == "node_modules"


def discover_entries(directory: str) -> Dict[str, List[str]]:
    """扫描目录，返回各类目资源的相对路径列表。"""
    entries: Dict[str, List[str]] = {
        "agents": [],
        "tools": [],
        "skills": [],
        "extensions": [],
        "prompts": [],
        "user_tools": [],
        "personas": [],
    }

    for resource_type, subdir in [
        ("agents", AGENTS_DIR_NAME),
        ("tools", TOOLS_DIR_NAME),
        ("skills", SKILLS_DIR_NAME),
        ("extensions", EXTENSIONS_DIR_NAME),
        ("prompts", PROMPTS_DIR_NAME),
        ("user_tools", USER_TOOLS_DIR_NAME),
        ("personas", PERSONAS_DIR_NAME),
    ]:
        full = os.path.join(directory, subdir)
        if not os.path.isdir(full):
            continue
        for entry in sorted(os.listdir(full)):
            if _is_hidden_entry(entry):
                continue
            entry_path = os.path.join(full, entry)
            rel = f"./{subdir}/{entry}"

            if resource_type == "agents" and is_agent_file(entry_path):
                entries["agents"].append(rel)
            elif resource_type == "tools" and is_tool_dir(entry_path):
                entries["tools"].append(rel)
            elif resource_type == "skills" and is_skill_path(entry_path):
                entries["skills"].append(rel)
            elif resource_type == "extensions" and is_extension_path(entry_path):
                entries["extensions"].append(rel)
            elif resource_type == "user_tools" and is_user_tool_dir(entry_path):
                entries["user_tools"].append(rel)
            elif resource_type == "personas":
                # persona 条目：.md 单文件或子目录（人格分组，loader 递归展开）
                if os.path.isfile(entry_path) and entry.endswith(".md"):
                    entries["personas"].append(rel)
                elif os.path.isdir(entry_path):
                    entries["personas"].append(rel)
            elif resource_type == "prompts":
                if os.path.isfile(entry_path) and entry.endswith(".md"):
                    entries["prompts"].append(rel)

    return entries


def _toml_string_list(values: List[str]) -> str:
    """把字符串列表渲染为 TOML 数组字面量。"""
    if not values:
        return "[]"
    items = [f'"{v}"' for v in values]
    return "[\n    " + ",\n    ".join(items) + "\n]"


def _render_nova_section(
    entries: Dict[str, List[str]],
    auto_install_dependencies: bool = True,
) -> str:
    """渲染 ``[tool.nova]`` 段的文本。"""
    lines = ["[tool.nova]"]
    lines.append(f"agents = {_toml_string_list(entries['agents'])}")
    lines.append(f"tools = {_toml_string_list(entries['tools'])}")
    lines.append(f"skills = {_toml_string_list(entries['skills'])}")
    lines.append(f"extensions = {_toml_string_list(entries['extensions'])}")
    lines.append(f"prompts = {_toml_string_list(entries['prompts'])}")
    lines.append(f"user_tools = {_toml_string_list(entries['user_tools'])}")
    lines.append(f"personas = {_toml_string_list(entries['personas'])}")
    lines.append(
        f"auto_install_dependencies = {str(auto_install_dependencies).lower()}"
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _remove_existing_nova_section(content: str) -> str:
    """移除已有 ``[tool.nova]`` 段及其所有子表（如 ``[tool.nova.sub]``）。

    使用循环匹配，因为子表可能与其他段交错；每次匹配一段（从段头到下一个
    段头或文件尾），直到没有 nova 段头为止。
    """
    pattern = re.compile(
        r"\n?\[tool\.nova(?:\.[^\]]+)?\][^\n]*(?:\n(?!\[)[^\n]*)*",
        re.DOTALL,
    )
    while pattern.search(content):
        content = pattern.sub("", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip() + "\n"


def scaffold_pyproject_nova_section(
    directory: str,
    name: Optional[str] = None,
    version: str = "0.1.0",
    description: str = "",
) -> str:
    """在指定目录的 ``pyproject.toml`` 中写入/更新 ``[tool.nova]`` 段。

    若 ``pyproject.toml`` 不存在，则生成一个最小版本。
    返回生成的文件路径。
    """
    directory = os.path.abspath(directory)
    entries = discover_entries(directory)
    pkg_name = name or os.path.basename(os.path.normpath(directory))

    nova_section = _render_nova_section(entries)

    pyproject_path = os.path.join(directory, "pyproject.toml")
    if os.path.exists(pyproject_path):
        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = _remove_existing_nova_section(content)
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + nova_section
    else:
        authors = "Nova"
        content = f"""[tool.poetry]
name = "{pkg_name}"
version = "{version}"
description = "{description}"
authors = ["{authors}"]

[tool.poetry.dependencies]
python = ">=3.12,<3.14"

[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"

{nova_section}"""

    with open(pyproject_path, "w", encoding="utf-8") as f:
        f.write(content)

    return pyproject_path


__all__ = [
    "discover_entries",
    "scaffold_pyproject_nova_section",
]
