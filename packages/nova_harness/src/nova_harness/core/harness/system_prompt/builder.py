"""
SystemPromptBuilder — 把 Agent 配置渲染成系统提示词字符串。

包含动态部分渲染、工具白名单过滤与最终文本组装。
"""

from typing import Dict, List, Optional, Set

from nova_harness.core.types.agent_config import (
    AgentConfig,
    DynamicContext,
    Section,
    ToolInfo,
)
from nova_harness.core.types.tools import ToolDefinition


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


def render_user_context(sections: List[Section]) -> str:
    """渲染 User Context。"""
    if not sections:
        return ""
    parts = ["# User Context", ""]
    for section in sections:
        display_path = section.name.replace("/", " > ")
        parts.append(
            f"<!-- Source: {section.source} -->\n\n## {display_path}\n\n{section.content}"
        )
    return "\n\n".join(parts)


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


def render_onboarding(setup_content: str, user_dir: str) -> str:
    """渲染首次激活提示词。"""
    save_instructions = f"""

---

## Save Instructions

After collecting user information, save it to:

**Directory**: `{user_dir}/`

You can create any `.md` files in this directory to persist user data.
Use available tools (e.g., `write_file`) to save files. If a file exists, read it first then update.
"""
    return f"{setup_content}{save_instructions}"


def compose_system_prompt(
    config: AgentConfig,
    context: Optional[DynamicContext] = None,
    include_user: bool = True,
    include_tools: bool = True,
    include_dynamic: bool = True,
    selected_tools: Optional[List[str]] = None,
    append_system_prompt: Optional[str] = None,
    tool_definitions: Optional[Dict[str, ToolDefinition]] = None,
) -> str:
    """
    组合完整的系统提示词。

    Args:
        config: Agent 配置
        context: 动态上下文
        include_user: 是否包含用户数据
        include_tools: 是否包含工具部分（布尔开关）
        include_dynamic: 是否包含动态部分
        selected_tools: 要启用的工具名称列表（白名单模式，未指定的工具将被排除）
                      如果为 None，则包含所有工具
        append_system_prompt: 需要追加在末尾的额外内容（如 skill 列表）

    结构：
    1. Agent Description（静态）
    2. System Sections（静态）
    3. Available Tools（静态，仅包含白名单中的工具）
    ---
    4. Meta (Dynamic)（动态）
    ---
    5. User Context（可选）
    ---
    6. Append System Prompt（可选）
    """
    parts = []

    # 1. 静态：Agent 描述
    desc_md = render_agent_description(config.description)
    if desc_md:
        parts.append(desc_md)

    # 2. 静态：System Sections
    if config.sections:
        sections_md = render_sections(config.sections, title="System Instructions")
        if sections_md:
            parts.append(sections_md)

    # 3. 静态：Tools（支持白名单过滤 + prompt snippet）
    if include_tools:
        selected_set = set(selected_tools) if selected_tools else None
        tools_md = render_tools(
            config.tools,
            selected_names=selected_set,
            tool_definitions=tool_definitions,
        )
        if tools_md:
            parts.append(tools_md)

        # 3.1 工具使用规范
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

    # 合并静态内容
    static_content = "\n\n".join(parts) if parts else ""

    # 4. 动态：Meta 信息
    dynamic_parts = []
    if include_dynamic and context:
        dynamic_md = render_dynamic_section(context)
        if dynamic_md:
            dynamic_parts.append(dynamic_md)

    # 5. 可选：User Context
    if include_user and config.user_sections:
        user_md = render_user_context(config.user_sections)
        if user_md:
            dynamic_parts.append(user_md)

    # 组装动态部分
    dynamic_content = "\n\n".join(dynamic_parts) if dynamic_parts else ""

    # 6. 可选：追加内容
    append_parts = []
    if append_system_prompt:
        append_parts.append(append_system_prompt)

    # 最终组装
    sections: List[str] = []
    if static_content:
        sections.append(static_content)
    if dynamic_content:
        sections.append(dynamic_content)
    if append_parts:
        sections.append("\n\n".join(append_parts))

    if sections:
        return "\n\n".join(sections)
    return "You are a helpful assistant."
