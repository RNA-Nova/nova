"""AgentSession 运行时的结构化消费接口。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol, Set

from nova_harness.config.auth.protocol import AuthStorageProtocol
from nova_harness.config.protocol import SettingsManagerProtocol
from nova_harness.extensions.protocol import ExtensionRunnerProtocol
from nova_harness.model.protocol import ModelRuntimeProtocol
from nova_harness.resources.protocol import ResourceLoaderProtocol
from nova_harness.sessions.protocol import SessionManagerProtocol

if TYPE_CHECKING:
    from nova_harness.core.domains.system_prompt.protocol import (
        SystemPromptManagerProtocol,
    )
    from nova_harness.core.domains.tools.protocol import ToolsManagerProtocol
    from nova_harness.resources.tools import ToolExecContext


class AgentSessionProtocol(Protocol):
    """供控制器与 ``AgentSessionRuntime`` 使用的 AgentSession 能力子集。"""

    agent: Any
    session_manager: SessionManagerProtocol
    settings_manager: SettingsManagerProtocol

    @property
    def model_runtime(self) -> ModelRuntimeProtocol: ...

    @property
    def resource_loader(self) -> ResourceLoaderProtocol: ...

    tools_manager: Optional[ToolsManagerProtocol]
    system_prompt_manager: Optional[SystemPromptManagerProtocol]

    agent_manager: Any

    @property
    def extension_runner(self) -> Optional[ExtensionRunnerProtocol]: ...

    cwd: str

    @property
    def model(self) -> Optional[Any]: ...

    @property
    def thinking_level(self) -> Any: ...

    @property
    def is_streaming(self) -> bool: ...

    @property
    def messages(self) -> List[Any]: ...

    @property
    def session_id(self) -> str: ...

    @property
    def session_file(self) -> Optional[str]: ...

    scoped_models: List[Any]
    initial_active_tool_names: Optional[List[str]]
    base_tools_override: Optional[Dict[str, Any]]
    custom_tools: Optional[List[Any]]
    allowed_tool_names: Optional[Set[str]]
    excluded_tool_names: Optional[Set[str]]

    # 控制器需要直接访问的内部状态。
    _pending_session_messages: List[Any]
    _compaction_abort_controller: Optional[Any]
    _auto_compaction_abort_controller: Optional[Any]
    _branch_summary_abort_controller: Optional[Any]
    _extension_runner: Optional[ExtensionRunnerProtocol]
    _overflow_recovery_attempted: bool
    _retry_attempt: int
    _retry_abort_event: Optional[Any]
    _steering_messages: List[str]
    _follow_up_messages: List[str]
    _pending_next_turn_messages: List[Any]
    _last_assistant_message: Optional[Any]
    _event_listeners: List[Callable[[Any], None]]
    _retry: Any
    _queue: Any
    _tools: Any
    _compaction: Any
    _current_run_id: Optional[str]

    async def abort(self) -> None: ...
    def _is_command_allowed(self, name: str) -> bool: ...
    def _get_allowed_skills(self) -> Any: ...
    def _emit(self, event: Any) -> None: ...
    def _disconnect_from_agent(self) -> None: ...
    def _reconnect_to_agent(self) -> None: ...
    def _sync_system_prompt(self) -> None: ...
    def set_active_tools_by_name(self, tool_names: List[str]) -> None: ...
    def get_tool_exec_context(self) -> ToolExecContext: ...


class AgentSessionServicesProtocol(Protocol):
    """cwd 绑定的 AgentSession 服务集合。"""

    cwd: str
    agent_dir: str
    settings_manager: SettingsManagerProtocol
    model_runtime: ModelRuntimeProtocol
    resource_loader: ResourceLoaderProtocol
    auth_storage: AuthStorageProtocol
    diagnostics: List[Any]


__all__ = ["AgentSessionProtocol", "AgentSessionServicesProtocol"]
