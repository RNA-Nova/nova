"""工具注册表控制。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from nova_agent import AgentTool

from nova_harness.core.types.tools import DynamicTool

if TYPE_CHECKING:
    from nova_harness.core.agent_session.agent import AgentSession


class ToolController:
    """封装 AgentSession 的工具注册表管理。"""

    def __init__(self, session: "AgentSession") -> None:
        self._session = session

    def refresh_registry(
        self,
        active_tool_names: Optional[List[str]] = None,
        include_all_extension_tools: bool = True,
    ) -> None:
        """根据 ResourceLoader、扩展工具、调用方覆盖与激活白名单重建工具注册表。"""
        registry: Dict[str, AgentTool] = {}

        # 1) 包管理器安装的工具（由 ResourceLoader 统一加载）
        package_tools = self._session.resource_loader.get_tools()
        if package_tools:
            registry.update(package_tools)

        # 2) 扩展工具可覆盖同名包管理工具
        runner = self._session._extension_runner
        if runner is not None:
            for tool in runner.get_extension_tools():
                registry[tool.name] = tool

        # 3) 调用方显式覆盖最高优先级
        if self._session.base_tools_override:
            registry.update(self._session.base_tools_override)

        self._session._tool_registry = registry

        # 收集 ToolDefinition 用于系统提示词渲染 snippet/guidelines
        definitions: Dict[str, Any] = {}
        for name, tool in registry.items():
            if isinstance(tool, DynamicTool):
                definitions[name] = tool._definition
        self._session._tool_definitions = definitions
        spm = self._session.system_prompt_manager
        if hasattr(spm, "set_tool_definitions"):
            spm.set_tool_definitions(list(definitions.values()))

        previous_active_names = set(active_tool_names) if active_tool_names else set()
        if previous_active_names:
            active_names = [n for n in previous_active_names if n in registry]
        elif self._session.base_tools_override:
            active_names = list(self._session.base_tools_override.keys())
        else:
            active_names = [
                n for n in self._session.initial_active_tool_names if n in registry
            ]

        # reload 时若保留全部扩展工具，则把新增的工具也加入激活列表
        if include_all_extension_tools and runner is not None:
            for tool in runner.get_extension_tools():
                if tool.name not in active_names:
                    active_names.append(tool.name)

        self.set_active_by_name(active_names)

    def get_active_names(self) -> List[str]:
        """返回当前激活的工具名称列表。"""
        return [t.name for t in getattr(self._session.agent.state, "tools", [])]

    def get_all_tools(self) -> List[Any]:
        """返回所有可用工具的 ToolInfo 列表。"""
        from nova_harness.core.types.agent_config import ToolInfo

        tools = []
        seen = set()
        for name, tool in self._session._tool_registry.items():
            if name in seen:
                continue
            seen.add(name)
            tools.append(
                ToolInfo(
                    name=name,
                    description=getattr(tool, "description", ""),
                )
            )
        return tools

    def get_definition(self, name: str) -> Optional[Any]:
        """按名称返回工具定义（占位，后续补齐 ToolDefinition）。"""
        return self._session._tool_registry.get(name)

    def refresh(self) -> None:
        """重新扫描扩展工具并刷新工具注册表。"""
        self.refresh_registry(
            active_tool_names=self.get_active_names(),
            include_all_extension_tools=True,
        )

    def set_active_by_name(self, tool_names: List[str]) -> None:
        """按名称设置 Agent 当前激活的工具。"""
        tools: List[AgentTool] = []
        valid_names: List[str] = []
        for name in tool_names:
            tool = self._session._tool_registry.get(name)
            if tool is not None:
                tools.append(tool)
                valid_names.append(name)

        self._session.agent.state.tools = tools
        spm = self._session.system_prompt_manager
        if hasattr(spm, "set_active_tools"):
            spm.set_active_tools(valid_names)
        self._session._sync_system_prompt()
        if valid_names and hasattr(
            self._session.session_manager, "append_active_tools_change"
        ):
            self._session.session_manager.append_active_tools_change(valid_names)
