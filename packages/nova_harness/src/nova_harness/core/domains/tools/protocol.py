"""工具管理器消费接口。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Protocol, Set

if TYPE_CHECKING:
    from nova_agent import AgentTool

    from nova_harness.extensions.protocol import ExtensionRunnerProtocol
    from nova_harness.resources.selection import CapabilitySelection
    from nova_harness.resources.tools import (
        ToolContextProvider,
        ToolDefinition,
        ToolInfo,
    )


class ToolsManagerProtocol(Protocol):
    """供 ``SystemPromptManager`` 与 ``AgentSession`` 使用的工具管理能力。"""

    extension_runner: Optional[ExtensionRunnerProtocol]
    agent_name: Optional[str]
    base_tools_override: Optional[Dict[str, AgentTool]]
    custom_tools: Optional[List[ToolDefinition]]
    allowed_tool_names: Optional[Set[str]]
    excluded_tool_names: Optional[Set[str]]
    settings_tool_patterns: Optional[List[str]]
    role_boundary: str

    @property
    def tool_definitions(self) -> Dict[str, ToolDefinition]: ...

    @property
    def selection_report(self) -> List[CapabilitySelection]: ...

    def set_active_tools(self, tool_names: List[str]) -> None: ...
    def get_active_tools(self) -> List[str]: ...
    def get_available_tools(self) -> List[str]: ...
    def get_all_tools(self) -> List[ToolInfo]: ...
    def get_tool(self, name: str) -> Optional[AgentTool]: ...
    def get_tool_definition(self, name: str) -> Optional[ToolDefinition]: ...
    def refresh(
        self,
        active_tool_names: Optional[List[str]] = None,
        context_provider: Optional[ToolContextProvider] = None,
    ) -> None: ...


__all__ = ["ToolsManagerProtocol"]
