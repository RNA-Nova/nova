"""工具注册表控制。"""

from __future__ import annotations

from typing import Any, List, Optional

from nova_harness.core.types.protocols import AgentSessionProtocol, ToolsManagerProtocol


class ToolController:
    """封装 AgentSession 的工具注册表管理，实际逻辑委托给 ToolsManager。"""

    def __init__(self, session: AgentSessionProtocol) -> None:
        self._session = session

    def _tools_manager(self) -> ToolsManagerProtocol:
        manager = self._session.tools_manager
        if manager is None:
            return manager  # type: ignore[return-value]
        # 保持 ToolsManager 与 session 的当前配置一致（测试会替换 runner/override）
        manager.extension_runner = self._session.extension_runner
        manager.base_tools_override = self._session.base_tools_override
        manager.custom_tools = self._session.custom_tools
        allowed = self._session.allowed_tool_names
        manager.allowed_tool_names = set(allowed) if allowed else None
        excluded = self._session.excluded_tool_names
        manager.excluded_tool_names = set(excluded) if excluded else None
        # settings 层（用户终裁）：tools pattern + role_boundary 注入
        # （保留三态：[] = 全禁，None = 不设防）
        settings_mgr = getattr(self._session, "settings_manager", None)
        settings = settings_mgr.get_settings() if settings_mgr is not None else None
        patterns = getattr(settings, "tools", None) if settings is not None else None
        manager.settings_tool_patterns = (
            list(patterns) if patterns is not None else None
        )
        role_boundary = (
            getattr(settings, "role_boundary", None) if settings is not None else None
        )
        manager.role_boundary = role_boundary or "open"
        return manager

    def refresh_registry(
        self,
        active_tool_names: Optional[List[str]] = None,
    ) -> None:
        """重建工具注册表与激活集合。

        ``active_tool_names=None`` 时使用初始三态：仍为 None 则让
        ToolsManager 默认（激活注册表全部）；``[]`` 表示显式不激活。
        """
        if active_tool_names is None:
            active_tool_names = self._session.initial_active_tool_names
        manager = self._tools_manager()
        manager.refresh(
            active_tool_names=active_tool_names,
            context_provider=self._session.get_tool_exec_context,
        )
        # 激活工具必须同步进 Agent.state.tools——否则 LLM 收不到工具定义
        # （此前只有 set_active_by_name 会同步，启动路径漏同步，模型只能
        # "口述"工具调用）
        self._sync_to_agent()

    def get_active_names(self) -> List[str]:
        """返回当前激活的工具名称列表。"""
        return self._tools_manager().get_active_tools()

    def get_all_tools(self) -> List[Any]:
        """返回所有可用工具的 ToolInfo 列表。"""
        return self._tools_manager().get_all_tools()

    def get_definition(self, name: str) -> Optional[Any]:
        """按名称返回工具实例。"""
        return self._tools_manager().get_tool(name)

    def refresh(self) -> None:
        """重新扫描工具并刷新工具注册表。"""
        self.refresh_registry(
            active_tool_names=self.get_active_names(),
        )

    def set_active_by_name(self, tool_names: List[str]) -> None:
        """按名称设置 Agent 当前激活的工具。"""
        manager = self._tools_manager()
        manager.set_active_tools(tool_names)
        valid_names = self._sync_to_agent()
        self._session.system_prompt_manager.set_active_tools(valid_names)
        self._session._sync_system_prompt()

    def _sync_to_agent(self) -> List[str]:
        """把激活工具同步到 Agent.state.tools，返回有效工具名列表。"""
        manager = self._tools_manager()
        active_names = manager.get_active_tools()
        tools = []
        valid_names: List[str] = []
        for name in active_names:
            tool = manager.get_tool(name)
            if tool is not None:
                tools.append(tool)
                valid_names.append(name)

        self._session.agent.state.tools = tools
        return valid_names
