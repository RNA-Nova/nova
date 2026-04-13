# definition/render.py

"""
AgentDefinitor - 渲染模块（含动态部分和工具白名单）
"""

from typing import Any, Dict, List, Optional, Set

from .types import AgentConfig, DynamicContext, Section, ToolInfo


def render_agent_description(description: Optional[str]) -> str:
    """渲染Agent描述."""
    if not description:
        return ""
    return f"# Agent Description\n\n{description}"


def render_sections(sections: List[Section], title: Optional[str] = None) -> str:
    """渲染 Section 列表."""
    if not sections:
        return ""
    
    parts = []
    if title:
        parts.append(f"# {title}")
    
    for section in sections:
        display_name = section.name.replace("-", " ").replace("_", " ").title()
        parts.append(f"## {display_name}\n\n{section.content}")
    
    return "\n\n".join(parts)


def render_tools(tools: List[ToolInfo], selected_names: Optional[Set[str]] = None) -> str:
    """
    渲染 Tools，支持白名单模式。
    
    Args:
        tools: 工具列表
        selected_names: 要启用的工具名称集合（白名单，大小写敏感）
                      如果为 None，则包含所有工具
    """
    if not tools:
        return ""
    
    # 白名单过滤：仅保留指定工具
    if selected_names:
        tools = [t for t in tools if t.name in selected_names]
    
    if not tools:
        return ""
    
    lines = ["# Available Tools", ""]
    for tool in tools:
        lines.append(f"- **{tool.name}**: {tool.description}")
    return "\n".join(lines)


def render_user_context(sections: List[Section]) -> str:
    """渲染 User Context."""
    if not sections:
        return ""
    parts = ["# User Context", ""]
    for section in sections:
        display_path = section.name.replace("/", " > ")
        parts.append(f"<!-- Source: {section.source} -->\n\n## {display_path}\n\n{section.content}")
    return "\n\n".join(parts)


def render_dynamic_section(context: DynamicContext) -> str:
    """渲染动态元信息部分."""
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
    """渲染首次激活提示词."""
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
) -> str:
    """
    组合完整的系统提示词。
    
    Args:
        config: Agent配置
        context: 动态上下文
        include_user: 是否包含用户数据
        include_tools: 是否包含工具部分（布尔开关）
        include_dynamic: 是否包含动态部分
        selected_tools: 要启用的工具名称列表（白名单模式，未指定的工具将被排除）
                      如果为 None，则包含所有工具
        
    结构：
    1. Agent Description（静态）
    2. System Sections（静态）
    3. Available Tools（静态，仅包含白名单中的工具）
    ---
    4. Meta (Dynamic)（动态）
    ---
    5. User Context（可选）
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

    # 3. 静态：Tools（支持白名单过滤）
    if include_tools and config.tools:
        selected_set = set(selected_tools) if selected_tools else None
        tools_md = render_tools(config.tools, selected_names=selected_set)
        if tools_md:
            parts.append(tools_md)

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

    # 最终组装
    if static_content and dynamic_content:
        return f"{static_content}\n\n{dynamic_content}"
    elif static_content:
        return static_content
    elif dynamic_content:
        return dynamic_content
    else:
        return "You are a helpful assistant."