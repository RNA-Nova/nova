"""Scaffold ``package.json`` for a Nova bundle directory.

根据标准目录结构自动扫描 ``agents/``、``tools/``、``skills/``，生成对应的
bundle 清单。
"""

import json
import os
from typing import Dict, List, Optional

from nova_harness.core.package.utils import is_agent_dir, is_tool_dir


def discover_entries(directory: str) -> Dict[str, List[str]]:
    """扫描目录，返回 agents/tools/skills 的相对路径列表。"""
    entries: Dict[str, List[str]] = {"agents": [], "tools": [], "skills": []}

    for kind, subdir in [
        ("agents", "agents"),
        ("tools", "tools"),
        ("skills", "skills"),
    ]:
        full = os.path.join(directory, subdir)
        if not os.path.isdir(full):
            continue
        for entry in sorted(os.listdir(full)):
            entry_path = os.path.join(full, entry)
            rel = f"./{subdir}/{entry}"

            if kind == "agents" and is_agent_dir(entry_path):
                entries["agents"].append(rel)
            elif kind == "tools" and is_tool_dir(entry_path):
                entries["tools"].append(rel)
            elif kind == "skills":
                if os.path.isdir(entry_path) or (
                    os.path.isfile(entry_path) and entry == "SKILL.md"
                ):
                    entries["skills"].append(rel)

    return entries


def infer_kind(entries: Dict[str, List[str]]) -> str:
    """根据发现的条目推断 package kind。"""
    total = sum(len(v) for v in entries.values())
    if total > 1:
        return "bundle"
    if entries["agents"]:
        return "agent"
    if entries["tools"]:
        return "tool"
    if entries["skills"]:
        return "skill"
    return "bundle"


def scaffold_package_json(
    directory: str,
    name: Optional[str] = None,
    version: str = "0.1.0",
    description: str = "",
) -> str:
    """在指定目录生成 ``package.json``，返回生成的文件路径。

    若文件已存在则直接覆盖。
    """
    directory = os.path.abspath(directory)
    entries = discover_entries(directory)
    kind = infer_kind(entries)
    pkg_name = name or os.path.basename(os.path.normpath(directory))

    manifest: Dict[str, object] = {
        "name": pkg_name,
        "version": version,
        "description": description,
        "kind": kind,
        "dependencies": [],
        "nova": {
            "agents": entries["agents"],
            "tools": entries["tools"],
            "skills": entries["skills"],
            "auto_install_dependencies": True,
        },
    }

    path = os.path.join(directory, "package.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return path


__all__ = ["discover_entries", "infer_kind", "scaffold_package_json"]
