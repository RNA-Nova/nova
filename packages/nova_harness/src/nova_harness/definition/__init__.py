# definition/__init__.py

"""
AgentDefinitor - 智能体表型管理器（动态上下文 + 工具白名单）

支持工具白名单模式：
    >>> agent.build_system_prompt(selected_tools=["read_file", "search"])
    >>> # 或
    >>> agent.prompt(selected_tools=["read_file"], cwd="/tmp")

典型用法：
    >>> from definition import AgentDefinitor, DynamicContext
    >>> agent = AgentDefinitor("./my_agent")
    >>> 
    >>> # 只读模式（仅启用读取类工具）
    >>> prompt = agent.prompt(
    ...     selected_tools=["read_file", "search"],
    ...     mode="read-only"
    ... )
"""

from typing import List, Optional

from .definitor import AgentDefinitor
from .types import AgentConfig, DynamicContext, Section, ToolInfo

__version__ = "2.3.0"
__all__ = [
    "AgentDefinitor",
    "AgentConfig",
    "Section", 
    "ToolInfo",
    "DynamicContext",
]


def load_agent(agent_dir: str) -> AgentDefinitor:
    """快速加载Agent."""
    return AgentDefinitor(agent_dir)


def build_prompt(
    agent_dir: str, 
    context: Optional[DynamicContext] = None,
    selected_tools: Optional[List[str]] = None,
    **dynamic_vars
) -> str:
    """
    快速构建系统提示词。
    
    Args:
        agent_dir: Agent目录路径
        context: 动态上下文对象（优先使用）
        selected_tools: 要启用的工具名称列表（白名单模式，未指定的工具将被排除）
        **dynamic_vars: 如果未提供 context，自动构建 DynamicContext
        
    Examples:
        >>> # 仅启用读取工具（安全模式）
        >>> prompt = build_prompt(
        ...     "./agent", 
        ...     selected_tools=["read_file", "search"],
        ...     cwd="/tmp"
        ... )
    """
    agent = AgentDefinitor(agent_dir)
    if context:
        return agent.build_system_prompt(context=context, selected_tools=selected_tools)
    else:
        return agent.prompt(selected_tools=selected_tools, **dynamic_vars)