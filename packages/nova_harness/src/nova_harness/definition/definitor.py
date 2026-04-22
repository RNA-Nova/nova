# definition/definitor.py

"""
AgentDefinitor - 核心管理器模块（含工具白名单）
"""

import os
import shutil
from typing import List, Optional, Set

from .loader import (
    load_sections,
    load_text_file,
    load_tools,
    load_user_sections_recursive,
)
from .render import compose_system_prompt, render_onboarding
from .types import AgentConfig, DynamicContext, Section, ToolInfo


class AgentDefinitor:
    """
    Agent管理器（支持工具白名单模式）。
    
    可在构建时传入 selected_tools 列表指定仅使用的工具，未在列表中的工具将被排除。
    """
    
    def __init__(self, agent_dir: str) -> None:
        self._init_paths(agent_dir)
        self.refresh()

    def _init_paths(self, agent_dir: str) -> None:
        """初始化或更新 agent 目录相关路径。"""
        if not os.path.exists(agent_dir):
            raise FileNotFoundError(f"Agent directory not found: {agent_dir}")
        
        if not os.path.isdir(agent_dir):
            raise NotADirectoryError(f"Path is not a directory: {agent_dir}")
        
        self.agent_dir = os.path.abspath(agent_dir)
        self.agent_name = os.path.basename(self.agent_dir)
        
        # 路径定义
        self.description_file = os.path.join(self.agent_dir, "description.md")
        self.sections_dir = os.path.join(self.agent_dir, "sections")
        self.tools_file = os.path.join(self.agent_dir, "tools.json")
        self.setup_file = os.path.join(self.agent_dir, "setup.md")
        self.user_dir = os.path.join(self.agent_dir, "user")
        
        # 初始化配置
        self.config = AgentConfig(
            name=self.agent_name,
            agent_dir=self.agent_dir
        )

    def set_agent_dir(self, agent_dir: str) -> None:
        """
        动态切换 agent 定义目录。

        切换后会自动重新加载所有内容（description、sections、tools、setup、user）。
        
        Args:
            agent_dir: 新的 agent 定义目录路径
        """
        self._init_paths(agent_dir)
        self.refresh()

    def refresh(self) -> None:
        """重新加载所有内容."""
        self.config.description = load_text_file(self.description_file)
        self.config.sections = load_sections(self.sections_dir, source_label="system")
        self.config.tools = load_tools(self.tools_file)
        self.config.setup_content = load_text_file(self.setup_file)
        self.config.user_sections = load_user_sections_recursive(self.user_dir)

    def is_first_time(self) -> bool:
        """检查是否需要首次激活."""
        if not self.config.has_setup:
            return False
        
        if not os.path.exists(self.user_dir):
            return True
            
        for _, _, files in os.walk(self.user_dir):
            if any(f.endswith(".md") for f in files):
                return False
        
        return True

    def build_onboarding(self) -> Optional[str]:
        """构建首次激活提示词."""
        if not self.config.setup_content:
            return None
        return render_onboarding(self.config.setup_content, self.user_dir)

    def build_system_prompt(
        self,
        context: Optional[DynamicContext] = None,
        selected_tools: Optional[List[str]] = None,
        include_user: bool = True,
        include_tools: bool = True,
        include_dynamic: bool = True, 
    ) -> str:
        """
        构建完整的系统提示词。
        
        Args:
            context: 动态上下文（包含 cwd, timestamp, custom_vars 等）
            include_user: 是否包含 user/ 数据
            include_tools: 是否包含 tools 部分（注意：这是布尔开关）
            include_dynamic: 是否包含动态 Meta 部分
            selected_tools: 要启用的工具名称列表（白名单模式，未指定的工具将被排除）
                           只有列表中的工具会出现在 Available Tools 中
                           如果为 None，则包含所有工具
                          
        Returns:
            组装后的 Markdown 字符串
        """
        # 如果开启动态部分但没有提供 context，自动创建
        if include_dynamic and context is None:
            context = DynamicContext(cwd=os.getcwd())
        
        return compose_system_prompt(
            config=self.config,
            context=context,
            include_user=include_user,
            include_tools=include_tools,
            include_dynamic=include_dynamic,
            selected_tools=selected_tools,  # 传入白名单列表
        )

    def prompt(self, selected_tools: Optional[List[str]] = None, **dynamic_vars) -> str:
        """
        快速构建提示词的便捷方法。
        
        Args:
            selected_tools: 要启用的工具名称列表（白名单模式，未指定的工具将被排除）
            **dynamic_vars: 动态变量（cwd, timestamp, session_id 等）
            
        Examples:
            >>> # 正常构建（包含所有工具）
            >>> agent.prompt()
            >>> 
            >>> # 仅启用特定工具（白名单模式）
            >>> agent.prompt(selected_tools=["read_file", "search"])
            >>> 
            >>> # 组合使用
            >>> agent.prompt(
            ...     cwd="/project",
            ...     selected_tools=["read_file"],
            ...     mode="safe"
            ... )
        """
        # 提取标准字段
        cwd = dynamic_vars.pop("cwd", os.getcwd())
        timestamp = dynamic_vars.pop("timestamp", None)
        session_id = dynamic_vars.pop("session_id", None)
        
        context = DynamicContext(
            cwd=cwd,
            timestamp=timestamp,
            session_id=session_id,
            custom_vars=dynamic_vars
        )
        
        return self.build_system_prompt(
            context=context,
            selected_tools=selected_tools  # 透传白名单列表
        )

    def get_available_tools(self, selected: Optional[List[str]] = None) -> List[str]:
        """
        获取当前可用工具名称列表（支持预览白名单过滤后的结果）。
        
        Args:
            selected: 要启用的工具名称列表（用于预览，None 则返回所有）
            
        Returns:
            工具名称列表
        """
        all_tools = [t.name for t in self.config.tools]
        if selected:
            selected_set = set(selected)
            return [t for t in all_tools if t in selected_set]
        return all_tools

    def get_info(self) -> dict:
        """获取Agent信息摘要."""
        return self.config.to_dict()

    def list_user_sections(self) -> List[str]:
        """列出所有用户数据文件路径."""
        return [s.name for s in self.config.user_sections]

    def add_user_section(self, relative_path: str, content: str) -> None:
        """添加或更新用户数据."""
        if ".." in relative_path or relative_path.startswith("/"):
            raise ValueError(f"Invalid relative path: {relative_path}")
        
        full_path = os.path.join(self.user_dir, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        self.refresh()

    def remove_user_section(self, relative_path: str) -> bool:
        """删除用户数据文件."""
        if ".." in relative_path or relative_path.startswith("/"):
            raise ValueError(f"Invalid relative path: {relative_path}")
        
        full_path = os.path.join(self.user_dir, relative_path)
        if os.path.exists(full_path):
            os.remove(full_path)
            self.refresh()
            return True
        return False

    def reset_user_data(self) -> bool:
        """清空所有用户数据."""
        if os.path.exists(self.user_dir):
            shutil.rmtree(self.user_dir)
            self.config.user_sections = []
            return True
        return False

    def reload_tools(self) -> List[ToolInfo]:
        """热重载 tools.json."""
        self.config.tools = load_tools(self.tools_file)
        return self.config.tools

    def reload_user(self) -> List[Section]:
        """热重载 user/ 目录."""
        self.config.user_sections = load_user_sections_recursive(self.user_dir)
        return self.config.user_sections