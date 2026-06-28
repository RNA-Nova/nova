"""
SystemPromptManager — 负责 agent 切换、工具选择和系统提示词构建。

它从 ResourceLoader 读取静态 Agent 配置，运行时维护当前选中的 agent 和工具白名单，
并把最终系统提示词构建委托给 SystemPromptBuilder。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from nova_harness.core.types.agent_config import AgentConfig, DynamicContext, ToolInfo
from nova_harness.core.types.tools import ToolDefinition

if TYPE_CHECKING:
    from nova_harness.core.resources.loader import ResourceLoader

from nova_harness.core.harness.system_prompt.builder import compose_system_prompt
from nova_harness.core.utils.skills import format_skills_for_prompt

DEFAULT_ACTIVE_TOOLS = ["read", "bash", "edit", "write"]


class SystemPromptManager:
    """
    管理当前 Agent 配置、激活工具集和扩展工具，构建系统提示词。
    """

    def __init__(
        self,
        resource_loader: ResourceLoader,
        agent_name: str,
    ) -> None:
        self._resource_loader = resource_loader
        self._agent_name = agent_name
        self._active_tools: List[str] = []
        self._extension_tools: List[ToolInfo] = []
        self._tool_definitions: Dict[str, ToolDefinition] = {}
        self._reset_active_tools()

    @property
    def agent_name(self) -> str:
        return self._agent_name

    def _agent_config(self) -> Optional[AgentConfig]:
        return self._resource_loader.get_agents().get(self._agent_name)

    def change_agent(self, name: str) -> None:
        """切换当前 agent，重置扩展工具和默认激活工具。"""
        self._agent_name = name
        self._extension_tools = []
        self._reset_active_tools()

    def _reset_active_tools(self) -> None:
        """将激活工具重置为默认值（与可用工具的交集）。"""
        available = self._available_config_tool_names()
        defaults = [t for t in DEFAULT_ACTIVE_TOOLS if t in available]
        self._active_tools = defaults if defaults else available

    def _available_config_tool_names(self) -> List[str]:
        config = self._agent_config()
        if config is None:
            return []
        return [t.name for t in config.tools]

    def set_active_tools(self, tool_names: List[str]) -> None:
        """按可用工具集合过滤后设置激活工具白名单。"""
        available = set(self.get_available_tool_names())
        self._active_tools = [t for t in tool_names if t in available]

    def get_active_tool_names(self) -> List[str]:
        return list(self._active_tools)

    def get_available_tool_names(self) -> List[str]:
        """返回配置工具 + 扩展工具的名称列表。"""
        names = self._available_config_tool_names()
        names.extend(t.name for t in self._extension_tools)
        return names

    def get_default_active_tool_names(self) -> List[str]:
        """返回当前 agent 的默认激活工具（无扩展工具）。"""
        available = self._available_config_tool_names()
        defaults = [t for t in DEFAULT_ACTIVE_TOOLS if t in available]
        return defaults if defaults else available

    def set_extension_tools(self, tools: List[ToolInfo]) -> None:
        """设置由扩展注入的额外工具描述。"""
        self._extension_tools = list(tools)

    def set_tool_definitions(self, definitions: List[ToolDefinition]) -> None:
        """设置实际加载的工具定义，用于渲染 prompt snippet 与 guidelines。"""
        self._tool_definitions = {d.name: d for d in definitions}

    def build_system_prompt(
        self,
        context: Optional[DynamicContext] = None,
    ) -> str:
        """基于当前配置、激活工具和扩展工具构建系统提示词。"""
        config = self._agent_config()
        if config is None:
            merged_config = AgentConfig(name=self._agent_name, agent_dir="")
        else:
            merged_tools = list(config.tools) + self._extension_tools
            merged_config = config.model_copy(update={"tools": merged_tools})

        # 只有当 read 工具可用时才注入 skill 列表（模型需要 read 才能加载 skill 文件）
        has_read_tool = "read" in self._active_tools
        skills = list(self._resource_loader.get_skills().values())
        skills_append = format_skills_for_prompt(skills, has_read_tool)

        return compose_system_prompt(
            config=merged_config,
            context=context,
            selected_tools=self._active_tools,
            append_system_prompt=skills_append or None,
            tool_definitions=self._tool_definitions,
        )
