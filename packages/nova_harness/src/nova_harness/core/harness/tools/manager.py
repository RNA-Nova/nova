"""ToolsManager — 统一管理工具发现、过滤、激活与元数据。

把原本分散在 ``ToolController`` 和 ``SystemPromptManager`` 中的工具管理逻辑
集中到这里，成为 AgentSession 内部工具相关能力的单一数据源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set

from nova_agent import AgentTool

from nova_harness.core.harness.tools.dynamic_tool import DynamicTool
from nova_harness.core.types.agent.config import ToolInfo
from nova_harness.core.types.protocols import (
    ExtensionRunnerProtocol,
    ResourceLoaderProtocol,
)
from nova_harness.core.types.runtime.tools import ToolDefinition

DEFAULT_ACTIVE_TOOLS = ["read", "bash", "edit", "write"]


def _tool_source_info(tool: AgentTool) -> Dict[str, Optional[str]]:
    """从工具对象提取来源信息。"""
    definition = getattr(tool, "_definition", None)
    if isinstance(definition, ToolDefinition):
        if definition.source_info is not None:
            return {
                "source": definition.source_info.source,
                "source_path": definition.source_info.path,
            }
        if definition.tool_dir:
            return {"source": "package", "source_path": definition.tool_dir}
        if definition.executor_path:
            return {"source": "package", "source_path": definition.executor_path}
    return {"source": "extension", "source_path": None}


@dataclass
class ToolsManager:
    """
    工具管理中心。

    职责：
    1. 发现候选工具：包管理工具 + 扩展工具 + 调用方覆盖
    2. 应用多层过滤：agent 配置白名单、用户 allowlist、用户 denylist
    3. 维护实际 ``AgentTool`` 注册表与 ``ToolDefinition`` 元数据
    4. 决定并暴露当前激活工具集合
    5. 为 system prompt 渲染提供完整的 ``ToolInfo``
    """

    resource_loader: ResourceLoaderProtocol
    extension_runner: Optional[ExtensionRunnerProtocol] = None
    base_tools_override: Optional[Dict[str, AgentTool]] = None
    custom_tools: Optional[List[ToolDefinition]] = None
    allowed_tool_names: Optional[Set[str]] = None
    excluded_tool_names: Optional[Set[str]] = None
    default_active_tools: Optional[List[str]] = None
    no_tools: Optional[Literal["all", "builtin"]] = None
    agent_name: Optional[str] = None

    _tool_registry: Dict[str, AgentTool] = field(default_factory=dict, init=False)
    _tool_definitions: Dict[str, ToolDefinition] = field(
        default_factory=dict, init=False
    )
    _active_tools: List[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.allowed_tool_names is not None:
            self.allowed_tool_names = set(self.allowed_tool_names)
        if self.excluded_tool_names is not None:
            self.excluded_tool_names = set(self.excluded_tool_names)

    # -------------------------------------------------------------------------
    # Discovery & filtering
    # -------------------------------------------------------------------------

    def _discover_candidate_tools(self) -> Dict[str, AgentTool]:
        """发现所有候选工具（未过滤）。"""
        candidates: Dict[str, AgentTool] = {}

        # 1) 包管理器安装的工具
        package_tools = self.resource_loader.get_tools().get("tools", {})
        if package_tools:
            candidates.update(package_tools)

        # 2) custom tools（由 SDK/调用方直接传入的 ToolDefinition）
        if self.custom_tools:
            for definition in self.custom_tools:
                candidates[definition.name] = DynamicTool(definition)

        # 3) 调用方显式覆盖最高优先级
        if self.base_tools_override:
            candidates.update(self.base_tools_override)

        return candidates

    def _is_allowed(self, name: str, agent_available_names: Set[str]) -> bool:
        """判断工具名是否应被纳入注册表。"""
        # 用户层 denylist
        if self.excluded_tool_names is not None and name in self.excluded_tool_names:
            return False

        # 用户层 allowlist
        if self.allowed_tool_names is not None and name not in self.allowed_tool_names:
            return False

        # agent 配置白名单：若 agent 配置显式声明了 tools，则只保留其中的工具
        if agent_available_names and name not in agent_available_names:
            return False

        return True

    def _agent_available_tool_names(self) -> Set[str]:
        """从当前 agent 配置读取可用工具名集合。"""
        config = self._agent_config()
        if config is None:
            return set()
        return {t.name for t in config.tools}

    def _agent_config(self) -> Optional[Any]:
        """尝试从 ResourceLoader 获取当前 agent 配置。"""
        if not hasattr(self.resource_loader, "get_agents"):
            return None
        agents = self.resource_loader.get_agents()
        # MagicMock 可能真值判断为 True，因此显式检查是否为非空 dict
        if not isinstance(agents, dict) or not agents:
            return None
        # 优先使用当前 agent 名称；未指定时回退到第一个 agent
        if self.agent_name is not None:
            return agents.get(self.agent_name)
        return next(iter(agents.values()))

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def refresh(
        self,
        active_tool_names: Optional[List[str]] = None,
    ) -> None:
        """重建工具注册表、元数据与激活集合。"""
        agent_available = self._agent_available_tool_names()
        candidates = self._discover_candidate_tools()

        # 应用过滤
        registry: Dict[str, AgentTool] = {}
        definitions: Dict[str, ToolDefinition] = {}
        for name, tool in candidates.items():
            if not self._is_allowed(name, agent_available):
                continue
            registry[name] = tool
            definition = getattr(tool, "_definition", None)
            if isinstance(definition, ToolDefinition):
                definitions[name] = definition

        self._tool_registry = registry
        self._tool_definitions = definitions

        # 决定激活工具
        self._decide_active_tools(
            active_tool_names=active_tool_names,
        )

    def _decide_active_tools(
        self,
        active_tool_names: Optional[List[str]] = None,
    ) -> None:
        """根据注册表和调用方偏好决定激活工具。"""
        registry = self._tool_registry

        # 若调用方显式指定，优先使用
        if active_tool_names is not None:
            self._active_tools = [n for n in active_tool_names if n in registry]
            return

        # no_tools="all"：初始不激活任何工具
        if self.no_tools == "all":
            self._active_tools = []
            return

        # 决定默认激活工具
        default_candidates = self.default_active_tools
        if default_candidates is None:
            # 未指定时，优先使用 DEFAULT_ACTIVE_TOOLS 与注册表的交集；
            # 交集为空时回退到注册表中所有工具
            default_candidates = DEFAULT_ACTIVE_TOOLS

        if self.no_tools == "builtin":
            # 禁用默认内置工具，但保留 custom 工具
            default_candidates = []

        defaults = [n for n in default_candidates if n in registry]
        active = defaults if defaults else list(registry.keys())

        # 若允许包含所有 custom 工具，把 custom 工具也加入激活列表
        if self.custom_tools:
            for tool in (DynamicTool(d) for d in self.custom_tools):
                if tool.name in registry and tool.name not in active:
                    active.append(tool.name)

        self._active_tools = active

    def set_active_tools(self, tool_names: List[str]) -> None:
        """按名称设置激活工具（自动过滤掉不在注册表中的名称）。"""
        self._active_tools = [n for n in tool_names if n in self._tool_registry]

    def get_active_tools(self) -> List[str]:
        """返回当前激活工具名称列表。"""
        return list(self._active_tools)

    def get_available_tools(self) -> List[str]:
        """返回注册表中所有可用工具名称列表。"""
        return list(self._tool_registry.keys())

    def get_tool(self, name: str) -> Optional[AgentTool]:
        """按名称返回工具实例。"""
        return self._tool_registry.get(name)

    def get_tool_definition(self, name: str) -> Optional[ToolDefinition]:
        """按名称返回工具定义。"""
        return self._tool_definitions.get(name)

    def get_all_tools(self) -> List[ToolInfo]:
        """返回所有可用工具的完整 ``ToolInfo`` 列表。"""
        tools: List[ToolInfo] = []
        seen: Set[str] = set()
        for name, tool in self._tool_registry.items():
            if name in seen:
                continue
            seen.add(name)

            definition = self._tool_definitions.get(name)
            source_info = _tool_source_info(tool)

            tools.append(
                ToolInfo(
                    name=name,
                    description=getattr(tool, "description", "") or "",
                    parameters=getattr(tool, "parameters", None)
                    or (definition.parameters if definition else None),
                    prompt_snippet=definition.prompt_snippet if definition else None,
                    prompt_guidelines=(
                        definition.prompt_guidelines if definition else None
                    ),
                    source=source_info.get("source"),
                    source_path=source_info.get("source_path"),
                )
            )
        return tools

    @property
    def tool_registry(self) -> Dict[str, AgentTool]:
        """返回当前工具注册表副本。"""
        return dict(self._tool_registry)

    @property
    def tool_definitions(self) -> Dict[str, ToolDefinition]:
        return dict(self._tool_definitions)


__all__ = ["ToolsManager", "DEFAULT_ACTIVE_TOOLS"]
