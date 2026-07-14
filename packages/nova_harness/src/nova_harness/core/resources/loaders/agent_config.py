"""Agent 配置加载实现。

负责从文件系统加载 Agent 配置源文件（agent.yaml、description.md、sections/），
并按 Nova 资源优先级合并结果。
"""

import glob
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from nova_harness.core.types.agent.config import AgentConfig, Section, ToolInfo
from nova_harness.core.types.package_manager import ResolvedResource
from nova_harness.core.utils.files import load_text_file

# =============================================================================
# 文件级加载
# =============================================================================


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


def _parse_description_md(
    file_path: str,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """解析 description.md，支持可选 YAML frontmatter。

    返回 (frontmatter_dict, body_text)。没有 frontmatter 时 body 为原文本。
    """
    content = load_text_file(file_path)
    if content is None:
        return {}, None

    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    body = parts[2].strip() or None
    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, body

    return frontmatter, body


def _load_agent_yaml(agent_yaml_path: str) -> Dict[str, Any]:
    """加载 agent.yaml 为字典，失败时返回空字典。"""
    if not os.path.exists(agent_yaml_path):
        return {}

    try:
        with open(agent_yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def _parse_tool_items(items: List[Any]) -> List[ToolInfo]:
    """把 agent.yaml/frontmatter 中的 tool 条目解析为 ToolInfo 列表。"""
    tools: List[ToolInfo] = []
    seen: set = set()

    for item in items or []:
        if isinstance(item, str):
            name = item.strip()
            if name and name not in seen:
                seen.add(name)
                tools.append(ToolInfo(name=name, description=""))
        elif isinstance(item, dict) and "name" in item:
            name = str(item["name"]).strip()
            if name and name not in seen:
                seen.add(name)
                tools.append(
                    ToolInfo(
                        name=name,
                        description=item.get("description", ""),
                        parameters=item.get("parameters"),
                        prompt_snippet=item.get("prompt_snippet"),
                        prompt_guidelines=item.get("prompt_guidelines"),
                    )
                )

    return tools


def _parse_string_list(items: List[Any]) -> List[str]:
    """把字符串列表过滤、去重。"""
    seen: set = set()
    result: List[str] = []
    for item in items or []:
        if isinstance(item, str):
            value = item.strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
    return result


def _merge_tools(
    yaml_tools: List[ToolInfo], frontmatter_tools: Optional[List[Any]]
) -> List[ToolInfo]:
    """合并 agent.yaml 与 frontmatter 中的 tools，按 name 去重。"""
    seen = {t.name: t for t in yaml_tools}

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


def _merge_string_lists(
    yaml_values: List[str], frontmatter_values: Optional[List[Any]]
) -> List[str]:
    """合并 agent.yaml 与 frontmatter 中的字符串列表，按顺序去重。"""
    seen: set = set()
    result: List[str] = []

    for value in yaml_values:
        if value not in seen:
            seen.add(value)
            result.append(value)

    for item in frontmatter_values or []:
        if isinstance(item, str):
            value = item.strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)

    return result


def load_agent_config_from_dir(agent_dir: str) -> Optional[AgentConfig]:
    """加载单个 Agent 配置目录为 AgentConfig。"""
    directory = Path(agent_dir)

    agent_yaml_path = directory / "agent.yaml"
    description_file = directory / "description.md"

    # 至少要存在 agent.yaml 或 description.md 才认为是一个 agent 目录
    if not agent_yaml_path.exists() and not description_file.exists():
        return None

    agent_data = _load_agent_yaml(str(agent_yaml_path))
    frontmatter, description_body = (
        _parse_description_md(str(description_file))
        if description_file.exists()
        else ({}, None)
    )

    # 名称优先用 agent.yaml 里的，否则用目录名
    name = agent_data.get("name") or directory.name

    yaml_tools = _parse_tool_items(agent_data.get("tools"))
    tools = _merge_tools(yaml_tools, frontmatter.get("tools"))

    subagents = _merge_string_lists(
        _parse_string_list(agent_data.get("subagents")),
        frontmatter.get("subagents"),
    )
    skills = _merge_string_lists(
        _parse_string_list(agent_data.get("skills")),
        frontmatter.get("skills"),
    )
    extensions = _merge_string_lists(
        _parse_string_list(agent_data.get("extensions")),
        frontmatter.get("extensions"),
    )

    description = (
        agent_data.get("description")
        or frontmatter.get("description")
        or description_body
    )

    return AgentConfig(
        name=name,
        agent_dir=str(directory),
        description=description,
        model=agent_data.get("model") or frontmatter.get("model"),
        subagents=subagents,
        sections=load_sections(str(directory / "sections"), source_label="system"),
        tools=tools,
        skills=skills,
        extensions=extensions,
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


# =============================================================================
# Resource 级加载
# =============================================================================


def load_agent_configs(
    resolved_paths: Optional[List[ResolvedResource]] = None,
) -> Dict[str, AgentConfig]:
    """根据 resolver 给出的 Agent 路径加载配置。

    路径已按优先级排序，后续配置会覆盖同名配置。
    """
    agents: Dict[str, AgentConfig] = {}
    for resource in resolved_paths or []:
        if not resource.enabled:
            continue
        config = load_agent_config_from_dir(resource.path)
        if config is not None:
            agents[config.name] = config
    return agents


__all__ = [
    "load_agent_config",
    "load_agent_configs",
    "load_sections",
    "load_agent_config_from_dir",
    "load_agents",
]
