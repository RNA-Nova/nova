"""核心服务依赖的 Protocol 定义。

本模块用 ``typing.Protocol`` 描述 AgentSession、资源加载器、包解析器等组件
需要的外部服务能力，避免在类型注解中使用 ``Any``，同时不引入运行时的循环导入。

所有 Protocol 只声明方法/属性契约，不包含实现。具体实现类（如
``SettingsManager``、``ModelRuntime``、``DefaultResourceLoader``）只要具备对应
成员即可被类型检查器接受。
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Set,
)

from nova_ai import Model
from nova_ai.types.auth import Credential

if TYPE_CHECKING:
    # 类型检查时才导入具体类型，避免运行时的循环导入。
    from nova_harness.core.types.config.settings import Settings
    from nova_harness.core.types.extensions import (
        Extension,
        ExtensionRuntime,
        LoadedExtensionsResult,
    )
    from nova_harness.core.types.package import (
        MissingSourceAction,
        PackageMetadata,
        ResolvedPaths,
    )
    from nova_harness.core.types.resources.context_files import ContextFile
    from nova_harness.core.types.resources.extension_paths import ResourceExtensionPaths
    from nova_harness.core.types.resources.prompts import PromptTemplate
    from nova_harness.core.types.resources.skills import Skill
    from nova_harness.core.types.resources.tools import (
        ToolContextProvider,
        ToolDefinition,
        ToolExecContext,
    )
else:
    # 运行时用 Any 占位；Protocol 的注解在 ``from __future__ import annotations`` 下
    # 不会被执行，因此实际类型只在类型检查器视角中生效。
    Settings = Any  # type: ignore[misc, assignment]
    Extension = Any  # type: ignore[misc, assignment]
    ExtensionRuntime = Any  # type: ignore[misc, assignment]
    LoadedExtensionsResult = Any  # type: ignore[misc, assignment]
    MissingSourceAction = Any  # type: ignore[misc, assignment]
    PackageMetadata = Any  # type: ignore[misc, assignment]
    ResolvedPaths = Any  # type: ignore[misc, assignment]
    ContextFile = Any  # type: ignore[misc, assignment]
    ResourceExtensionPaths = Any  # type: ignore[misc, assignment]
    PromptTemplate = Any  # type: ignore[misc, assignment]
    ToolDefinition = Any  # type: ignore[misc, assignment]
    Skill = Any  # type: ignore[misc, assignment]


class SettingsReaderProtocol(Protocol):
    """只读 settings 访问能力。"""

    def is_project_trusted(self) -> bool: ...
    def get_project_settings(self) -> "Settings": ...
    def get_global_settings(self) -> "Settings": ...


class SettingsManagerProtocol(SettingsReaderProtocol, Protocol):
    """``SettingsManager`` 的 Protocol 版本，支持读写项目信任状态与重载。"""

    def set_project_trusted(self, value: bool) -> None: ...
    def reload(self) -> None: ...
    def get_compaction_settings(self) -> Any: ...
    def get_branch_summary_settings(self) -> Any: ...
    def get_retry_settings(self) -> Any: ...
    def get_shell_command_prefix(self) -> Optional[str]: ...
    def get_shell_path(self) -> Optional[str]: ...
    def set_default_model_and_provider(self, provider: str, model_id: str) -> None: ...
    def set_default_thinking_level(self, level: Any) -> None: ...
    def get_default_thinking_level(self) -> Any: ...
    def set_compaction_enabled(self, enabled: bool) -> None: ...
    def set_retry_enabled(self, enabled: bool) -> None: ...


class AuthStorageProtocol(Protocol):
    """鉴权信息存储能力（CredentialStore + runtime overrides + 同步判定原语）。"""

    async def read(self, provider_id: str) -> Optional[Any]: ...
    async def list(self) -> List[Any]: ...
    async def modify(self, provider_id: str, fn: Any) -> Optional[Any]: ...
    async def delete(self, provider_id: str) -> None: ...
    def set_runtime_api_key(self, provider: str, api_key: str) -> None: ...
    def remove_runtime_api_key(self, provider: str) -> None: ...
    def has_runtime_api_key(self, provider: str) -> bool: ...
    def has(self, provider: str) -> bool: ...
    def has_auth(self, provider: str) -> bool: ...
    def reload(self) -> None: ...


class ModelRuntimeProtocol(Protocol):
    """模型运行时能力：鉴权解析、流式调用、可用性快照与动态 provider 注册。"""

    async def get_api_key(self, model: Model) -> Optional[str]: ...
    async def get_request_auth(self, provider_or_model: Any) -> Optional[Any]: ...
    def is_using_oauth(self, provider_id: str) -> bool: ...
    def stream_simple(self, model: Model, context: Any, options: Any = None) -> Any: ...
    def has_configured_auth(self, model: Model) -> bool: ...
    async def refresh(self, signal: Any = None) -> Any: ...
    async def refresh_availability(self) -> None: ...
    async def login(
        self, provider_id: str, auth_type: Any, interaction: Any
    ) -> Any: ...
    async def logout(self, provider_id: str) -> None: ...
    def register_provider(self, name: str, config: Any) -> None: ...
    def unregister_provider(self, name: str) -> None: ...
    def get_all(self) -> List[Model]: ...
    def get_available_snapshot(self) -> List[Model]: ...
    async def get_available(self, provider_id: Optional[str] = None) -> List[Model]: ...
    def find(self, provider: str, model_id: str) -> Optional[Model]: ...


class EventBusProtocol(Protocol):
    """事件总线能力，用于扩展间异步/同步事件分发。"""

    def emit(self, event_type: str, payload: Any) -> None: ...


class PackageOperationsProtocol(Protocol):
    """包管理器安装/列表能力，供 ``PackageResolver`` 在缺失包时回调使用。"""

    def install(self, source: str) -> Any: ...
    def list(self) -> List[PackageMetadata]: ...


ExtensionAPIFactory = Callable[[Extension, ExtensionRuntime], Any]
"""扩展 API 工厂类型：根据扩展对象与运行时构造一个扩展 API 实例。"""


class PackageManagerProtocol(PackageOperationsProtocol, Protocol):
    """统一包管理器能力：安装/列表 + 运行时资源解析。"""

    async def resolve_resources(
        self,
        *,
        install_missing_packages: Optional[bool] = None,
    ) -> ResolvedPaths: ...


class ResourceLoaderProtocol(Protocol):
    """资源加载器能力，覆盖 prompts / extensions / agents / skills / tools 等。"""

    @property
    def event_bus(self) -> EventBusProtocol: ...

    async def reload(
        self, pre_trust_extensions: Optional[LoadedExtensionsResult] = None
    ) -> None: ...

    def extend_resources(self, paths: ResourceExtensionPaths) -> None: ...

    def get_prompts(self) -> Dict[str, Any]: ...

    def get_extensions(self) -> LoadedExtensionsResult: ...

    def get_disabled_extension_names(self) -> Set[str]: ...

    def get_agents(self) -> Dict[str, Any]: ...

    def get_agent_names(self) -> List[str]: ...

    def get_skills(self) -> Dict[str, Skill]: ...

    def get_personas(self) -> Dict[str, Any]: ...

    def get_tools(self) -> Dict[str, Any]: ...

    def get_context_files(self) -> List[ContextFile]: ...


class ToolsManagerProtocol(Protocol):
    """``ToolsManager`` 的 Protocol 版本，供 ``SystemPromptManager`` 与 ``AgentSession`` 使用。"""

    @property
    def tool_definitions(self) -> Dict[str, ToolDefinition]: ...

    def set_active_tools(self, tool_names: List[str]) -> None: ...
    def get_active_tools(self) -> List[str]: ...
    def get_available_tools(self) -> List[str]: ...
    def get_all_tools(self) -> List[Any]: ...
    def refresh(
        self,
        active_tool_names: Optional[List[str]] = None,
        context_provider: Optional[ToolContextProvider] = None,
    ) -> None: ...


class SystemPromptManagerProtocol(Protocol):
    """系统提示词管理器能力（纯渲染——当前角色旋钮归 AgentManager）。"""

    def build_system_prompt(self, context: Optional[Any] = None) -> str: ...

    def build_system_prompt_options(
        self, context: Optional[Any] = None
    ) -> Dict[str, Any]: ...

    def set_active_tools(self, tool_names: List[str]) -> None: ...


class SessionManagerProtocol(Protocol):
    """会话管理器能力，覆盖 AgentSession 与控制器实际调用的方法子集。"""

    def get_cwd(self) -> str: ...
    def get_session_dir(self) -> str: ...
    def get_session_id(self) -> str: ...
    def get_session_file(self) -> Optional[str]: ...
    def get_session_name(self) -> Optional[str]: ...
    def is_persisted(self) -> bool: ...

    def append_message(self, message: Any) -> str: ...
    def append_model_change(self, provider: str, model_id: str) -> str: ...
    def append_thinking_level_change(
        self, thinking_level: Optional[Any] = None
    ) -> str: ...
    def append_custom_entry(
        self, custom_type: str, data: Optional[Any] = None
    ) -> str: ...
    def append_custom_message_entry(
        self,
        custom_type: str,
        content: Any,
        display: bool = True,
        details: Optional[Any] = None,
    ) -> str: ...
    def append_session_info(self, name: str) -> str: ...
    def append_label_change(self, target_id: str, label: Optional[str]) -> str: ...
    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Optional[Any] = None,
        from_hook: Optional[bool] = None,
    ) -> Any: ...  # 返回落盘的 CompactionEntry

    def uses_default_session_dir(self) -> bool: ...

    def get_leaf_id(self) -> Optional[str]: ...
    def get_leaf_entry(self) -> Optional[Any]: ...
    def get_entry(self, entry_id: str) -> Optional[Any]: ...
    def get_entries(self) -> List[Any]: ...
    def get_children(self, parent_id: str) -> List[Any]: ...
    def get_label(self, entry_id: str) -> Optional[str]: ...
    def get_branch(self, from_id: Optional[str] = None) -> List[Any]: ...
    def get_tree(self) -> List[Any]: ...
    def build_context_entries(self) -> List[Any]: ...
    def build_session_context(self) -> Any: ...
    def get_header(self) -> Optional[Any]: ...

    def branch(self, branch_from_id: str) -> None: ...
    def reset_leaf(self) -> None: ...
    def branch_with_summary(
        self,
        branch_from_id: Optional[str],
        summary: str,
        details: Optional[Any] = None,
        from_hook: Optional[bool] = None,
    ) -> str: ...
    def create_branched_session(self, leaf_id: str) -> Optional[str]: ...
    def new_session(
        self,
        session_id: Optional[str] = None,
        parent_session: Optional[str] = None,
    ) -> Optional[str]: ...
    def set_session_file(self, session_file: str) -> None: ...


class ExtensionRunnerProtocol(Protocol):
    """扩展运行器能力，供控制器与 ``AgentSession`` 使用。"""

    project_trusted: Optional[bool]

    def get_command(self, name: str) -> Optional[Any]: ...
    def get_registered_commands(self) -> List[Any]: ...
    def create_command_context(self, extension: Optional[Any] = None) -> Any: ...
    def has_handlers(self, event_type: str) -> bool: ...
    async def emit(self, event: Any) -> Any: ...
    async def emit_model_select(self, event: Any) -> Any: ...
    async def emit_thinking_level_select(self, event: Any) -> Any: ...
    async def emit_message_end(self, event: Any) -> Any: ...
    async def emit_tool_call(self, event: Any) -> Any: ...
    async def emit_tool_result(self, event: Any) -> Any: ...
    async def emit_user_bash(self, event: Any) -> Any: ...
    async def emit_resources_discover(
        self, cwd: str, reason: str = "startup"
    ) -> Any: ...
    def on_error(self, listener: Callable[[Any], None]) -> Callable[[], None]: ...
    def emit_error(self, error: Any) -> None: ...
    def invalidate(self, message: Optional[str] = None) -> None: ...
    def get_flag_values(self) -> Dict[str, Any]: ...
    def set_ui_context(self, ui_context: Optional[Any] = None) -> None: ...
    def bind_command_context(self, actions: Optional[Any] = None) -> None: ...
    def bind_core(
        self,
        actions: Any,
        context_actions: Any,
        provider_actions: Optional[Any] = None,
    ) -> None: ...


class AgentSessionProtocol(Protocol):
    """供控制器与 ``AgentSessionRuntime`` 使用的 AgentSession 能力子集。"""

    agent: Any
    session_manager: SessionManagerProtocol
    settings_manager: SettingsManagerProtocol
    model_runtime: ModelRuntimeProtocol
    resource_loader: ResourceLoaderProtocol
    tools_manager: Optional[ToolsManagerProtocol]
    system_prompt_manager: Optional[SystemPromptManagerProtocol]
    agent_manager: Any
    extension_runner: Optional[ExtensionRunnerProtocol]
    cwd: str
    model: Optional[Any]
    thinking_level: Optional[Any]
    is_streaming: bool
    messages: List[Any]
    session_id: str
    session_file: Optional[str]
    scoped_models: List[Any]
    initial_active_tool_names: Optional[List[str]]
    base_tools_override: Optional[Dict[str, Any]]
    custom_tools: Optional[List[Any]]
    allowed_tool_names: Optional[Set[str]]
    excluded_tool_names: Optional[Set[str]]

    # 内部状态（控制器需要直接访问）
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

    # 内部方法（控制器调用）
    def _emit(self, event: Any) -> None: ...
    def _disconnect_from_agent(self) -> None: ...
    def _reconnect_to_agent(self) -> None: ...
    def _sync_system_prompt(self) -> None: ...
    def set_active_tools_by_name(self, tool_names: List[str]) -> None: ...
    def get_tool_exec_context(self) -> ToolExecContext: ...


class AgentSessionServicesProtocol(Protocol):
    """``AgentSessionServices`` 的 Protocol 版本，描述 cwd 绑定服务集合。"""

    cwd: str
    agent_dir: str
    settings_manager: SettingsManagerProtocol
    model_runtime: ModelRuntimeProtocol
    resource_loader: ResourceLoaderProtocol
    auth_storage: AuthStorageProtocol
    diagnostics: List[Any]


ResolveProjectTrustCallback = Callable[[LoadedExtensionsResult], Awaitable[bool]]
"""Project Trust 决议回调类型：接收预加载的扩展结果，返回是否信任当前项目。"""


__all__ = [
    "AgentSessionProtocol",
    "AgentSessionServicesProtocol",
    "AuthStorageProtocol",
    "EventBusProtocol",
    "ExtensionAPIFactory",
    "ExtensionRunnerProtocol",
    "ModelRuntimeProtocol",
    "PackageManagerProtocol",
    "PackageOperationsProtocol",
    "ResolveProjectTrustCallback",
    "ResourceLoaderProtocol",
    "SessionManagerProtocol",
    "SettingsManagerProtocol",
    "SettingsReaderProtocol",
    "SystemPromptManagerProtocol",
    "ToolsManagerProtocol",
]
