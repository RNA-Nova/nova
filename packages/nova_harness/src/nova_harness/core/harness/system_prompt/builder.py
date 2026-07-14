"""
SystemPromptBuilder — 把 Agent 配置渲染成系统提示词字符串。

包含动态部分渲染、工具白名单过滤与最终文本组装。
"""

from typing import Dict, List, Optional, Set

from nova_harness.core.types.agent.config import (
    AgentConfig,
    DynamicContext,
    Section,
    ToolInfo,
)
from nova_harness.core.types.resources.context_files import ContextFile
from nova_harness.core.types.runtime.tools import ToolDefinition
from nova_harness.core.types.skills import Skill
from nova_harness.core.utils.skills import format_skills_for_prompt


def render_agent_description(description: Optional[str]) -> str:
    """渲染 Agent 描述。"""
    if not description:
        return ""
    return f"# Agent Description\n\n{description}"


def render_sections(sections: List[Section], title: Optional[str] = None) -> str:
    """渲染 Section 列表。"""
    if not sections:
        return ""

    parts = []
    if title:
        parts.append(f"# {title}")

    for section in sections:
        display_name = section.name.replace("-", " ").replace("_", " ").title()
        parts.append(f"## {display_name}\n\n{section.content}")

    return "\n\n".join(parts)


def render_tools(
    tools: List[ToolInfo],
    selected_names: Optional[Set[str]] = None,
    tool_definitions: Optional[Dict[str, ToolDefinition]] = None,
) -> str:
    """
    渲染 Tools，支持白名单模式与 prompt snippet。

    Args:
        tools: Agent 配置中的工具列表（作为 fallback 名称/描述来源）
        selected_names: 要启用的工具名称集合（白名单，大小写敏感）
                      如果为 None，则包含所有工具
        tool_definitions: 实际加载的工具定义，用于读取 ``prompt_snippet``
    """
    tool_definitions = tool_definitions or {}

    # 以 AgentConfig.tools 为基础，补充来自 loader/extension 的工具定义
    merged: Dict[str, ToolInfo] = {t.name: t for t in tools}
    for name, definition in tool_definitions.items():
        if name not in merged:
            merged[name] = ToolInfo(name=name, description=definition.description)

    # 白名单过滤：仅保留指定工具
    if selected_names:
        merged = {n: t for n, t in merged.items() if n in selected_names}

    if not merged:
        return ""

    lines = ["# Available Tools", ""]
    for tool in merged.values():
        definition = tool_definitions.get(tool.name)
        snippet = (
            definition.prompt_snippet
            if definition and definition.prompt_snippet
            else tool.description
        )
        lines.append(f"- **{tool.name}**: {snippet}")
    return "\n".join(lines)


def render_guidelines(guidelines: List[str]) -> str:
    """渲染工具使用规范段落。"""
    if not guidelines:
        return ""
    lines = ["# Tool Guidelines", ""]
    for guideline in guidelines:
        lines.append(f"- {guideline}")
    return "\n".join(lines)


def render_project_context(context_files: List[ContextFile]) -> str:
    """渲染项目上下文文件（如 ``AGENTS.md`` / ``CLAUDE.md``）。"""
    if not context_files:
        return ""

    parts = ["<project_context>", ""]
    parts.append("Project-specific instructions and guidelines:")
    parts.append("")
    for ctx in context_files:
        parts.append(
            f'<project_instructions path="{ctx.path}">\n'
            f"{ctx.content}\n"
            f"</project_instructions>"
        )
    parts.append("</project_context>")
    return "\n".join(parts)


def render_dynamic_section(context: DynamicContext) -> str:
    """渲染动态元信息部分。"""
    lines = ["# Meta (Dynamic)"]

    if context.cwd:
        lines.append(f"- **Working directory**: {context.cwd}")

    if context.timestamp:
        lines.append(f"- **Current time**: {context.timestamp}")

    if context.session_id:
        lines.append(f"- **Session**: {context.session_id}")

    if context.custom_vars:
        lines.append("")
        lines.append("## Context Variables")
        for key, value in context.custom_vars.items():
            lines.append(f"- **{key}**: {value}")

    return "\n".join(lines)


def render_skills(skills: List[Skill], has_read_tool: bool = True) -> str:
    """渲染 skill 列表为追加到 system prompt 的文本。"""
    if not skills:
        return ""
    return format_skills_for_prompt(skills, has_read_tool=has_read_tool)


def compose_system_prompt(
    config: AgentConfig,
    context: Optional[DynamicContext] = None,
    include_tools: bool = True,
    include_dynamic: bool = True,
    include_skills: bool = True,
    selected_tools: Optional[List[str]] = None,
    skills: Optional[List[Skill]] = None,
    tool_definitions: Optional[Dict[str, ToolDefinition]] = None,
    context_files: Optional[List[ContextFile]] = None,
) -> str:
    """
    组合完整的系统提示词。

    Args:
        config: Agent 配置
        context: 动态上下文
        include_tools: 是否包含工具部分
        include_dynamic: 是否包含动态部分
        include_skills: 是否包含 skill 部分
        selected_tools: 要启用的工具名称列表
        skills: 已加载的 skill 列表
        tool_definitions: 工具定义，用于读取 prompt snippet 和 guidelines
        context_files: 项目上下文文件

    结构：
    1. Agent Description（静态）
    2. System Sections（静态）
    3. Available Tools（静态）
    4. Tool Guidelines（静态）
    ---
    5. Meta (Dynamic)（动态）
    6. Project Context（可选）
    7. Skills（可选）
    """
    parts = []

    desc_md = render_agent_description(config.description)
    if desc_md:
        parts.append(desc_md)

    if config.sections:
        sections_md = render_sections(config.sections, title="System Instructions")
        if sections_md:
            parts.append(sections_md)

    if include_tools:
        selected_set = set(selected_tools) if selected_tools else None
        tools_md = render_tools(
            config.tools,
            selected_names=selected_set,
            tool_definitions=tool_definitions,
        )
        if tools_md:
            parts.append(tools_md)

        guidelines: List[str] = []
        for name, definition in (tool_definitions or {}).items():
            if selected_set is not None and name not in selected_set:
                continue
            if definition.prompt_guidelines:
                guidelines.extend(definition.prompt_guidelines)
        if guidelines:
            guidelines_md = render_guidelines(guidelines)
            if guidelines_md:
                parts.append(guidelines_md)

    static_content = "\n\n".join(parts) if parts else ""

    dynamic_parts = []
    if include_dynamic and context:
        dynamic_md = render_dynamic_section(context)
        if dynamic_md:
            dynamic_parts.append(dynamic_md)

    project_context_md = render_project_context(context_files or [])
    if project_context_md:
        dynamic_parts.append(project_context_md)

    if include_skills and skills:
        has_read = selected_tools is None or "read" in selected_tools
        skills_md = render_skills(skills, has_read_tool=has_read)
        if skills_md:
            dynamic_parts.append(skills_md)

    sections: List[str] = []
    if static_content:
        sections.append(static_content)
    if dynamic_parts:
        sections.append("\n\n".join(dynamic_parts))

    if sections:
        return "\n\n".join(sections)
    return "You are a helpful assistant."
