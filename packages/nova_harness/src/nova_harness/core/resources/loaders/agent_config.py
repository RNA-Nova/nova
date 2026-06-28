"""Agent 配置加载实现。

负责从文件系统加载 Agent 配置源文件（description.md、sections/、tools.json、
setup.md、user/），并按 Nova 资源优先级（全局 -> 项目级）合并结果。
"""

import glob
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from nova_harness.core.config.defaults import CONFIG_DIR_NAME
from nova_harness.core.types.agent_config import AgentConfig, Section, ToolInfo

# =============================================================================
# 文件级加载
# =============================================================================


def load_text_file(file_path: str) -> Optional[str]:
    """安全加载文本文件。"""
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return content if content else None
    except (IOError, UnicodeDecodeError):
        return None


def load_json_file(file_path: str) -> Optional[dict]:
    """安全加载 JSON 文件。"""
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def load_tools(tools_file: str) -> List[ToolInfo]:
    """
    加载 tools.json 为 ToolInfo 列表。

    格式示例：
    [
      {"name": "read_file", "description": "读取文件..."},
      {"name": "write_file", "description": "写入文件..."}
    ]
    """
    data = load_json_file(tools_file)
    if not isinstance(data, list):
        return []

    tools = []
    for item in data:
        if isinstance(item, dict) and "name" in item:
            tools.append(
                ToolInfo(name=item["name"], description=item.get("description", ""))
            )
    return tools


def load_sections(sections_dir: str, source_label: str = "system") -> List[Section]:
    """
    加载指定目录的 Markdown 文件为 Section 列表。

    按数字前缀排序（01-, 02-...），无数字前缀的按文件名字母排序。
    """
    if not os.path.exists(sections_dir) or not os.path.isdir(sections_dir):
        return []

    md_files = glob.glob(os.path.join(sections_dir, "*.md"))
    if not md_files:
        return []

    def sort_key(path: str) -> tuple:
        filename = os.path.basename(path)
        match = re.match(r"^(\d+)[-_]", filename)
        if match:
            return (0, int(match.group(1)), filename)
        return (1, 0, filename)

    md_files.sort(key=sort_key)

    sections = []
    for order, filepath in enumerate(md_files, start=1):
        content = load_text_file(filepath)
        if content is None:
            continue

        filename = os.path.basename(filepath)
        clean_name = re.sub(r"^\d+[-_]", "", filename)
        clean_name = clean_name.replace(".md", "")
        clean_name = clean_name.replace("-", " ").replace("_", " ")

        sections.append(
            Section(
                name=clean_name,
                order=order,
                content=content,
                source=f"{source_label}:{filename}",
            )
        )

    return sections


def load_user_sections_recursive(user_dir: str) -> List[Section]:
    """递归加载 user/ 目录的所有 Markdown 文件。"""
    sections = []

    if not os.path.exists(user_dir):
        return sections

    md_files = []
    for root, _, files in os.walk(user_dir):
        for filename in sorted(files):
            if filename.endswith(".md"):
                filepath = os.path.join(root, filename)
                relpath = os.path.relpath(filepath, user_dir)
                md_files.append((filepath, relpath))

    md_files.sort(key=lambda x: x[1])

    for order, (filepath, relpath) in enumerate(md_files, start=1):
        content = load_text_file(filepath)
        if content:
            name = relpath.replace(".md", "").replace(os.sep, "/")
            sections.append(
                Section(
                    name=name, order=order, content=content, source=f"user:{relpath}"
                )
            )

    return sections


def _parse_description_md(file_path: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """解析 description.md，支持可选 YAML frontmatter。

    返回 (frontmatter_dict, body_text)。
    没有 frontmatter 时返回 ({}, original_text)。
    """
    content = load_text_file(file_path)
    if content is None:
        return {}, None

    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        frontmatter = {}

    body = parts[2].strip() or None
    return frontmatter, body


def _merge_tools(
    file_tools: List[ToolInfo], frontmatter_tools: Optional[List[Any]]
) -> List[ToolInfo]:
    """合并 tools.json 与 frontmatter 中的 tools，按 name 去重。"""
    seen = {t.name: t for t in file_tools}

    for item in frontmatter_tools or []:
        if isinstance(item, str):
            name = item.strip()
            if name and name not in seen:
                seen[name] = ToolInfo(name=name, description="")
        elif isinstance(item, dict) and "name" in item:
            name = str(item["name"]).strip()
            if name and name not in seen:
                seen[name] = ToolInfo(
                    name=name, description=item.get("description", "")
                )

    return list(seen.values())


def load_agent_config_from_dir(agent_dir: str) -> Optional[AgentConfig]:
    """加载单个 Agent 配置目录为 AgentConfig。"""
    directory = Path(agent_dir)
    description_file = directory / "description.md"
    if not description_file.exists():
        return None

    frontmatter, description = _parse_description_md(str(description_file))
    file_tools = load_tools(str(directory / "tools.json"))
    tools = _merge_tools(file_tools, frontmatter.get("tools"))

    return AgentConfig(
        name=directory.name,
        agent_dir=str(directory),
        description=description,
        model=frontmatter.get("model"),
        subagents=frontmatter.get("subagents") or [],
        sections=load_sections(str(directory / "sections"), source_label="system"),
        tools=tools,
        setup_content=load_text_file(str(directory / "setup.md")) or None,
        user_sections=load_user_sections_recursive(str(directory / "user")),
    )


def load_agents(agents_dir: str) -> Dict[str, AgentConfig]:
    """加载指定 agents/ 目录下的所有 Agent 配置。"""
    agents: Dict[str, AgentConfig] = {}
    directory = Path(agents_dir)
    if not directory.exists():
        return agents

    for entry in sorted(directory.iterdir()):
        if not entry.is_dir():
            continue
        config = load_agent_config_from_dir(str(entry))
        if config is not None:
            agents[config.name] = config

    return agents


# Backward-compatible alias
def load_agent_definitions(definitions_dir: str) -> Dict[str, AgentConfig]:
    """已废弃，请使用 :func:`load_agents`。"""
    return load_agents(definitions_dir)


# =============================================================================
# Resource 级加载
# =============================================================================


def load_agent_config(cwd: str, agent_dir: str) -> Dict[str, AgentConfig]:
    """
    按资源优先级加载所有可用 Agent 配置。

    先加载全局配置，再由项目级配置覆盖同名配置。
    """
    agents: Dict[str, AgentConfig] = {}

    global_agents = load_agents(str(Path(agent_dir) / "agents"))
    agents.update(global_agents)

    project_agents = load_agents(str(Path(cwd) / CONFIG_DIR_NAME / "agents"))
    agents.update(project_agents)

    return agents


__all__ = [
    "load_agent_config",
    "load_text_file",
    "load_json_file",
    "load_tools",
    "load_sections",
    "load_user_sections_recursive",
    "load_agent_config_from_dir",
    "load_agents",
    "load_agent_definitions",
]
