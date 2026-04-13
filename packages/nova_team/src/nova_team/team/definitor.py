# definition/definitor.py

"""
TeamDefinitor - 核心管理器（动态合并 + 状态修改接口）
"""

import os
from typing import Any, Dict, List, Optional, Set, Tuple

from ..definition import AgentDefinitor, DynamicContext

from .storage import FileMountsStorage, MountScope
from .types import MountsData, SubagentMountEntry


class TeamDefinitor:
    """
    Team 管理器（状态可修改，保存无参）.
    
    特性：
    1. 动态上下文：配置文件为基础，传入参数优先覆盖（合并）
    2. 状态修改：set_dynamic_context, set_selected_tools, set_agent_enabled
    3. 保存：直接保存内存中的 mounts_data，无需传入参数
    
    Example:
        >>> tm = TeamDefinitor("./team")
        >>> 
        >>> # 修改状态
        >>> tm.set_dynamic_context({"cwd": "/tmp", "mode": "debug"})
        >>> tm.set_selected_tools("master", ["read_file", "search"])  # 白名单模式
        >>> tm.set_agent_enabled("coder", True)
        >>> 
        >>> # 保存到项目层
        >>> tm.save_to_project()
    """
    
    def __init__(
        self,
        team_dir: str,
        cwd: str = os.getcwd(),
        agent_dir: str = None,
    ) -> None:
        if not os.path.isdir(team_dir):
            raise FileNotFoundError(f"Team directory not found: {team_dir}")
        
        self.team_dir = os.path.abspath(team_dir)
        self.master_dir = os.path.join(self.team_dir, "master")
        self.subagents_dir = os.path.join(self.team_dir, "subagents")
        
        # 存储层（两级）
        self.storage = FileMountsStorage(cwd=cwd, agent_dir=agent_dir)
        
        # 加载到内存（后续操作直接修改 self.mounts_data）
        self.mounts_data, self.effective_scope = self._read_effective()
        
        # 初始化 Agents
        self._master_definitor: Optional[AgentDefinitor] = None
        self._subagent_definitors: Dict[str, AgentDefinitor] = {}
        self._init_agents()
    
    def _init_agents(self) -> None:
        """初始化 AgentDefinitor."""
        if os.path.exists(self.master_dir):
            self._master_definitor = AgentDefinitor(self.master_dir)
        
        if os.path.exists(self.subagents_dir):
            for name in os.listdir(self.subagents_dir):
                path = os.path.join(self.subagents_dir, name)
                if os.path.isdir(path):
                    self._subagent_definitors[name] = AgentDefinitor(path)
    
    def _read_effective(self) -> Tuple[MountsData, Optional[MountScope]]:
        """读取有效 mounts（PROJECT > GLOBAL）."""
        import json
        
        # PROJECT 层
        project_content = None
        def _read_project(current):
            nonlocal project_content
            project_content = current
            return None
        self.storage.with_lock(MountScope.PROJECT, _read_project)
        
        if project_content:
            try:
                return MountsData.from_dict(json.loads(project_content)), MountScope.PROJECT
            except (json.JSONDecodeError, TypeError):
                pass
        
        # GLOBAL 层
        global_content = None
        def _read_global(current):
            nonlocal global_content
            global_content = current
            return None
        self.storage.with_lock(MountScope.GLOBAL, _read_global)
        
        if global_content:
            try:
                return MountsData.from_dict(json.loads(global_content)), MountScope.GLOBAL
            except (json.JSONDecodeError, TypeError):
                pass
        
        return MountsData(), None
    
    def _build_dynamic_context(
        self, 
        override: Optional[DynamicContext] = None
    ) -> DynamicContext:
        """
        构建动态上下文（以配置文件为基础，传入参数优先覆盖）.
        
        现在直接合并 DynamicContext 对象，不再处理字典转换。
        """
        # 基础配置
        base = self.mounts_data.dynamic_context
        
        if not override:
            return base
        
        # DynamicContext 合并：override 的非 None 字段覆盖 base
        return DynamicContext(
            cwd=override.cwd if override.cwd is not None else base.cwd,
            timestamp=override.timestamp if override.timestamp is not None else base.timestamp,
            session_id=override.session_id if override.session_id is not None else base.session_id,
            custom_vars={**base.custom_vars, **override.custom_vars}  # custom_vars 可以合并
        )
    
    # ==================== 新增：状态设置接口 ====================
    
    def set_dynamic_context(self, context: DynamicContext) -> None:
        """
        设置全局 dynamic_context（覆盖整个配置）.
        
        Args:
            context: 新的动态上下文字典（包含 cwd, timestamp, custom_vars 等）
        """
        self.mounts_data.dynamic_context = context
    
    def set_selected_tools(self, name: str, tools: List[str]) -> None:
        """
        根据名称设置选定的工具列表（白名单模式）.
        
        Args:
            name: "master" 或 subagent 名称（如 "coder", "writer"）
            tools: 要启用的工具名称列表（如 ["read_file", "search"]）。
                  只有列表中的工具会被包含，None 或空列表表示包含所有工具。
        
        Raises:
            KeyError: 如果指定 subagent 不存在且不是 "master"
        """
        if name == "master":
            self.mounts_data.master.selected_tools = tools
        else:
            # 确保 subagent 条目存在
            if name not in self.mounts_data.subagents:
                # 检查物理是否存在
                if name not in self._subagent_definitors:
                    raise KeyError(f"Subagent '{name}' not found")
                self.mounts_data.subagents[name] = SubagentMountEntry()
            
            self.mounts_data.subagents[name].selected_tools = tools
    
    def set_agent_enabled(self, name: str, enabled: bool) -> None:
        """
        根据名称启用或禁用智能体.
        
        Args:
            name: subagent 名称（如 "coder"）。注意：master 不能被禁用.
            enabled: True 启用，False 禁用
        
        Raises:
            ValueError: 如果尝试禁用 master
            KeyError: 如果指定 subagent 不存在
        """
        if name == "master":
            raise ValueError("Cannot enable/disable master agent")
        
        if name not in self._subagent_definitors:
            raise KeyError(f"Subagent '{name}' not found")
        
        if name not in self.mounts_data.subagents:
            self.mounts_data.subagents[name] = SubagentMountEntry()
        
        self.mounts_data.subagents[name].enabled = enabled
    
    # ==================== 渲染辅助方法 ====================
    
    def _render_sections(self, sections) -> str:
        """渲染 Sections."""
        if not sections:
            return ""
        parts = []
        for s in sections:
            title = s.name.replace("-", " ").replace("_", " ").title()
            parts.append(f"## {title}\n\n{s.content}")
        return "\n\n".join(parts)
    
    def _render_subagents_registry(self) -> str:
        """渲染挂载的 Subagents Registry."""
        lines = []
        
        for name, entry in self.mounts_data.subagents.items():
            if not entry.enabled:
                continue
            
            definitor = self._subagent_definitors.get(name)
            if not definitor:
                continue
            
            desc = definitor.config.description or f"Agent: {name}"
            lines.append(f"## {name}")
            lines.append(desc)
            lines.append("")
        
        if not lines:
            return ""
        
        return "# Available Subagents\n\n" + "\n".join(lines)
    
    def _render_tools(self, tools, selected: Optional[List[str]] = None) -> str:
        """
        渲染 Tools（应用白名单过滤）.
        
        Args:
            tools: 所有可用工具列表
            selected: 要选定的工具名称列表（白名单）。None 表示包含所有。
        """
        if not tools:
            return ""
        
        # 白名单过滤：仅保留选定的工具
        if selected:
            selected_set = set(selected)
            filtered = [t for t in tools if t.name in selected_set]
        else:
            filtered = tools
        
        if not filtered:
            return ""
        
        lines = ["# Available Tools", ""]
        for t in filtered:
            lines.append(f"- **{t.name}**: {t.description}")
        return "\n".join(lines)
    
    def _render_dynamic_section(self, context: DynamicContext) -> str:
        """渲染动态元信息（支持 <meta timestamp="..." /> 格式注释）."""
        parts = ["# Meta (Dynamic)"]
        
        # 时间戳可以用注释格式输出（如果用户需要 XML 风格）
        if context.timestamp:
            parts.append(f'- **timestamp**: {context.timestamp}')
        
        if context.cwd:
            parts.append(f"- **Working directory**: {context.cwd}")
        if context.session_id:
            parts.append(f"- **Session**: {context.session_id}")
        
        if context.custom_vars:
            parts.append("")
            parts.append("## Context Variables")
            for key, value in context.custom_vars.items():
                parts.append(f"- **{key}**: {value}")
        
        return "\n".join(parts)
    
    def _render_user_context(self, sections) -> str:
        """渲染 User Context."""
        if not sections:
            return ""
        parts = ["# User Context", ""]
        for s in sections:
            name = s.name.replace("/", " > ")
            parts.append(f"## {name}\n\n{s.content}")
        return "\n\n".join(parts)
    
    # ==================== 核心构建方法 ====================
    
    def build_master(self, override_context: Optional[DynamicContext] = None) -> str:
        """
        构建 Master Prompt.
        
        Args:
            override_context: 动态覆盖配置文件的 dynamic_context（优先合并）
        """
        if not self._master_definitor:
            raise RuntimeError("Master not initialized")
        
        cfg = self.mounts_data.master
        context = self._build_dynamic_context(override_context)
        
        # 静态部分
        static_parts = []
        
        if self._master_definitor.config.description:
            static_parts.append(
                f"# Agent Description\n\n{self._master_definitor.config.description}"
            )
        
        if self._master_definitor.config.sections:
            static_parts.append(self._render_sections(self._master_definitor.config.sections))
        
        if cfg.inject_subagents_desc:
            registry = self._render_subagents_registry()
            if registry:
                static_parts.append(registry)
        
        if cfg.include_tools and self._master_definitor.config.tools:
            tools_md = self._render_tools(
                self._master_definitor.config.tools, 
                cfg.selected_tools  # 改为 selected_tools（白名单）
            )
            if tools_md:
                static_parts.append(tools_md)
        
        static_content = "\n\n".join(static_parts) if static_parts else ""
        
        # 动态部分
        dynamic_parts = []
        
        if cfg.include_dynamic:
            dynamic_parts.append(self._render_dynamic_section(context))
        
        if cfg.include_user and self._master_definitor.config.user_sections:
            user_md = self._render_user_context(self._master_definitor.config.user_sections)
            if user_md:
                dynamic_parts.append(user_md)
        
        dynamic_content = "\n\n".join(dynamic_parts) if dynamic_parts else ""
        
        # 组装
        if static_content and dynamic_content:
            return f"{static_content}\n\n{dynamic_content}"
        elif static_content:
            return static_content
        elif dynamic_content:
            return dynamic_content
        return "You are a helpful assistant."
    
    def build_subagent(
        self, 
        name: str, 
        override_context: Optional[DynamicContext] = None
    ) -> Optional[str]:
        """构建 Subagent Prompt."""
        if name not in self._subagent_definitors:
            return None
        
        entry = self.mounts_data.subagents.get(name, SubagentMountEntry())
        if not entry.enabled:
            return None
        
        definitor = self._subagent_definitors[name]
        context = self._build_dynamic_context(override_context)
        
        return definitor.build_system_prompt(
            context=context,
            include_dynamic=entry.include_dynamic,
            include_tools=entry.include_tools,
            include_user=entry.include_user,
            selected_tools=entry.selected_tools,  # 改为 selected_tools
        )
    
    # ==================== 新增：无参保存接口 ====================
    
    def save_to_project(self) -> None:
        """
        保存当前内存中的 mounts_data 到 PROJECT 层.
        
        直接序列化 self.mounts_data，无需传入参数.
        """
        import json
        
        def _write(current):
            return json.dumps(self.mounts_data.to_dict(), indent=2, ensure_ascii=False)
        
        self.storage.with_lock(MountScope.PROJECT, _write)
    
    def save_to_global(self) -> None:
        """
        保存当前内存中的 mounts_data 到 GLOBAL 层.
        
        影响所有使用 GLOBAL 层的项目（谨慎使用）.
        """
        import json
        
        def _write(current):
            return json.dumps(self.mounts_data.to_dict(), indent=2, ensure_ascii=False)
        
        self.storage.with_lock(MountScope.GLOBAL, _write)
    
    def reload(self) -> None:
        """重新加载 mounts（丢弃内存修改，从文件重新读取）."""
        self.mounts_data, self.effective_scope = self._read_effective()
    
    def get_active_scope(self) -> Optional[MountScope]:
        """获取当前生效的存储层."""
        return self.effective_scope
    
    def is_first_time(self) -> bool:
        """检查是否需要 onboarding."""
        return self._master_definitor.is_first_time() if self._master_definitor else False
    
    def build_onboarding(self) -> Optional[str]:
        """构建 onboarding."""
        return self._master_definitor.build_onboarding() if self._master_definitor else None