"""
SystemPromptManager — 系统提示词纯渲染（config + 各 manager → 文本）。

它从 ResourceLoader 读取静态 Agent 配置，并把运行时工具选择委托给
``ToolsManager``，自身只负责渲染 system prompt。persona 装配（yaml
``persona:`` 条目 → Section 序列 + override 旋钮）委托给 ``PersonaManager``；
当前角色名与可委派菜单数据委托给 ``AgentManager``（旋钮已乔迁——本
管理器不再持有 ``change_agent``）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nova_harness.core.harness.skills import filter_skills_by_whitelist
from nova_harness.core.harness.system_prompt.builder import compose_system_prompt
from nova_harness.core.types.protocols import (
    ResourceLoaderProtocol,
    ToolsManagerProtocol,
)
from nova_harness.core.types.resources.agents import AgentConfig, DynamicContext
from nova_harness.core.types.resources.skills import Skill
from nova_harness.core.types.resources.tools import ToolDefinition, ToolInfo


class SystemPromptManager:
    """
    管理当前 Agent 配置并构建系统提示词。

    工具发现、过滤、激活等运行时逻辑已下沉到 ``ToolsManager``；
    当前角色旋钮归 ``AgentManager``；本类只负责：
    - 经 ``AgentManager`` 取当前 agent 配置（活视图——角色切换无需通知本类）
    - 调用 ``ToolsManager`` 获取激活工具和工具定义
    - 调用 ``PersonaManager`` 装配人格 sections（override 优先）
    - 渲染最终 system prompt
    """

    def __init__(
        self,
        resource_loader: ResourceLoaderProtocol,
        agent_manager: Any,
        tools_manager: Optional[ToolsManagerProtocol] = None,
        persona_manager: Optional[Any] = None,
    ) -> None:
        self._resource_loader = resource_loader
        self._agent_manager = agent_manager
        self._tools_manager = tools_manager
        self._persona_manager = persona_manager

    @property
    def agent_name(self) -> str:
        """当前 agent 名（只读转发 AgentManager——旋钮归 AgentManager）。"""
        return self._agent_manager.current

    @property
    def persona_manager(self) -> Optional[Any]:
        return self._persona_manager

    @persona_manager.setter
    def persona_manager(self, value: Optional[Any]) -> None:
        self._persona_manager = value

    def get_agent_config(self) -> Optional[AgentConfig]:
        """返回当前 agent 的静态配置。"""
        return self._resource_loader.get_agents().get(self._agent_manager.current)

    def _agent_config(self) -> Optional[AgentConfig]:
        return self.get_agent_config()

    def _filter_skills_by_config(self, skills: Dict[str, Any]) -> Dict[str, Any]:
        """按来源分治裁剪 skill：空名单全放；非空名单仅裁包内，其余来源放开。"""
        config = self.get_agent_config()
        allowed = getattr(config, "skills", None) if config else None
        return filter_skills_by_whitelist(skills, allowed)

    def _tool_definitions(self) -> Dict[str, ToolDefinition]:
        """返回工具定义字典；未绑定 ToolsManager 时返回空字典。"""
        if self._tools_manager is None:
            return {}
        return getattr(self._tools_manager, "tool_definitions", None) or {}

    def _all_tool_infos(self) -> List[ToolInfo]:
        """返回 AgentConfig.tools 与 ToolsManager 贡献的工具的合并列表。

        按工具名去重；当两者出现同名工具时，优先保留 AgentConfig 中显式声明
        的定义，避免 ToolsManager 的默认发现覆盖用户配置。
        """
        config = self._agent_config()
        if config is None:
            return []
        merged: Dict[str, ToolInfo] = {}
        for tool in config.tools or []:
            merged[tool.name] = tool
        if self._tools_manager is not None:
            for tool in self._tools_manager.get_all_tools():
                if tool.name not in merged:
                    merged[tool.name] = tool
        return list(merged.values())

    def set_active_tools(self, tool_names: List[str]) -> None:
        """设置激活工具（委托给 ToolsManager）。"""
        if self._tools_manager is None:
            return
        self._tools_manager.set_active_tools(tool_names)

    def get_active_tool_names(self) -> List[str]:
        """返回当前激活工具名称。"""
        if self._tools_manager is None:
            return []
        return self._tools_manager.get_active_tools()

    def get_available_tool_names(self) -> List[str]:
        """返回当前可用工具名称。"""
        if self._tools_manager is None:
            return []
        return self._tools_manager.get_available_tools()

    def get_default_active_tool_names(self) -> Optional[List[str]]:
        """当前 agent 的默认激活工具：yaml ``tools`` 名单原文（三态）。

        ``None``（未声明）= 注册表全部；``[]`` = 显式不激活。
        """
        config = self.get_agent_config()
        if config is None or config.tools is None:
            return None
        return [t.name for t in config.tools]

    def _delegation_menu(self, active_tools: List[str]) -> List[Dict[str, str]]:
        """可委派 agent 菜单（``# Available Agents`` 段数据）。

        仅当激活工具含 ``subagent`` 时注入（模型不再靠报错学名单）；
        数据由 ``AgentManager`` 提供（全部注册 agent + source 标签）。
        """
        if "subagent" not in active_tools:
            return []
        return self._agent_manager.delegation_menu()

    def _collect_build_options(
        self,
        context: Optional[DynamicContext] = None,
    ) -> Dict[str, Any]:
        """收集构建 system prompt 时使用的选项，供缓存与扩展查询。"""
        config = self._agent_config()
        if config is None:
            merged_config = AgentConfig(name=self.agent_name, agent_dir="")
        else:
            update: Dict[str, Any] = {"tools": self._all_tool_infos()}
            if self._persona_manager is not None:
                # persona 装配归 PersonaManager（override 优先）；loader 只做解析
                sections, _diagnostics = self._persona_manager.assemble(config)
                update["sections"] = sections
            merged_config = config.model_copy(update=update)

        active_tools: List[str] = []
        if self._tools_manager is not None:
            active_tools = self._tools_manager.get_active_tools()

        # 按当前 agent 的 skills 白名单过滤
        skills = list(
            self._filter_skills_by_config(
                self._resource_loader.get_skills().get("skills", {})
            ).values()
        )

        tool_snippets: Dict[str, str] = {}
        prompt_guidelines: List[str] = []
        for name, definition in self._tool_definitions().items():
            if name not in active_tools:
                continue
            if definition.prompt_snippet:
                tool_snippets[name] = definition.prompt_snippet
            if definition.prompt_guidelines:
                prompt_guidelines.extend(definition.prompt_guidelines)

        context_files: List[Any] = []
        if hasattr(self._resource_loader, "get_context_files"):
            try:
                raw = self._resource_loader.get_context_files()
                if isinstance(raw, list):
                    context_files = raw
            except Exception:
                context_files = []

        return {
            "cwd": context.cwd if context else "",
            "skills": skills,
            "context_files": context_files,
            "selected_tools": active_tools,
            "tool_snippets": tool_snippets,
            "prompt_guidelines": prompt_guidelines,
            "agent_config": merged_config,
            "delegation_menu": self._delegation_menu(active_tools),
        }

    def build_system_prompt(
        self,
        context: Optional[DynamicContext] = None,
    ) -> str:
        """基于当前配置、激活工具和扩展工具构建系统提示词。"""
        options = self._collect_build_options(context)
        return compose_system_prompt(
            config=options["agent_config"],
            context=context,
            selected_tools=options["selected_tools"],
            skills=options["skills"],
            tool_definitions=self._tool_definitions(),
            context_files=options["context_files"],
            delegation_menu=options["delegation_menu"],
        )

    def build_system_prompt_options(
        self,
        context: Optional[DynamicContext] = None,
    ) -> Dict[str, Any]:
        """返回最近一次（或当前）构建 system prompt 时使用的选项。"""
        return self._collect_build_options(context)
