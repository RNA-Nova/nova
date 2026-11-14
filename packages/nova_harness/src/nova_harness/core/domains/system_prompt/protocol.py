"""系统提示词管理器消费接口。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol

if TYPE_CHECKING:
    from nova_harness.resources.agents import AgentConfig


class SystemPromptManagerProtocol(Protocol):
    """系统提示词纯渲染能力；当前角色旋钮归 AgentManager。"""

    @property
    def persona_manager(self) -> Optional[Any]: ...

    @persona_manager.setter
    def persona_manager(self, value: Optional[Any]) -> None: ...

    def get_agent_config(self) -> Optional[AgentConfig]: ...
    def build_system_prompt(self, context: Optional[Any] = None) -> str: ...
    def build_system_prompt_options(
        self, context: Optional[Any] = None
    ) -> Dict[str, Any]: ...
    def set_active_tools(self, tool_names: List[str]) -> None: ...


__all__ = ["SystemPromptManagerProtocol"]
