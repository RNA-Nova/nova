"""Package manager utilities."""

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json_file(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_json_file(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def copytree(src: str, dst: str) -> None:
    """Copy a directory tree, overwriting the destination if it exists."""
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def is_agent_dir(path: str) -> bool:
    """Check whether *path* looks like a valid agent config directory."""
    if not os.path.isdir(path):
        return False
    markers = [
        "description.md",
        "setup.md",
        "tools.json",
        "package.json",
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
        "package.json",
    ]
    for m in markers:
        if os.path.exists(os.path.join(path, m)):
            return True
    return False


def infer_kind(path: str) -> Optional[str]:
    """Infer whether *path* is an agent config, tool, skill, or bundle package."""
    agents = os.path.join(path, "agents")
    tools = os.path.join(path, "tools")
    skills = os.path.join(path, "skills")
    if os.path.isdir(agents) or os.path.isdir(tools) or os.path.isdir(skills):
        return "bundle"
    if os.path.isfile(os.path.join(path, "SKILL.md")):
        return "skill"
    if is_agent_dir(path):
        return "agent"
    if is_tool_dir(path):
        return "tool"
    return None


__all__ = [
    "copytree",
    "infer_kind",
    "is_agent_dir",
    "is_tool_dir",
    "load_json_file",
    "now_iso",
    "save_json_file",
]
