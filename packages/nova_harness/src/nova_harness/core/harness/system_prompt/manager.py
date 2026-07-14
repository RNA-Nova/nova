"""
SystemPromptManager — 负责 agent 切换与系统提示词构建。

它从 ResourceLoader 读取静态 Agent 配置，并把运行时工具选择委托给
``ToolsManager``，自身只负责渲染 system prompt。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nova_harness.core.harness.system_prompt.builder import compose_system_prompt
from nova_harness.core.types.agent.config import AgentConfig, DynamicContext, ToolInfo
from nova_harness.core.types.protocols import (
    ResourceLoaderProtocol,
    ToolsManagerProtocol,
)
from nova_harness.core.types.runtime.tools import ToolDefinition
from nova_harness.core.types.skills import Skill

DEFAULT_ACTIVE_TOOLS = ["read", "bash", "edit", "write"]


class SystemPromptManager:
    """
    管理当前 Agent 配置并构建系统提示词。

    工具发现、过滤、激活等运行时逻辑已下沉到 ``ToolsManager``；
    本类只负责：
    - 维护当前 agent 名称与配置
    - 调用 ``ToolsManager`` 获取激活工具和工具定义
    - 渲染最终 system prompt
    """

    def __init__(
        self,
        resource_loader: ResourceLoaderProtocol,
        agent_name: str,
        tools_manager: Optional[ToolsManagerProtocol] = None,
    ) -> None:
        self._resource_loader = resource_loader
        self._agent_name = agent_name
        self._tools_manager = tools_manager

    @property
    def agent_name(self) -> str:
        return self._agent_name

    def get_agent_config(self) -> Optional[AgentConfig]:
        """返回当前 agent 的静态配置。"""
        return self._resource_loader.get_agents().get(self._agent_name)

    def _agent_config(self) -> Optional[AgentConfig]:
        return self.get_agent_config()

    def change_agent(self, name: str) -> None:
        """切换当前 agent。"""
        self._agent_name = name

    def _filter_skills_by_config(self, skills: Dict[str, Any]) -> Dict[str, Any]:
        """按当前 agent 的 skills 白名单过滤 skill。"""
        config = self.get_agent_config()
        if config is None:
            return {}
        allowed = set(config.skills)
        if not allowed:
            return {}
        return {name: skill for name, skill in skills.items() if name in allowed}

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
        for tool in config.tools:
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

    def get_default_active_tool_names(self) -> List[str]:
        """返回当前 agent 的默认激活工具（无扩展工具）。"""
        config = self.get_agent_config()
        if config is None:
            return []
        available = {t.name for t in config.tools}
        defaults = [t for t in DEFAULT_ACTIVE_TOOLS if t in available]
        return defaults if defaults else list(available)

    def _collect_build_options(
        self,
        context: Optional[DynamicContext] = None,
    ) -> Dict[str, Any]:
        """收集构建 system prompt 时使用的选项，供缓存与扩展查询。"""
        config = self._agent_config()
        if config is None:
            merged_config = AgentConfig(name=self._agent_name, agent_dir="")
        else:
            merged_config = config.model_copy(
                update={"tools": self._all_tool_infos()}
            )

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
        )

    def build_system_prompt_options(
        self,
        context: Optional[DynamicContext] = None,
    ) -> Dict[str, Any]:
        """返回最近一次（或当前）构建 system prompt 时使用的选项。"""
        return self._collect_build_options(context)
