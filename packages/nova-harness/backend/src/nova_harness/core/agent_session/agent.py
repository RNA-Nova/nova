"""AgentSession - Agent 生命周期与会话管理核心。

负责事件体系、自动重试、自动压缩、模型循环、队列管理、工具元数据等能力。

为降低 ``AgentSession`` 的复杂度，具体的领域逻辑已拆分到
``core/controllers/`` 下的各控制器；``AgentSession`` 负责编排入口、
事件总线与生命周期管理。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple, Union

from nova_agent import (
    AbortController,
    Agent,
    AgentMessage,
    CustomAgentMessage,
    ModelThinkingLevel,
)
from nova_ai import (
    AssistantMessage,
    ImageContent,
    TextContent,
    UserMessage,
)
from nova_harness.core.agent_session.controllers import (
    CompactionController,
    EventController,
    ModelController,
    QueueController,
    RetryController,
    SlashInputHandler,
    StatsCollector,
    ToolController,
    TreeNavigator,
    UserToolController,
)
from nova_harness.core.config.auth.guidance import (
    format_no_auth_message,
    format_no_model_selected_message,
)
from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.extensions import (
    ExtensionRunner,
    emit_session_shutdown_event,
)
from nova_harness.core.harness.agents import AgentManager
from nova_harness.core.harness.persona import PersonaManager
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.harness.skills import (
    expand_skill_command,
    filter_skills_by_whitelist,
)
from nova_harness.core.harness.system_prompt import SystemPromptManager
from nova_harness.core.harness.tools import ToolsManager
from nova_harness.core.harness.user_tools import UserToolManager
from nova_harness.core.resources.loaders.prompt_templates import expand_prompt_template
from nova_harness.core.types.compaction import CompactionResult
from nova_harness.core.types.events import (
    AgentSessionEvent,
    AgentSettledEvent,
    AutoRetryEndEvent,
    BeforeAgentStartEvent,
    EntryAppendedEvent,
    ExtensionErrorEvent,
    MessageEndEvent,
    MessageStartEvent,
    SessionInfoChangedEvent,
    SessionReplacedEvent,
    SessionShutdownEvent,
    SessionStartEvent,
    ToolCallEvent,
    ToolResultEvent,
    TurnEndEvent,
)
from nova_harness.core.types.events.constants import (
    AGENT_SETTLED,
    INPUT,
    RESOURCES_DISCOVER,
    TOOL_CALL,
    TOOL_RESULT,
)
from nova_harness.core.types.extensions import (
    ExecOptions,
    ExecResult,
    ExtensionActions,
    ExtensionCommandContextActions,
    ExtensionContextActions,
    ExtensionProviderActions,
    SlashCommandInfo,
    SourceInfo,
)
from nova_harness.core.types.protocols import (
    ModelRuntimeProtocol,
    ResourceLoaderProtocol,
    SessionManagerProtocol,
    SettingsManagerProtocol,
    SystemPromptManagerProtocol,
    ToolsManagerProtocol,
)
from nova_harness.core.types.resources.extension_paths import (
    ResourceExtensionPathEntry,
    ResourceExtensionPaths,
)
from nova_harness.core.types.resources.selection import CapabilitySelection
from nova_harness.core.types.resources.tools import (
    ToolDefinition,
    ToolExecContext,
)
from nova_harness.core.types.resources.user_tools import UserToolInfo
from nova_harness.core.types.session import (
    NavigateOptions,
    PromptOptions,
    SessionStats,
)
from nova_harness.core.types.session.config import AgentSessionConfig
from nova_harness.core.types.session.model import (
    ModelCycleResult,
    ScopedModelConfig,
)
from nova_harness.core.types.ui import NoOpUIContext, ScopedUIContext, UIContext
from nova_harness.core.utils.messages import extract_text_from_content
from nova_harness.core.utils.name_sets import (
    apply_name_list,
    build_selection_report,
    is_name_allowed,
)

# ============================================================================
# Extension action 具体实现（替代 SimpleNamespace，提供类型安全）
# ============================================================================


# ============================================================================
# AgentSession
# ============================================================================


class AgentSession:
    """Agent 会话核心：状态、事件、模型、工具、压缩、树导航。"""

    # 类层级只做类型标注，不赋可变默认值；实例值在 __init__ 中设置。
    config: AgentSessionConfig = None  # type: ignore[assignment]
    agent: Agent = None  # type: ignore[assignment]
    session_manager: SessionManagerProtocol = None  # type: ignore[assignment]
    settings_manager: SettingsManagerProtocol = None  # type: ignore[assignment]
    cwd: str = ""
    system_prompt_manager: Optional[SystemPromptManagerProtocol] = None
    tools_manager: Optional[ToolsManagerProtocol] = None
    persona_manager: Optional[PersonaManager] = None
    agent_manager: AgentManager = None  # type: ignore[assignment]
    user_tools_manager: UserToolManager = None  # type: ignore[assignment]
    _resource_loader: ResourceLoaderProtocol = None  # type: ignore[assignment]
    _model_runtime: ModelRuntimeProtocol = None  # type: ignore[assignment]
    scoped_models: List[ScopedModelConfig]
    initial_active_tool_names: Optional[List[str]]
    base_tools_override: Optional[Dict[str, Any]] = None
    custom_tools: Optional[List[ToolDefinition]] = None
    allowed_tool_names: Optional[Set[str]] = None
    excluded_tool_names: Optional[Set[str]] = None
    extension_runner_ref: Optional[Dict[str, Optional[Any]]] = None
    session_start_event: SessionStartEvent = None  # type: ignore[assignment]
    _extension_runner: Optional[ExtensionRunner] = None
    _unsubscribe_agent: Optional[Callable[[], None]] = None
    _event_listeners: List[Callable[[AgentSessionEvent], None]]
    _steering_messages: List[str]
    _follow_up_messages: List[str]
    _pending_next_turn_messages: List[AgentMessage]
    _pending_session_messages: List[CustomAgentMessage]
    _last_assistant_message: Optional[AssistantMessage] = None
    _retry_attempt: int = 0
    _retry_abort_event: Optional[AbortController] = None
    _overflow_recovery_attempted: bool = False
    _compaction_abort_controller: Optional[AbortController] = None
    _auto_compaction_abort_controller: Optional[AbortController] = None
    _branch_summary_abort_controller: Optional[AbortController] = None
    _base_system_prompt: str = ""
    _base_system_prompt_options: Dict[str, Any]

    # Extension binding state（用于 reload 后恢复绑定）
    _extension_ui_context: Optional[UIContext] = None
    _extension_command_context_actions: Optional[ExtensionCommandContextActions] = None
    _extension_abort_handler: Optional[Callable[[], None]] = None
    _extension_shutdown_handler: Optional[Callable[[], None]] = None
    _extension_error_listener: Optional[Callable[[ExtensionErrorEvent], None]] = None
    _extension_error_unsubscriber: Optional[Callable[[], None]] = None

    # 领域控制器
    _retry: RetryController = None  # type: ignore[assignment]
    _compaction: CompactionController = None  # type: ignore[assignment]
    _user_tools: UserToolController = None  # type: ignore[assignment]
    _events: EventController = None  # type: ignore[assignment]
    _model: ModelController = None  # type: ignore[assignment]
    _tools: ToolController = None  # type: ignore[assignment]
    _queue: QueueController = None  # type: ignore[assignment]
    _tree: TreeNavigator = None  # type: ignore[assignment]
    _stats: StatsCollector = None  # type: ignore[assignment]
    _slash_handler: SlashInputHandler = None  # type: ignore[assignment]

    def __init__(self, config: AgentSessionConfig) -> None:
        self.config = config
        self.agent: Agent = config.agent
        self.session_manager = config.session_manager
        self.settings_manager = config.settings_manager
        self.cwd: str = config.cwd
        self._resource_loader = config.resource_loader
        self._model_runtime = config.model_runtime
        self.scoped_models: List[ScopedModelConfig] = config.scoped_models or []
        self.initial_active_tool_names: Optional[List[str]] = (
            config.initial_active_tool_names
        )
        self.base_tools_override: Optional[Dict[str, Any]] = config.base_tools_override
        self.custom_tools: Optional[List[ToolDefinition]] = config.custom_tools
        self.allowed_tool_names: Optional[Set[str]] = (
            set(config.allowed_tool_names) if config.allowed_tool_names else None
        )
        self.excluded_tool_names: Optional[Set[str]] = (
            set(config.excluded_tool_names) if config.excluded_tool_names else None
        )

        self.system_prompt_manager = config.system_prompt_manager
        self.tools_manager = config.tools_manager

        self.extension_runner_ref = config.extension_runner_ref
        self.session_start_event: SessionStartEvent = (
            config.session_start_event or SessionStartEvent(reason="new")
        )

        self._runtime: Optional[Any] = None
        self._extension_runner: Optional[ExtensionRunner] = None
        self._unsubscribe_agent: Optional[Callable[[], None]] = None
        self._event_listeners: List[Callable[[AgentSessionEvent], None]] = []

        self._steering_messages: List[str] = []
        self._follow_up_messages: List[str] = []
        self._pending_next_turn_messages: List[AgentMessage] = []
        self._pending_session_messages: List[CustomAgentMessage] = []
        self._last_assistant_message: Optional[AssistantMessage] = None
        self._retry_attempt: int = 0
        self._retry_abort_event: Optional[AbortController] = None
        self._overflow_recovery_attempted: bool = False
        self._compaction_abort_controller: Optional[AbortController] = None
        self._auto_compaction_abort_controller: Optional[AbortController] = None
        self._branch_summary_abort_controller: Optional[AbortController] = None
        self._base_system_prompt: str = ""
        self._base_system_prompt_options: Dict[str, Any] = {}

        self._extension_ui_context: Optional[UIContext] = None
        # 工具执行面的弹窗串行锁：一轮内多个工具调用并行时，UI 请求经同一把
        # 锁排队（ScopedUIContext 承载——见 get_tool_exec_context）。
        self._tool_ui_request_lock: asyncio.Lock = asyncio.Lock()
        self._extension_command_context_actions: Optional[
            ExtensionCommandContextActions
        ] = None
        self._extension_abort_handler: Optional[Callable[[], None]] = None
        self._extension_shutdown_handler: Optional[Callable[[], None]] = None
        self._extension_error_listener: Optional[
            Callable[[ExtensionErrorEvent], None]
        ] = None
        self._extension_error_unsubscriber: Optional[Callable[[], None]] = None

        # 初始化控制器
        self._retry = RetryController(self)
        self._compaction = CompactionController(self)
        # 用户工具：空注册表起步——框架不内置任何用户工具，包级工具在
        # _build_runtime 中按当前 agent 白名单注册（与包工具同一注册路径）
        self.user_tools_manager = UserToolManager()
        self._user_tools = UserToolController(self, self.user_tools_manager)
        # persona 管理器：会话级单例（override 内存态随 change_agent/reload 保留；
        # 注册表为活视图，读 loader 现取，无需手动刷新）
        self.persona_manager = PersonaManager(resource_loader=self._resource_loader)
        # agent 管理器：会话级单例（当前角色旋钮内存态；注册表活视图；
        # tools_manager 在 _build_runtime 中后绑定）
        self.agent_manager = AgentManager(
            resource_loader=self._resource_loader,
            agent_dir=config.agent_dir or str(get_agent_dir()),
            persona_manager=self.persona_manager,
        )
        # CapabilitySelection 报告：各过滤点（extensions/user_tools 在本类
        # 名单过滤处产出，tools 归 ToolsManager.refresh，persona 归
        # PersonaManager 装配）→ _build_runtime 末尾重建统一收集处；
        # AgentManager 经 provider 透出（manager 互不调用，编排在会话）
        self._extensions_selection_report: List[CapabilitySelection] = []
        self._user_tools_selection_report: List[CapabilitySelection] = []
        self._capability_report: List[CapabilitySelection] = []
        self.agent_manager.capability_report_provider = lambda: list(
            self._capability_report
        )
        self._events = EventController(self)
        self._model = ModelController(self)
        self._tools = ToolController(self)
        self._queue = QueueController(self)
        self._tree = TreeNavigator(self)
        self._stats = StatsCollector(self)
        self._slash_handler = SlashInputHandler(self)

        self._subscribe_agent_events()
        self._install_agent_hooks()
        self._build_runtime()
        self.sync_queue_modes_from_settings()

    # -------------------------------------------------------------------------
    # 内部构造
    # -------------------------------------------------------------------------

    def _subscribe_agent_events(self) -> None:
        """订阅底层 Agent 事件，用于持久化与会话生命周期。"""
        if hasattr(self.agent, "subscribe"):
            self._unsubscribe_agent = self.agent.subscribe(self._events.handle)

    def _install_agent_hooks(self) -> None:
        """把扩展 runner 的能力挂到 Agent 的工具/续话钩子上。"""
        if not hasattr(self.agent, "before_tool_call"):
            return

        async def before_tool_call(ctx: Any, signal: Optional[Any] = None) -> Any:
            runner = self._extension_runner
            if runner is None or not runner.has_handlers(TOOL_CALL):
                return None
            tool_call = ctx.tool_call
            result = await runner.emit_tool_call(
                ToolCallEvent(
                    type=TOOL_CALL,
                    tool_call_id=getattr(tool_call, "id", ""),
                    tool_name=getattr(tool_call, "name", ""),
                    args=getattr(ctx, "args", {}),
                )
            )
            if getattr(result, "block", False):
                from nova_agent import BeforeToolCallResult

                return BeforeToolCallResult(
                    block=True, reason=getattr(result, "reason", None)
                )
            return None

        async def after_tool_call(ctx: Any, signal: Optional[Any] = None) -> Any:
            runner = self._extension_runner
            if runner is None or not runner.has_handlers(TOOL_RESULT):
                return None
            tool_call = ctx.tool_call
            result = await runner.emit_tool_result(
                ToolResultEvent(
                    type=TOOL_RESULT,
                    tool_call_id=getattr(tool_call, "id", ""),
                    tool_name=getattr(tool_call, "name", ""),
                    args=getattr(ctx, "args", {}),
                    content=getattr(ctx.result, "content", []),
                    details=getattr(ctx.result, "details", None),
                    is_error=getattr(ctx, "is_error", False),
                )
            )
            if result is None:
                return None
            from nova_agent import AfterToolCallResult

            return AfterToolCallResult(
                content=getattr(result, "content", None),
                details=getattr(result, "details", None),
                is_error=getattr(result, "is_error", None),
            )

        async def prepare_next_turn(ctx: Any, signal: Optional[Any] = None) -> Any:
            runner = self._extension_runner
            if runner is None:
                return None
            event = TurnEndEvent(
                turn_index=getattr(ctx, "turn_index", 0),
                message=getattr(ctx, "message", None),
                tool_results=getattr(ctx, "tool_results", []),
            )
            return await runner.emit_prepare_next_turn(event)

        async def should_stop_after_turn(ctx: Any) -> bool:
            runner = self._extension_runner
            if runner is None:
                return False
            event = TurnEndEvent(
                turn_index=getattr(ctx, "turn_index", 0),
                message=getattr(ctx, "message", None),
                tool_results=getattr(ctx, "tool_results", []),
            )
            # 必须取 .stop——结果对象本身是 dataclass 实例，恒为 truthy，
            # 直接返回会让每个 turn 都被误判为"停止"（工具结果永远送不回 LLM）
            result = await runner.emit_should_stop_after_turn(event)
            return bool(getattr(result, "stop", False))

        async def transform_context(
            messages: List[AgentMessage], signal: Optional[Any] = None
        ) -> List[AgentMessage]:
            runner = self._extension_runner
            if runner is None:
                return messages
            return await runner.emit_context(messages, signal)

        self.agent.before_tool_call = before_tool_call
        self.agent.after_tool_call = after_tool_call
        self.agent.prepare_next_turn = prepare_next_turn
        self.agent.should_stop_after_turn = should_stop_after_turn
        self.agent.transform_context = transform_context

    def _get_allowed_extensions(self, extensions: List[Any]) -> List[Any]:
        """按当前 agent 的 extensions 名单过滤扩展（三态 + ``!`` 排除），
        并产出该域的 CapabilitySelection 报告（暂存待 ``_build_runtime`` 汇集）。

        ``None``（未声明）= 全部允许；``[]`` = 全禁；名单 = 仅列名（可 ``!``）。
        config 经 AgentManager 现取——SystemPromptManager 在首次
        ``_build_runtime`` 时尚未创建，不能作为过滤数据源。
        """
        config = self.agent_manager.current_config()
        name_list = getattr(config, "extensions", None)
        loaded_names = {getattr(ext, "name", "") for ext in extensions}
        if name_list is None:
            self._extensions_selection_report = []
            return list(extensions)
        allowed_names = apply_name_list(loaded_names, name_list)

        # 报告：settings 的 extensions pattern 是路径级（resolver 应用），
        # 被裁资源不进加载管线；loader 按扩展命名规则（目录名/文件 stem）
        # 把被裁路径推导回注册名，由此区分 missing 与 disabled_by_settings
        disabled_names: set = set()
        get_disabled = getattr(
            self._resource_loader, "get_disabled_extension_names", None
        )
        if callable(get_disabled):
            # Mock/占位 loader 的防御：非集合返回值一律视为无被裁扩展
            result = get_disabled()
            if isinstance(result, (set, frozenset)):
                disabled_names = set(result)
        # allowed_names ⊆ loaded_names（apply_name_list 语义），过滤结果即 final
        self._extensions_selection_report = build_selection_report(
            "extensions",
            name_list,
            loaded_names | set(disabled_names),
            surviving_after_settings=loaded_names,
            final=allowed_names,
        )
        return [
            ext for ext in extensions if getattr(ext, "name", None) in allowed_names
        ]

    def _get_command_filter(self) -> Tuple[Optional[List[str]], set]:
        """命令过滤（yaml 名单原文, 排除集）。

        - yaml ``commands``：三态（None=全放 / []=全禁 / 名单含 ``!``）；
        - 排除集：settings 的 ``disabled_commands``（用户层黑名单）。
        命中规则按命令原始名（碰撞别名 ``name:1`` 不影响判定）。
        """
        config = None
        if self.system_prompt_manager is not None:
            config = self.system_prompt_manager.get_agent_config()
        name_list = getattr(config, "commands", None)
        disabled: set = set()
        if self.settings_manager is not None:
            disabled = set(self.settings_manager.get_settings().disabled_commands or [])
        return name_list, disabled

    def _is_command_allowed(self, name: str) -> bool:
        """命令是否可用（yaml 名单 ∩ 非排除集）。"""
        name_list, disabled = self._get_command_filter()
        if not is_name_allowed(name, name_list):
            return False
        return name not in disabled

    def get_allowed_command_names(self) -> Optional[List[str]]:
        """命令 yaml 名单原文（None = 全部允许）——快照透出用。"""
        name_list, _disabled = self._get_command_filter()
        return sorted(name_list) if name_list is not None else None

    def get_disabled_command_names(self) -> set:
        """命令排除集（settings disabled_commands）——快照透出用。"""
        _allowed, disabled = self._get_command_filter()
        return disabled

    def _get_allowed_skills(self) -> Dict[str, Any]:
        """按来源分治裁剪 skill：空名单全放；非空名单仅裁包内，其余来源放开。"""
        skills = {}
        if self.resource_loader is not None:
            skills = self.resource_loader.get_skills().get("skills") or {}

        config = None
        if self.system_prompt_manager is not None:
            config = self.system_prompt_manager.get_agent_config()
        return filter_skills_by_whitelist(skills, getattr(config, "skills", None))

    def _refresh_user_tools(self) -> None:
        """重建用户工具注册表（settings pattern → yaml 名单，两级收窄）。

        包级用户工具以工厂形态经 ResourceLoader 提供（``get_user_tools``），
        此处绑定会话上下文实例化定义并注册。settings ``user_tools`` 键是
        用户终裁层；yaml ``user_tools`` 名单三态（None=全放 / []=全禁 /
        名单含 ``!``）。同时产出该域的 CapabilitySelection 报告（暂存待
        ``_build_runtime`` 汇集）——settings 键是名字级，可精确归因
        ``disabled_by_settings``。
        """
        manager = self.user_tools_manager
        manager.clear()
        self._user_tools_selection_report = []
        if self._resource_loader is None:
            return
        get_user_tools = getattr(self._resource_loader, "get_user_tools", None)
        if get_user_tools is None:
            return
        resources = (get_user_tools() or {}).get("user_tools") or {}

        # settings 用户终裁层
        settings_patterns = None
        if self.settings_manager is not None:
            settings_patterns = self.settings_manager.get_settings().user_tools
        after_settings = apply_name_list(resources.keys(), settings_patterns)

        # yaml 角色选配层
        config = None
        if self.system_prompt_manager is not None:
            config = self.system_prompt_manager.get_agent_config()
        name_list = getattr(config, "user_tools", None)
        surviving = apply_name_list(after_settings, name_list)

        self._user_tools_selection_report = build_selection_report(
            "user_tools",
            name_list,
            resources.keys(),
            surviving_after_settings=after_settings,
            final=surviving,
        )

        for name, resource in resources.items():
            if name not in surviving:
                continue
            create = getattr(resource, "create", None)
            if create is None:
                continue
            manager.register(create(self))

    def _build_commands_selection_report(self) -> List[CapabilitySelection]:
        """commands 域的选配报告（宇宙 = 扩展命令 + prompt/skill 命令）。

        命令不是独立资源，是扩展/skill/prompt 的运行时投影——名单判定需要
        全量命令名，故报告在会话侧装配（与 ``get_commands`` 透出同一批
        数据源）。settings ``disabled_commands`` 黑名单裁掉的报
        ``disabled_by_settings``。注意运行时强制目前只落在扩展命令上
        （prompt/skill 命令不经 ``_is_command_allowed``），报告按名单语义
        对全宇宙判定。
        """
        name_list, disabled = self._get_command_filter()
        if name_list is None:
            return []
        universe: Set[str] = set()
        runner = self._extension_runner
        if runner is not None:
            # 按原始名判定（碰撞别名 name:1 不参与——同 _is_command_allowed）
            universe.update(c.name for c in runner.get_registered_commands())
        universe.update(
            t.name for t in self.resource_loader.get_prompts().get("prompts", [])
        )
        universe.update(
            f"skill:{s.name}"
            for s in self.resource_loader.get_skills().get("skills", {}).values()
        )
        after_settings = universe - disabled
        final = {n for n in after_settings if is_name_allowed(n, name_list)}
        return build_selection_report(
            "commands",
            name_list,
            universe,
            surviving_after_settings=after_settings,
            final=final,
        )

    def _build_skills_selection_report(self) -> List[CapabilitySelection]:
        """skills 域的选配报告（来源分治同 ``filter_skills_by_whitelist``）。

        settings 的 skills pattern 是路径级（resolver 应用）：被裁 skill 不
        进加载管线，而注册名来自 SKILL.md frontmatter（父目录名仅兜底），
        不解析文件无法干净映射回注册名——settings 态不可判定，被 settings
        裁掉的包内 skill 只能呈现为 missing（局限，在案）。
        """
        config = None
        if self.system_prompt_manager is not None:
            config = self.system_prompt_manager.get_agent_config()
        name_list = getattr(config, "skills", None)
        if name_list is None:
            return []
        skills = self.resource_loader.get_skills().get("skills", {})
        if not isinstance(skills, dict):
            skills = {}
        final = filter_skills_by_whitelist(skills, name_list)
        return build_selection_report(
            "skills",
            name_list,
            skills.keys(),
            final=set(final),
        )

    def _rebuild_capability_report(self) -> None:
        """汇集各资源域的 CapabilitySelection 报告（``_build_runtime`` 末尾调用）。

        生产分工：tools 归 ``ToolsManager.refresh``、persona 归
        ``PersonaManager`` 装配、extensions/user_tools 在本类名单过滤处
        暂存、commands/skills 此处现算（命令宇宙与 skill 注册表在会话侧）。
        汇集结果经 AgentManager 的 provider 透出到快照。
        """
        report: List[CapabilitySelection] = []
        if self.tools_manager is not None:
            report.extend(self.tools_manager.selection_report)
        report.extend(self._extensions_selection_report)
        report.extend(self._user_tools_selection_report)
        report.extend(self._build_commands_selection_report())
        report.extend(self._build_skills_selection_report())
        if self.persona_manager is not None:
            report.extend(self.persona_manager.selection_report)
        self._capability_report = report

    def _build_runtime(
        self,
        active_tool_names: Optional[List[str]] = None,
        flag_values: Optional[Dict[str, Any]] = None,
    ) -> None:
        """初始化扩展 runner、工具注册表与系统提示词。"""
        from nova_harness.core.extensions.event_bus import ExtensionEventBus
        from nova_harness.core.types.extensions import ExtensionRuntime

        raw_extensions_result = self.resource_loader.get_extensions()
        extensions = raw_extensions_result.extensions
        extension_runtime = raw_extensions_result.runtime

        # 确定当前 agent 名称（默认解析归拢在 AgentManager）：保持现状
        # （reload/change_agent 后）> 显式配置（CreateAgentSessionOptions.
        # agent_name，拼错即抛错）> 第一个可用 > "base_agent"。
        # 必须先于扩展过滤解析——extensions 名单等过滤点经 AgentManager
        # 现取 config（SystemPromptManager 首建时尚未创建）。
        agent_name = self.agent_manager.resolve_current(self.config.agent_name)

        extensions = self._get_allowed_extensions(extensions)

        if extension_runtime is None:
            extension_runtime = ExtensionRuntime(
                cwd=self.cwd,
                event_bus=self.resource_loader.event_bus,
                model_runtime=self._model_runtime,
            )

        self._extension_runner = ExtensionRunner(
            extensions=extensions,
            runtime=extension_runtime,
            cwd=self.cwd,
            session_manager=self.session_manager,
            model_runtime=self._model_runtime,
        )
        if flag_values:
            extension_runtime.flag_values.update(flag_values)
        if self.extension_runner_ref is not None:
            self.extension_runner_ref["current"] = self._extension_runner

        self._bind_extension_core(self._extension_runner)
        self._apply_extension_bindings(self._extension_runner)

        # 若 ToolsManager 已存在，更新其 extension_runner 引用（reload 等场景）
        if self.tools_manager is not None:
            self.tools_manager.extension_runner = self._extension_runner

        # 创建 ToolsManager 并重建工具注册表
        if self.tools_manager is None:
            self.tools_manager = ToolsManager(
                resource_loader=self._resource_loader,
                extension_runner=self._extension_runner,
                base_tools_override=self.base_tools_override,
                custom_tools=self.custom_tools,
                allowed_tool_names=self.allowed_tool_names,
                excluded_tool_names=self.excluded_tool_names,
                agent_name=agent_name,
            )
        else:
            self.tools_manager.agent_name = agent_name
        # AgentManager 后绑定 ToolsManager（save_agent 取激活集用）
        self.agent_manager.tools_manager = self.tools_manager

        # 创建/更新 SystemPromptManager，绑定 AgentManager/ToolsManager/PersonaManager
        if self.system_prompt_manager is None:
            self.system_prompt_manager = SystemPromptManager(
                resource_loader=self._resource_loader,
                agent_manager=self.agent_manager,
                tools_manager=self.tools_manager,
                persona_manager=self.persona_manager,
            )
        else:
            self.system_prompt_manager.persona_manager = self.persona_manager

        self._tools.refresh_registry(
            active_tool_names=active_tool_names,
        )
        self._refresh_user_tools()
        self._sync_system_prompt()
        # 汇集各域选配报告（persona 装配在 _sync_system_prompt 内完成，须最后）
        self._rebuild_capability_report()

    def _sync_system_prompt(self) -> None:
        """用当前配置和工具白名单重建系统提示词。"""
        from nova_harness.core.types.resources.agents import DynamicContext

        context = DynamicContext(cwd=self.cwd, session_id=str(self.session_id))
        self._enrich_environment_context(context)
        self._base_system_prompt = self.system_prompt_manager.build_system_prompt(
            context
        )
        self._base_system_prompt_options = (
            self.system_prompt_manager.build_system_prompt_options(context)
        )
        self.agent.state.system_prompt = self._base_system_prompt

    def _enrich_environment_context(self, context: Any) -> None:
        """富化系统提示词环境段（设计定案 R5——会话条目即事实源）。

        读两条会话条目：``executor_backend``（执行后端选择，/executor 写入）
        与 ``plan-mode``（只读规划模式档位）。cwd/后端身份经环境段呈现；
        缺省 = 本地直接执行（local）。
        """
        backend: Optional[str] = None
        environment_id: Optional[str] = None
        permission: Optional[str] = None
        remote_cwd: Optional[str] = None
        remote_shell: Optional[str] = None

        session_manager = getattr(self, "session_manager", None)
        if session_manager is not None:
            for entry in reversed(session_manager.get_branch()):
                entry_type = getattr(entry, "type", "")
                custom_type = getattr(entry, "custom_type", "")
                data = getattr(entry, "data", None)
                if entry_type != "custom" or not isinstance(data, dict):
                    continue
                if custom_type == "executor_backend" and backend is None:
                    backend = data.get("backend")
                    url = data.get("url")
                    environment_id = url if backend == "executor" and url else None
                    remote_cwd = data.get("remote_cwd")
                    remote_shell = data.get("remote_shell")
                elif custom_type == "plan-mode" and permission is None:
                    permission = (
                        "read-only" if data.get("enabled") else "workspace-write"
                    )
                if backend is not None and permission is not None:
                    break

        context.backend = backend or "local"
        context.environment_id = environment_id
        context.permission = permission or "workspace-write"
        # workspace_roots 即 fs 工具看到的世界：远程后端时是远程执行 cwd
        # （六个 fs 工具已随后端切换），本地时是本地 cwd（executor 接入定案）
        context.workspace_roots = [remote_cwd] if remote_cwd else [self.cwd]
        # cwd 即执行 cwd（codex environment_context 语义：命令在哪跑写哪）——
        # SSH 远程后端时用 remote_cwd（远程文件系统与本地无关）
        if remote_cwd:
            context.cwd = remote_cwd
        # shell 即平台代理（codex environment_context 实证形态——不写独立 OS 字段）；
        # 远程后端优先用供给探测到的远程登录 shell
        if remote_shell:
            context.shell = remote_shell
        else:
            shell_path = (
                self.settings_manager.get_shell_path()
                if self.settings_manager
                else None
            )
            if shell_path:
                context.shell = os.path.basename(shell_path)
            elif os.name == "nt":
                context.shell = "powershell"
            else:
                context.shell = os.environ.get("SHELL", "bash").split("/")[-1]

    # -------------------------------------------------------------------------
    # 属性
    # -------------------------------------------------------------------------

    @property
    def state(self) -> Any:
        return self.agent.state

    @property
    def model(self) -> Optional[Any]:
        return getattr(self.agent.state, "model", None)

    def get_tool_exec_context(self) -> ToolExecContext:
        """包工具的执行期上下文工厂（``execute`` 第 5 参）。

        每次调用现造冻结值对象，反映调用时刻的模型（对齐 pi runner 的
        ``createContext()``）。由 ``ToolController.refresh_registry``
        作为 ``context_provider`` 注入工具注册表。

        ``ui``（pi ``ctx.ui`` 对位）：经 ``ScopedUIContext`` 织入两条纪律
        ——run abort 竞速（``getattr(self.agent, "signal", None)`` 现取，
        与扩展侧同一信号源）与弹窗串行锁（会话级一把锁，并行工具调用的
        UI 请求排队）。无 UI 宿主（headless/NoOp）时给 NoOp 句柄 +
        ``has_ui=False``，工具走非交互降级。

        ``agents``：会话 agents 注册表快照（subagent 工具按名查表委派——
        工具侧不再自行发现，注册表即单一事实源）。
        """
        base_ui = self._extension_ui_context
        has_ui = base_ui is not None and not isinstance(base_ui, NoOpUIContext)
        agents: Dict[str, Any] = {}
        if self._resource_loader is not None:
            try:
                raw_agents = self._resource_loader.get_agents()
                if isinstance(raw_agents, dict):
                    agents = raw_agents
            except Exception:
                agents = {}
        return ToolExecContext(
            model=self.model,
            ui=ScopedUIContext(
                base_ui or NoOpUIContext(),
                lambda: getattr(self.agent, "signal", None),
                self._tool_ui_request_lock,
            ),
            has_ui=has_ui,
            agents=agents,
        )

    @property
    def thinking_level(self) -> ModelThinkingLevel:
        # nova_agent 的 AgentState.thinking_level 非可选（默认 OFF）
        return self.agent.state.thinking_level

    @property
    def is_streaming(self) -> bool:
        return bool(getattr(self.agent.state, "is_streaming", False))

    @property
    def is_retrying(self) -> bool:
        return self._retry.is_retrying

    @property
    def is_compacting(self) -> bool:
        return self._compaction.is_compacting

    @property
    def system_prompt(self) -> str:
        return str(getattr(self.agent.state, "system_prompt", "") or "")

    @property
    def messages(self) -> List[AgentMessage]:
        return list(getattr(self.agent.state, "messages", []))

    @property
    def session_id(self) -> str:
        return self.session_manager.get_session_id()

    @property
    def session_file(self) -> Optional[str]:
        return self.session_manager.get_session_file()

    @property
    def session_name(self) -> Optional[str]:
        return self.session_manager.get_session_name()

    @property
    def extension_runner(self) -> Optional[ExtensionRunner]:
        return self._extension_runner

    @property
    def steering_mode(self) -> str:
        return getattr(self.agent, "steering_mode", "one-at-a-time")

    @property
    def follow_up_mode(self) -> str:
        return getattr(self.agent, "follow_up_mode", "one-at-a-time")

    @property
    def pending_message_count(self) -> int:
        return len(self._steering_messages) + len(self._follow_up_messages)

    @property
    def auto_retry_enabled(self) -> bool:
        """是否启用自动重试。"""
        if self.settings_manager is None:
            return False
        return bool(self.settings_manager.get_retry_enabled())

    @property
    def auto_compaction_enabled(self) -> bool:
        """是否启用自动压缩。"""
        if self.settings_manager is None:
            return False
        return bool(self.settings_manager.get_compaction_enabled())

    @property
    def retry_attempt(self) -> int:
        """当前重试次数。"""
        return self._retry_attempt

    @property
    def prompt_templates(self) -> List[Any]:
        """返回已加载的 prompt 模板列表。"""
        if self._resource_loader is None:
            return []
        return list(self._resource_loader.get_prompts().get("prompts", []))

    def is_user_tool_running(self, name: Optional[str] = None) -> bool:
        """是否有用户工具调用正在执行；name 给定时只看该工具。"""
        return self._user_tools.is_running(name)

    @property
    def has_pending_user_tool_messages(self) -> bool:
        """是否有待处理的用户工具消息。"""
        return self._user_tools.has_pending_messages

    @property
    def resource_loader(self) -> Any:
        return self._resource_loader

    @property
    def model_runtime(self) -> Any:
        return self._model_runtime

    # -------------------------------------------------------------------------
    # 事件分发
    # -------------------------------------------------------------------------

    def _emit(self, event: Any) -> None:
        """向所有订阅者分发事件。"""
        for listener in list(self._event_listeners):
            try:
                result = listener(event)
                if inspect.isawaitable(result):
                    asyncio.create_task(result)
            except Exception:
                # 监听者异常不应中断主流程
                pass

    # -------------------------------------------------------------------------
    # 生命周期
    # -------------------------------------------------------------------------

    async def bind_extensions(self, bindings: Optional[Dict[str, Any]] = None) -> None:
        """扩展加载完成后绑定 UI/动作回调，并触发 session_start 与资源发现。"""
        if bindings is not None:
            self._extension_ui_context = bindings.get("ui_context")
            self._extension_command_context_actions = bindings.get(
                "command_context_actions"
            )
            self._extension_abort_handler = bindings.get("abort_handler")
            self._extension_shutdown_handler = bindings.get("shutdown_handler")
            self._extension_error_listener = bindings.get("on_error")

        if self._extension_runner is not None:
            self._apply_extension_bindings(self._extension_runner)
            await self._extension_runner.emit(self.session_start_event)
            await self._extend_resources_from_extensions()

    def _bind_extension_core(self, runner: ExtensionRunner) -> None:
        """把 session 能力打包成 actions 注入 runner。"""

        async def send_message(
            message: Dict[str, Any], options: Optional[Any] = None
        ) -> None:
            try:
                return await self.send_custom_message(message, options)
            except Exception as exc:
                runner.emit_error(
                    ExtensionErrorEvent(
                        extension_path="<runtime>",
                        event="send_message",
                        error=str(exc),
                        stack=traceback.format_exc() if __debug__ else None,
                    )
                )

        async def send_user_message(
            content: Any, options: Optional[Any] = None
        ) -> None:
            try:
                return await self.send_user_message(content, options)
            except Exception as exc:
                runner.emit_error(
                    ExtensionErrorEvent(
                        extension_path="<runtime>",
                        event="send_user_message",
                        error=str(exc),
                        stack=traceback.format_exc() if __debug__ else None,
                    )
                )

        def _refresh_current_model() -> None:
            current = self.model
            if current is None:
                return
            refreshed = self._model_runtime.find(current.provider, current.id)
            if refreshed and refreshed is not current:
                self.agent.state.model = refreshed

        def register_provider(name: str, config: Any) -> None:
            self._model_runtime.register_provider(name, config)
            _refresh_current_model()

        def unregister_provider(name: str) -> None:
            self._model_runtime.unregister_provider(name)
            _refresh_current_model()

        def compact(options: Optional[Any] = None) -> Any:
            custom_instructions = (
                options.get("custom_instructions") if options else None
            )
            on_complete = options.get("on_complete") if options else None
            on_error = options.get("on_error") if options else None

            async def _run() -> None:
                try:
                    result = await self.compact(custom_instructions)
                    if on_complete is not None:
                        on_complete(result)
                except Exception as exc:
                    if on_error is not None:
                        on_error(exc)
                        return
                    # 无 on_error 回调时异常必须传播——await 方拿到真实错误，
                    # fire-and-forget 则在任务异常日志中可见（静默吞错是排障地狱）
                    raise

            # 返回 Task 而非 None：调用方既可 fire-and-forget（不配回调），
            # 也可 await 拿到真实结果/异常（此前返回 None——`await ctx.compact()`
            # 直接崩 "NoneType can't be used in 'await' expression"，且后台任务
            # 里真实错误被静默吞掉）
            return asyncio.ensure_future(_run())

        def abort() -> None:
            if self._extension_abort_handler is not None:
                self._extension_abort_handler()
                return
            asyncio.create_task(self.abort())

        async def exec_command(
            command: str, args: List[str], options: Optional[Any] = None
        ) -> ExecResult:
            """执行子进程命令。"""
            if isinstance(options, ExecOptions):
                opts = options
            else:
                opts = ExecOptions(**(options or {}))
            cwd = opts.cwd or self.cwd
            try:
                proc = await asyncio.create_subprocess_exec(
                    command,
                    *args,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                if opts.timeout and opts.timeout > 0:
                    try:
                        stdout_b, stderr_b = await asyncio.wait_for(
                            proc.communicate(), timeout=opts.timeout
                        )
                    except asyncio.TimeoutError:
                        proc.kill()
                        stdout_b, stderr_b = await proc.communicate()
                        return ExecResult(
                            stdout=stdout_b.decode(errors="replace"),
                            stderr=stderr_b.decode(errors="replace"),
                            code=proc.returncode or 1,
                            killed=True,
                        )
                else:
                    stdout_b, stderr_b = await proc.communicate()
                code = proc.returncode if proc.returncode is not None else 1
                return ExecResult(
                    stdout=stdout_b.decode(errors="replace"),
                    stderr=stderr_b.decode(errors="replace"),
                    code=code,
                    killed=False,
                )
            except Exception as exc:
                return ExecResult(stdout="", stderr=str(exc), code=1, killed=False)

        def shutdown() -> None:
            if self._extension_shutdown_handler is not None:
                self._extension_shutdown_handler()

        def is_project_trusted() -> bool:
            if self.settings_manager is not None:
                return self.settings_manager.is_project_trusted()
            trusted = runner.project_trusted
            return True if trusted is None else trusted

        def get_signal() -> Optional[Any]:
            return getattr(self.agent, "signal", None)

        actions = ExtensionActions(
            send_message=send_message,
            send_user_message=send_user_message,
            exec=exec_command,
            append_entry=self.append_entry,
            set_session_name=lambda name: self.set_session_name(name),
            get_session_name=lambda: self.session_manager.get_session_name(),
            set_label=lambda entry_id, label: self.session_manager.append_label_change(
                entry_id, label
            ),
            get_active_tools=lambda: self.get_active_tool_names(),
            get_all_tools=lambda: [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "prompt_guidelines": t.prompt_guidelines,
                    "source_info": t.source_info
                    or SourceInfo(
                        path=t.source_path or t.name,
                        source=t.source or "package",
                        scope="temporary",
                        origin="package" if t.source == "package" else "top-level",
                    ),
                }
                for t in self.get_all_tools()
            ],
            set_active_tools=lambda tool_names: self.set_active_tools_by_name(
                tool_names
            ),
            refresh_tools=lambda: self._tools.refresh_registry(),
            get_commands=lambda: [
                SlashCommandInfo(
                    name=c.resolved_name,
                    description=c.description,
                    source="extension",
                    source_info=c.source_info
                    or SourceInfo(
                        path=c.extension_path or "",
                        source="extension",
                        scope="temporary",
                        origin="top-level",
                    ),
                )
                # 命令允许集/排除集过滤（agent.yaml commands + settings.disabled_commands）
                for c in runner.get_registered_commands()
                if self._is_command_allowed(c.name)
            ]
            + [
                SlashCommandInfo(
                    name=t.name,
                    description=t.description,
                    source="prompt",
                    source_info=t.source_info
                    or SourceInfo(
                        path=t.file_path,
                        source="local",
                        scope="temporary",
                        origin="top-level",
                    ),
                )
                for t in self.resource_loader.get_prompts().get("prompts", [])
            ]
            + [
                SlashCommandInfo(
                    name=f"skill:{s.name}",
                    description=s.description,
                    source="skill",
                    source_info=s.source_info
                    or SourceInfo(
                        path=s.file_path,
                        source="local",
                        scope="temporary",
                        origin="top-level",
                    ),
                )
                for s in self.resource_loader.get_skills().get("skills", {}).values()
            ],
            set_model=lambda model: self.set_model(model),
            get_thinking_level=lambda: self.thinking_level,
            set_thinking_level=lambda level: self.set_thinking_level(level),
        )

        context_actions = ExtensionContextActions(
            get_model=lambda: self.model,
            is_idle=lambda: not self.is_streaming,
            is_project_trusted=is_project_trusted,
            get_signal=get_signal,
            abort=abort,
            has_pending_messages=lambda: self.pending_message_count > 0,
            shutdown=shutdown,
            get_context_usage=lambda: self.get_context_usage(),
            compact=compact,
            get_system_prompt=lambda: self.system_prompt,
            get_system_prompt_options=lambda: dict(self._base_system_prompt_options),
            get_personas=self._get_persona_entries,
            get_persona_override=lambda: (
                self.persona_manager.current_override if self.persona_manager else None
            ),
            set_persona_override=self._set_persona_override,
            clear_persona_override=self._clear_persona_override,
            # agent 旋钮（注册表视图 + 当前角色切换 + yaml 写回——AgentManager 的扩展面）
            get_agents=self._get_agent_entries,
            change_agent=lambda name: self.change_agent(name),
            save_agent=self._save_agent,
            # executor 执行后端设置（/executor 扩展的端点清单数据源）
            get_executor_settings=lambda: (
                self.settings_manager.get_executor_settings()
                if self.settings_manager is not None
                else None
            ),
            # executor 端点登记/注销（/executor 首次连接自动登记、forget 移除）
            register_executor_endpoint=lambda name, url, cwd=None: (
                self.settings_manager.register_executor_endpoint(name, url, cwd)
                if self.settings_manager is not None
                else None
            ),
            unregister_executor_endpoint=lambda name: (
                self.settings_manager.unregister_executor_endpoint(name)
                if self.settings_manager is not None
                else False
            ),
            # 系统提示词重建（环境段内容变化后——/executor 切换等）
            refresh_system_prompt=lambda: self._sync_system_prompt(),
        )

        provider_actions = ExtensionProviderActions(
            register_provider=register_provider,
            unregister_provider=unregister_provider,
        )

        runner.bind_core(
            actions,
            context_actions,
            provider_actions,
        )

    def _apply_extension_bindings(self, runner: ExtensionRunner) -> None:
        """把保存的 UI 上下文、命令上下文与错误监听器绑定到指定 runner。

        每次重建 runner（如 _build_runtime / reload）都会调用本方法，
        因此需要先取消旧的错误监听器再注册新的，避免重复订阅。
        """
        runner.set_ui_context(self._extension_ui_context)
        if self._extension_command_context_actions is None:
            self._extension_command_context_actions = (
                self._create_default_command_context_actions()
            )
        runner.bind_command_context(self._extension_command_context_actions)

        if self._extension_error_unsubscriber is not None:
            self._extension_error_unsubscriber()
            self._extension_error_unsubscriber = None

        # 默认把扩展错误扇出到 Bus 2（ExtensionErrorEvent 不再无人接收）；
        # 外部注入的 on_error 监听器（若有）并存
        def _forward_extension_error(event: Any) -> None:
            self._emit(event)

        self._extension_error_unsubscriber = runner.on_error(_forward_extension_error)

        if self._extension_error_listener is not None:
            runner.on_error(self._extension_error_listener)

    def _create_default_command_context_actions(self) -> ExtensionCommandContextActions:
        """当外部（如 RPC/TUI）未注入命令上下文 actions 时，使用 AgentSession 自身能力构造默认值。"""

        async def wait_for_idle() -> None:
            if hasattr(self.agent, "wait_for_idle"):
                await self.agent.wait_for_idle()

        async def new_session(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            return await self.new_agent_session(
                kwargs.get("parent_session") if kwargs else None
            )

        async def fork(entry_id: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
            position = kwargs.get("position", "after")
            return await self.fork_session(entry_id, position)

        async def navigate_tree(
            target_id: str, *args: Any, **kwargs: Any
        ) -> Dict[str, Any]:
            return await self.navigate_tree(target_id, kwargs)

        async def switch_session(
            session_path: str, *args: Any, **kwargs: Any
        ) -> Dict[str, Any]:
            return await self.switch_agent_session(session_path)

        async def reload(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            await self.reload()
            return {"cancelled": False}

        def get_session_info(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            return self.get_session_info()

        def get_scoped_models(*args: Any, **kwargs: Any) -> List[ScopedModelConfig]:
            return list(self.scoped_models)

        def trust_project(*args: Any, **kwargs: Any) -> None:
            self.trust_project(True)

        def untrust_project(*args: Any, **kwargs: Any) -> None:
            self.trust_project(False)

        async def clone(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            return await self.clone_session()

        async def export(path: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
            return await self.export_session(path)

        async def import_session(
            path: str, *args: Any, **kwargs: Any
        ) -> Dict[str, Any]:
            return await self.import_session(path)

        return ExtensionCommandContextActions(
            wait_for_idle=wait_for_idle,
            new_session=new_session,
            fork=fork,
            navigate_tree=navigate_tree,
            switch_session=switch_session,
            reload=reload,
            get_session_info=get_session_info,
            get_scoped_models=get_scoped_models,
            trust_project=trust_project,
            untrust_project=untrust_project,
            clone=clone,
            export=export,
            import_session=import_session,
        )

    async def reload(self) -> None:
        """重新加载设置、资源与扩展，并刷新当前 session 的系统提示词。"""
        previous_flag_values: Dict[str, Any] = {}
        if self._extension_runner is not None:
            previous_flag_values = self._extension_runner.get_flag_values()
            await emit_session_shutdown_event(
                self._extension_runner,
                SessionShutdownEvent(reason="reload"),
            )

        await self.settings_manager.reload()
        self.sync_queue_modes_from_settings()
        await self.resource_loader.reload()

        self._build_runtime(
            active_tool_names=self.get_active_tool_names(),
            flag_values=previous_flag_values,
        )

        if self._has_extension_bindings() and self._extension_runner is not None:
            self.session_start_event = SessionStartEvent(reason="reload")
            await self._extension_runner.emit(self.session_start_event)
            await self._extend_resources_from_extensions(reason="reload")

        # Bus 2 通知（前端刷新包 UI 贡献——slots 整体重载的触发点）
        from nova_harness.core.types.events import SessionReloadedEvent

        self._emit(SessionReloadedEvent(reason="reload"))

    def _has_extension_bindings(self) -> bool:
        return any(
            (
                self._extension_ui_context is not None,
                self._extension_command_context_actions is not None,
                self._extension_shutdown_handler is not None,
                self._extension_error_listener is not None,
            )
        )

    async def _extend_resources_from_extensions(self, reason: str = "startup") -> None:
        """收集扩展贡献的临时资源路径并重新加载受影响资源。"""
        if self._extension_runner is None:
            return
        if not self._extension_runner.has_handlers(RESOURCES_DISCOVER):
            return

        result = await self._extension_runner.emit_resources_discover(self.cwd, reason)
        skill_paths = result.get("skill_paths", [])
        prompt_paths = result.get("prompt_paths", [])
        persona_paths = result.get("persona_paths", [])
        if not (skill_paths or prompt_paths or persona_paths):
            return

        paths = ResourceExtensionPaths(
            skill_paths=[
                ResourceExtensionPathEntry(
                    path=p["path"], extension_path=p.get("extensionPath")
                )
                for p in skill_paths
            ],
            prompt_paths=[
                ResourceExtensionPathEntry(
                    path=p["path"], extension_path=p.get("extensionPath")
                )
                for p in prompt_paths
            ],
            persona_paths=[
                ResourceExtensionPathEntry(
                    path=p["path"], extension_path=p.get("extensionPath")
                )
                for p in persona_paths
            ],
        )
        self.resource_loader.extend_resources(paths)
        # 扩展贡献的 skill 可能包含新工具，刷新注册表后再重建 system prompt
        self._tools.refresh_registry(
            active_tool_names=self.get_active_tool_names(),
        )
        self._sync_system_prompt()

    def _disconnect_from_agent(self) -> None:
        """临时断开与底层 Agent 的事件订阅（用户监听者保留）。"""
        if self._unsubscribe_agent is not None:
            try:
                self._unsubscribe_agent()
            except Exception:
                pass
            self._unsubscribe_agent = None

    def _reconnect_to_agent(self) -> None:
        """重新连接底层 Agent 事件订阅。"""
        if self._unsubscribe_agent is not None:
            return
        self._subscribe_agent_events()

    def dispose(self) -> None:
        """释放当前会话占用的资源。"""
        try:
            self.abort_retry()
            self.abort_compaction()
            self.abort_branch_summary()
            self.abort_user_tool()
            if hasattr(self.agent, "abort"):
                self.agent.abort()
        except Exception:
            pass

        if self._extension_runner is not None:
            try:
                self._extension_runner.invalidate()
            except Exception:
                pass

        self._disconnect_from_agent()
        self._event_listeners = []

    # -------------------------------------------------------------------------
    # Prompting
    # -------------------------------------------------------------------------

    async def prompt(self, text: str, options: Optional[PromptOptions] = None) -> None:
        """发送一条用户消息并触发 Agent 回复。"""
        opts = options or PromptOptions()
        expand_prompt_templates = getattr(opts, "expand_prompt_templates", True)
        preflight_result = getattr(opts, "preflight_result", None)

        current_text = text
        current_images: List[ImageContent] = list(getattr(opts, "images", []) or [])
        messages: Optional[List[AgentMessage]] = None

        try:
            # 1. 扩展命令立即执行（input 事件不能拦截扩展命令）
            if expand_prompt_templates and current_text.startswith("/"):
                if await self._slash_handler.execute_command(current_text):
                    if preflight_result is not None:
                        preflight_result(True)
                    return

            # 2. 扩展 input 事件拦截
            if (
                self._extension_runner is not None
                and self._extension_runner.has_handlers(INPUT)
            ):
                from nova_harness.core.types.events import InputEvent

                input_event = InputEvent(
                    text=current_text,
                    images=current_images,
                    source=getattr(opts, "source", "interactive"),
                    streaming_behavior=getattr(opts, "streaming_behavior", None),
                )
                input_result = await self._extension_runner.emit_input(input_event)
                action = getattr(input_result, "action", "continue")
                if action == "handled":
                    if preflight_result is not None:
                        preflight_result(True)
                    return
                if action == "transform":
                    if getattr(input_result, "text", None) is not None:
                        current_text = input_result.text
                    if getattr(input_result, "images", None) is not None:
                        current_images = input_result.images

            # 3. skill / prompt template 展开
            if expand_prompt_templates:
                current_text = self._slash_handler.expand_skill_and_prompt(current_text)

            # 流式中排队
            if self.is_streaming:
                behavior = getattr(opts, "streaming_behavior", None)
                if behavior is None:
                    raise RuntimeError(
                        "Agent is already processing. Specify streaming_behavior ('steer' or 'followUp')."
                    )
                if behavior == "followUp":
                    await self._queue.follow_up(current_text, current_images)
                else:
                    await self._queue.steer(current_text, current_images)
                if preflight_result is not None:
                    preflight_result(True)
                return

            # 刷新待处理用户工具消息
            self._user_tools.flush_pending()

            # 校验模型
            if self.model is None:
                raise RuntimeError(format_no_model_selected_message())

            api_key = None
            if hasattr(self.model_runtime, "get_api_key"):
                api_key = await self.model_runtime.get_api_key(self.model)
            if not api_key:
                raise RuntimeError(
                    format_no_auth_message(
                        self.model.provider,
                        self.model_runtime.is_using_oauth(self.model.provider),
                    )
                )

            # 发送前检查是否需要自动压缩（处理被中止的响应）
            last_assistant = self._find_last_assistant_message()
            if last_assistant is not None and await self._compaction.check_compaction(
                last_assistant, False
            ):
                try:
                    await self.agent.continue_()
                    while await self._handle_post_agent_run():
                        await self.agent.continue_()
                finally:
                    self._user_tools.flush_pending()

            # 构造用户消息
            content: List[Union[TextContent, ImageContent]] = [
                TextContent(type="text", text=current_text)
            ]
            if current_images:
                content.extend(current_images)

            messages = [UserMessage(role="user", content=content)]
            messages.extend(self._pending_next_turn_messages)
            self._pending_next_turn_messages = []

            # before_agent_start 扩展事件
            if self._extension_runner is not None:
                before_result = await self._extension_runner.emit_before_agent_start(
                    BeforeAgentStartEvent(
                        prompt=current_text,
                        images=current_images,
                        system_prompt=self._base_system_prompt,
                        system_prompt_options={"cwd": self.cwd},
                    )
                )
                if before_result is not None:
                    if before_result.system_prompt is not None:
                        self.agent.state.system_prompt = before_result.system_prompt
                    if before_result.messages:
                        messages.extend(before_result.messages)
            else:
                self.agent.state.system_prompt = self._base_system_prompt

        except Exception:
            if preflight_result is not None:
                preflight_result(False)
            raise

        if messages is None:
            return

        if preflight_result is not None:
            preflight_result(True)
        await self._run_agent_prompt(messages)

    async def invoke_user_tool(
        self,
        name: str,
        params: Optional[Dict[str, Any]] = None,
        on_event: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    ) -> CustomAgentMessage:
        """按名调用一个已注册的用户工具并记录结果消息。"""
        return await self._user_tools.invoke(name, params, on_event)

    def abort_user_tool(self, name: Optional[str] = None) -> None:
        """取消正在运行的用户工具调用；name 为空则全部取消。"""
        self._user_tools.abort(name)

    def list_user_tools(self) -> List[UserToolInfo]:
        """已注册用户工具的目录。"""
        return self.user_tools_manager.catalog()

    async def _run_agent_prompt(
        self, messages: Union[AgentMessage, List[AgentMessage]]
    ) -> None:
        """执行一次 Agent prompt，并在之后处理续话/重试/压缩。"""
        try:
            await self.agent.prompt(messages)
            while await self._handle_post_agent_run():
                await self.agent.continue_()
        finally:
            self._user_tools.flush_pending()
            await self._emit_agent_settled()

    async def _emit_agent_settled(self) -> None:
        """run 终结（含续话 drain）后发射 agent_settled——双发 Bus 2 与扩展面。

        对齐 pi ``_emitAgentSettled``：正常结束、abort、异常路径均发射
        （在 finally 中调用，run 终结即 settled）。
        """
        event = AgentSettledEvent()
        self._emit(event)
        runner = self._extension_runner
        if runner is not None and runner.has_handlers(AGENT_SETTLED):
            await runner.emit(event)

    async def _handle_post_agent_run(self) -> bool:
        """检查是否需要续话（重试、压缩或队列消息）。"""
        msg = self._last_assistant_message
        self._last_assistant_message = None
        if msg is None:
            return bool(
                hasattr(self.agent, "has_queued_messages")
                and self.agent.has_queued_messages()
            )

        if self._retry.is_retryable_error(msg) and await self._retry.prepare_retry(msg):
            return True

        if getattr(msg, "stop_reason", None) == "error" and self._retry_attempt > 0:
            self._emit(
                AutoRetryEndEvent(
                    success=False,
                    attempt=self._retry_attempt,
                    final_error=msg.error_message,
                )
            )
            self._retry_attempt = 0

        if await self._compaction.check_compaction(msg):
            return True

        return bool(
            hasattr(self.agent, "has_queued_messages")
            and self.agent.has_queued_messages()
        )

    def abort_retry(self) -> None:
        self._retry.abort_retry()

    def set_auto_retry_enabled(self, enabled: bool) -> None:
        self._retry.set_auto_retry_enabled(enabled)

    async def steer(
        self, text: str, images: Optional[List[ImageContent]] = None
    ) -> None:
        """在 Agent 运行时插入一条 steering 消息。"""
        if text.startswith("/") and self._slash_handler.is_extension_command(text):
            raise RuntimeError(
                f'Extension command "{text}" cannot be queued. '
                "Use prompt() or execute the command when not streaming."
            )
        expanded = expand_skill_command(text, self._get_allowed_skills())
        expanded = expand_prompt_template(
            expanded, self.resource_loader.get_prompts().get("prompts", [])
        )
        await self._queue.steer(expanded, images)

    async def follow_up(
        self, text: str, images: Optional[List[ImageContent]] = None
    ) -> None:
        """在 Agent 完成当前 turn 后追加一条 follow-up 消息。

        语义（对齐 pi）：turn 进行中 → 排队（turn 末由 loop 自动 drain）；
        **idle → 立即开启新一轮**（此前只入队不驱动，消息永远滞留）。
        """
        if text.startswith("/") and self._slash_handler.is_extension_command(text):
            raise RuntimeError(
                f'Extension command "{text}" cannot be queued. '
                "Use prompt() or execute the command when not streaming."
            )
        if self.is_streaming:
            expanded = expand_skill_command(text, self._get_allowed_skills())
            expanded = expand_prompt_template(
                expanded, self.resource_loader.get_prompts().get("prompts", [])
            )
            await self._queue.follow_up(expanded, images)
            return
        if self.agent.state.messages:
            # idle 且已有历史：入队后经 continue_ 立即 drain 开启新 turn
            expanded = expand_skill_command(text, self._get_allowed_skills())
            expanded = expand_prompt_template(
                expanded, self.resource_loader.get_prompts().get("prompts", [])
            )
            await self._queue.follow_up(expanded, images)
            await self.agent.continue_()
        else:
            # 首轮 follow_up 即 prompt（无历史可续，且避免重复展开）
            await self.prompt(text)

    def clear_queue(self) -> Dict[str, List[str]]:
        """清空 steering 与 follow-up 队列。"""
        return self._queue.clear()

    def get_steering_messages(self) -> List[str]:
        """返回当前 steering 消息列表。"""
        return list(self._queue.get_steering())

    def get_follow_up_messages(self) -> List[str]:
        """返回当前 follow-up 消息列表。"""
        return list(self._queue.get_follow_up())

    def export_to_jsonl(self) -> str:
        """将会话条目导出为 JSONL 字符串。"""
        entries = self.session_manager.get_entries()
        return "\n".join(
            json.dumps(entry.model_dump(), ensure_ascii=False) for entry in entries
        )

    def get_last_assistant_text(self) -> Optional[str]:
        """获取最后一条 assistant 消息的文本内容。"""
        msg = self._find_last_assistant_message()
        if msg is None:
            return None
        return extract_text_from_content(getattr(msg, "content", []))

    def has_extension_handlers(self, event_type: str) -> bool:
        """指定事件类型是否有扩展处理器。"""
        if self._extension_runner is None:
            return False
        return self._extension_runner.has_handlers(event_type)

    def get_tool_definition(self, name: str) -> Optional[Any]:
        """按名称返回工具定义。"""
        if self.tools_manager is None:
            return None
        return self.tools_manager.get_tool_definition(name)

    async def send_user_message(
        self,
        content: Union[str, List[Union[TextContent, ImageContent]]],
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """发送一条用户消息并触发回复（扩展用）。"""
        opts = options or {}
        text: str
        images: Optional[List[ImageContent]] = None

        if isinstance(content, str):
            text = content
        else:
            text_parts: List[str] = []
            images = []
            for part in content:
                if getattr(part, "type", None) == "text":
                    text_parts.append(getattr(part, "text", ""))
                else:
                    images.append(part)
            text = "\n".join(text_parts)
            if not images:
                images = None

        await self.prompt(
            text,
            PromptOptions(
                expand_prompt_templates=False,
                images=images or [],
                streaming_behavior=opts.get("deliverAs"),
                source="extension",
            ),
        )

    async def send_custom_message(
        self,
        message: Dict[str, Any],
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """发送一条自定义消息，创建 CustomMessageEntry。"""
        from nova_harness.core.types.messages import CustomMessage

        opts = options or {}
        # 边界归一：扩展圈事实惯例是 {"type": ..., "text": ...}（pi 对位为
        # {"customType", "content"}）——两族都收，别让历史调用点产出空消息
        custom_type = (
            message.get("custom_type")
            or message.get("customType")
            or message.get("type", "")
        )
        content = message.get("content", message.get("text", ""))
        display = message.get("display", True)
        details = message.get("details", None)

        custom_message = CustomMessage(
            custom_type=custom_type,
            content=content,
            display=display,
            details=details,
            timestamp=int(time.time() * 1000),
        )

        deliver_as = opts.get("deliverAs")
        if deliver_as == "nextTurn":
            self._pending_next_turn_messages.append(custom_message)
            return

        if self.is_streaming:
            if deliver_as == "followUp":
                self.agent.follow_up(custom_message)
            else:
                self.agent.steer(custom_message)
            return

        if opts.get("triggerTurn"):
            await self._run_agent_prompt(custom_message)
            return

        self.agent.append_message(custom_message)
        self.session_manager.append_custom_message_entry(
            custom_type, content, display, details
        )
        self._emit(MessageStartEvent(message=custom_message))
        self._emit(MessageEndEvent(message=custom_message))

    def create_replaced_session_context(self) -> Any:
        """创建用于 session 替换后的扩展上下文。

        复制 ``ExtensionCommandContext`` 的所有能力，并额外提供
        ``send_message`` / ``send_user_message``，使扩展在新会话上
        也能直接发送消息。
        """
        ctx = self.extension_runner.create_command_context()

        async def send_message(
            message: Dict[str, Any], options: Optional[Dict[str, Any]] = None
        ) -> None:
            return await self.send_custom_message(message, options)

        async def send_user_message(
            content: Union[str, List[Union[TextContent, ImageContent]]],
            options: Optional[Dict[str, Any]] = None,
        ) -> None:
            return await self.send_user_message(content, options)

        ctx.send_message = send_message
        ctx.send_user_message = send_user_message
        return ctx

    async def abort(self) -> None:
        """中止当前 Agent 运行并等待空闲。"""
        self.abort_retry()
        self.abort_compaction()
        self.abort_branch_summary()
        self.abort_user_tool()
        if hasattr(self.agent, "abort"):
            self.agent.abort()
        if hasattr(self.agent, "wait_for_idle"):
            await self.agent.wait_for_idle()

    # -------------------------------------------------------------------------
    # 队列模式管理
    # -------------------------------------------------------------------------

    def sync_queue_modes_from_settings(self) -> None:
        """从设置同步 steering/follow-up 模式。"""
        if hasattr(self.settings_manager, "get_steering_mode"):
            self.agent.steering_mode = self.settings_manager.get_steering_mode()
        if hasattr(self.settings_manager, "get_follow_up_mode"):
            self.agent.follow_up_mode = self.settings_manager.get_follow_up_mode()

    def set_steering_mode(self, mode: str) -> None:
        """设置 steering 模式并保存到设置。"""
        self.agent.steering_mode = mode
        if hasattr(self.settings_manager, "set_steering_mode"):
            self.settings_manager.set_steering_mode(mode)

    def set_follow_up_mode(self, mode: str) -> None:
        """设置 follow-up 模式并保存到设置。"""
        self.agent.follow_up_mode = mode
        if hasattr(self.settings_manager, "set_follow_up_mode"):
            self.settings_manager.set_follow_up_mode(mode)

    # -------------------------------------------------------------------------
    # 模型与思考级别
    # -------------------------------------------------------------------------

    async def set_model(self, model: Any) -> bool:
        return await self._model.set_model(model)

    async def cycle_model(
        self, direction: str = "forward"
    ) -> Optional[ModelCycleResult]:
        return await self._model.cycle_model(direction)

    def set_scoped_models(self, scoped_models: List[ScopedModelConfig]) -> None:
        self._model.set_scoped_models(scoped_models)

    async def set_thinking_level(
        self, level: Optional[Union[str, ModelThinkingLevel]]
    ) -> None:
        await self._model.set_thinking_level(level)

    def supports_thinking(self) -> bool:
        return self._model.supports_thinking()

    async def cycle_thinking_level(self) -> Optional[Any]:
        return await self._model.cycle_thinking_level()

    def get_available_thinking_levels(self) -> List[ModelThinkingLevel]:
        return self._model.get_available_thinking_levels()

    async def change_agent(self, name: str) -> None:
        """切换当前 Agent 配置（**角色初始态全量重建 + session_start 重放**）。

        全量重建 runtime：按新 agent 的名单重新过滤扩展、刷新工具注册表
        与系统提示词。激活集**不携带旧角色**——切换即新角色 yaml 初始态
        生效（open=初始激活集、strict=注册表闸门）；携带会击穿角色能力
        边界（旧角色的面板 delta 能把 worker 刻意不含的 subagent 塞回去）。

        与 reload() 同构重放 ``session_start``（reason=``"agent_change"``）：
        每个角色平等地走扩展启动钩子（条目恢复等）——工具面板等角色标签
        delta 按新角色匹配重放（切入即恢复该角色上次的面板状态）。
        恢复 handler 对 agent 条目在 agent_change 下跳过（否则分支里的旧
        角色条目会把刚切的角色切回去）。
        未知名由 AgentManager 抛 ``ValueError``（列出可用名）。
        """
        self.agent_manager.change_agent(name)
        self._build_runtime(active_tool_names=None)
        if self._has_extension_bindings() and self._extension_runner is not None:
            self.session_start_event = SessionStartEvent(reason="agent_change")
            await self._extension_runner.emit(self.session_start_event)
        self._emit_session_info_changed()

    async def save_agent(self, as_name: Optional[str] = None) -> Dict[str, Any]:
        """把当前生效状态物化为组合声明 yaml（``/agent save`` 落地）。

        编排点：当前模型 ref 现取注入 AgentManager；manager 写盘 + reload
        后，全量重建 runtime 让会话立即按新注册表运行（与 reload 同构的
        级联，保持当前激活工具集）；扩展在 reload 中重载（闭包态复位），
        故与 reload() 一样重放 session_start——条目持久化状态（tool-panel、
        subagent_allow、agent 切换等）经各扩展的恢复 handler 重建。
        """
        model = self.model
        model_ref = f"{model.provider}/{model.id}" if model is not None else None
        result = await self.agent_manager.save_agent(as_name, model_ref)
        self._build_runtime(active_tool_names=self.get_active_tool_names())
        if self._has_extension_bindings() and self._extension_runner is not None:
            self.session_start_event = SessionStartEvent(reason="reload")
            await self._extension_runner.emit(self.session_start_event)
            await self._extend_resources_from_extensions(reason="reload")
        return result

    # -------------------------------------------------------------------------
    # Agent 管理（当前角色旋钮的会话面——扩展命令经 context actions 调用）
    # -------------------------------------------------------------------------

    def _get_agent_entries(self) -> List[Dict[str, Any]]:
        """agents 注册表快照（/agent 选择器等扩展交互的数据源，含 current 标记）。"""
        return self.agent_manager.agent_entries()

    async def _save_agent(self, as_name: Optional[str] = None) -> Dict[str, Any]:
        """扩展 action 面：物化当前生效状态为组合声明 yaml（见 save_agent）。"""
        return await self.save_agent(as_name)

    # -------------------------------------------------------------------------
    # Persona 管理（override 旋钮的会话面——扩展命令经 context actions 调用）
    # -------------------------------------------------------------------------

    def _get_persona_entries(self) -> List[Dict[str, Any]]:
        """persona 注册表快照（/persona 选择器等扩展交互的数据源）。"""
        if self.persona_manager is None:
            return []
        entries: List[Dict[str, Any]] = []
        for persona in self.persona_manager.personas.values():
            info = persona.source_info
            entries.append(
                {
                    "name": persona.name,
                    "path": persona.file_path,
                    "source": info.source if info else "",
                    "scope": info.scope if info else "",
                    "origin": info.origin if info else "",
                }
            )
        return entries

    def _set_persona_override(self, name: str) -> None:
        """设置 persona override 并重建系统提示词（找不到由 manager 抛错）。"""
        if self.persona_manager is None:
            raise ValueError("persona 管理器不可用")
        self.persona_manager.set_persona_override(name)
        self._sync_system_prompt()
        self._emit_session_info_changed()

    def _clear_persona_override(self) -> None:
        """清除 persona override（恢复角色默认装配）并重建系统提示词。"""
        if self.persona_manager is None:
            return
        self.persona_manager.clear_persona_override()
        self._sync_system_prompt()
        self._emit_session_info_changed()

    # -------------------------------------------------------------------------
    # 工具管理
    # -------------------------------------------------------------------------

    def get_active_tool_names(self) -> List[str]:
        return self._tools.get_active_names()

    def get_all_tools(self) -> List[Any]:
        return self._tools.get_all_tools()

    def get_available_tools_info(self) -> List[Any]:
        """返回所有可用工具的 ToolInfo 列表。"""
        return self._tools.get_all_tools()

    def refresh_tools(self) -> None:
        self._tools.refresh()

    def set_active_tools_by_name(self, tool_names: List[str]) -> None:
        self._tools.set_active_by_name(tool_names)

    # -------------------------------------------------------------------------
    # 会话管理
    # -------------------------------------------------------------------------

    def _emit_session_info_changed(self) -> None:
        """发射会话信息变更（三字段当前全量值——payload 即完整事实）。"""
        event = SessionInfoChangedEvent(
            name=self.session_manager.get_session_name(),
            agent=(
                self.agent_manager.current
                if getattr(self, "agent_manager", None) is not None
                else None
            ),
            persona_override=(
                self.persona_manager.current_override
                if getattr(self, "persona_manager", None) is not None
                else None
            ),
        )
        self._emit(event)

    def append_entry(self, entry_type: str, data: Optional[Any] = None) -> str:
        """追加自定义条目 + 发 entry_appended 事件（实时进 transcript）。

        扩展 action 与 appendEntry RPC 共用同一入口。对齐 pi：仅 custom 条目
        有此事件——消息/压缩等条目各有专属通道，前端只消费 custom。
        """
        entry_id = self.session_manager.append_custom_entry(entry_type, data)
        entry = self.session_manager.get_entry(entry_id)
        if entry is not None:
            self._emit(EntryAppendedEvent(entry=entry))
        return entry_id

    def set_session_name(self, name: str) -> None:
        """设置会话显示名称并发射事件。"""
        self.session_manager.append_session_info(name)
        self._emit_session_info_changed()
        # 双发到扩展面（对齐 pi setSessionName：Bus 2 呈现 + runner 行为介入）；
        # 本方法同步，runner.emit 为异步，fire-and-forget 调度
        event = SessionInfoChangedEvent(name=self.session_manager.get_session_name())
        runner = self._extension_runner
        if runner is not None and runner.has_handlers(event.type):
            asyncio.create_task(runner.emit(event))

    def set_label(self, entry_id: str, label: Optional[str]) -> None:
        """给会话条目设置/清除标签（对齐 pi setLabel）。"""
        self.session_manager.append_label_change(entry_id, label)

    def get_user_message_text(self, message: Any) -> str:
        """从 UserMessage 中提取文本。"""
        if getattr(message, "role", None) != "user":
            return ""
        return extract_text_from_content(getattr(message, "content", ""))

    async def fork_session(
        self, entry_id: str, position: str = "before"
    ) -> Dict[str, Any]:
        """在指定条目处 fork 出新的分支会话。

        返回中携带 ``selectedText``/``editorText``（position != "at" 时被选
        user 消息的原文，供前端回填编辑器——对齐 pi fork 语义；
        ``editorText`` 与 navigateTree 结果的同名字段对齐，两者同值）。
        """
        if position not in ("at", "before", "after"):
            raise ValueError(f"Invalid fork position: {position}")

        target_leaf_id = entry_id
        selected_text: Optional[str] = None
        if position != "at":
            selected_entry = self.session_manager.get_entry(entry_id)
            if selected_entry is None:
                raise ValueError(f"Entry {entry_id} not found")
            if selected_entry.type != "message" or not isinstance(
                selected_entry.message, UserMessage
            ):
                raise ValueError("Invalid entry ID for forking")
            target_leaf_id = selected_entry.parent_id
            selected_text = self.get_user_message_text(selected_entry.message)

        self.session_manager.create_branched_session(target_leaf_id)
        self.agent.state.messages = (
            self.session_manager.build_session_context().messages
        )
        self._sync_system_prompt()
        self.session_start_event = SessionStartEvent(
            reason="fork", previous_session_file=self.session_file
        )
        if self._extension_runner is not None:
            await self._extension_runner.emit(self.session_start_event)
        self._emit(SessionReplacedEvent(reason="fork"))
        return {
            "cancelled": False,
            "selectedText": selected_text,
            "editorText": selected_text,
        }

    async def clone_session(self) -> Dict[str, Any]:
        """克隆当前会话到一个新的会话文件并切换到该会话。"""
        import shutil

        if not self.session_manager.is_persisted():
            raise RuntimeError("Clone is only supported for persisted sessions")

        session_file = self.session_manager.get_session_file()
        session_dir = self.session_manager.get_session_dir()
        if not session_file or not session_dir:
            raise RuntimeError("Current session is not persisted")
        # 全新会话（零条目）时文件尚未落盘——裸 copy 会抛 ENOENT，
        # 给用户可读的错误而不是系统错误码
        if not os.path.exists(session_file):
            raise RuntimeError("当前会话还没有内容，无法克隆")

        from nova_harness.core.harness.session.utils import (
            generate_session_id,
        )

        new_session_id = generate_session_id()
        timestamp = datetime.now().isoformat().replace(":", "-").replace(".", "-")
        new_file = os.path.join(session_dir, f"{timestamp}_{new_session_id}.jsonl")
        shutil.copy2(session_file, new_file)

        # 重写头部，使用新的 session id，避免与原始会话冲突
        header = None
        with open(new_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if lines:
            header_data = json.loads(lines[0])
            header_data["id"] = new_session_id
            header_data["timestamp"] = datetime.now().isoformat()
            lines[0] = json.dumps(header_data, ensure_ascii=False) + "\n"
        with open(new_file, "w", encoding="utf-8") as f:
            f.writelines(lines)

        self.session_manager = SessionManager.open(new_file, session_dir)
        self.agent.state.messages = (
            self.session_manager.build_session_context().messages
        )
        self._sync_system_prompt()
        self.session_start_event = SessionStartEvent(
            reason="clone", previous_session_file=session_file
        )
        if self._extension_runner is not None:
            await self._extension_runner.emit(self.session_start_event)
        self._emit(SessionReplacedEvent(reason="clone"))
        return {"cancelled": False}

    async def export_session(self, path: str) -> Dict[str, Any]:
        """将当前会话导出为 JSONL 文件。"""
        import shutil

        session_file = self.session_manager.get_session_file()
        if not session_file:
            raise RuntimeError("No session file to export")
        # 全新会话（零条目）时文件尚未落盘——裸 copy 会抛 ENOENT，
        # 给用户可读的错误而不是系统错误码
        if not os.path.exists(session_file):
            raise RuntimeError("当前会话还没有内容可导出")
        shutil.copy2(session_file, path)
        return {"exported_to": os.path.abspath(path)}

    async def import_session(
        self, path: str, cwd_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """从 JSONL 文件导入会话并切换到该会话。"""
        import shutil

        resolved_path = os.path.abspath(path)
        if not os.path.exists(resolved_path):
            raise FileNotFoundError(f"Session file not found: {resolved_path}")

        session_dir = self.session_manager.get_session_dir()
        previous_session_file = self.session_file
        if session_dir:
            destination_path = os.path.join(
                session_dir, os.path.basename(resolved_path)
            )
            if os.path.abspath(destination_path) != os.path.abspath(resolved_path):
                if os.path.exists(destination_path):
                    # 不与已有会话文件重名：追加时间戳后缀，避免静默覆盖现有会话
                    stem, dot_ext = os.path.splitext(os.path.basename(resolved_path))
                    destination_path = os.path.join(
                        session_dir,
                        f"{stem}-import-{int(time.time() * 1000)}{dot_ext}",
                    )
                shutil.copy2(resolved_path, destination_path)
        else:
            destination_path = resolved_path

        self.session_manager = SessionManager.open(
            destination_path, session_dir, cwd_override
        )
        self.agent.state.messages = (
            self.session_manager.build_session_context().messages
        )
        self._sync_system_prompt()
        self.session_start_event = SessionStartEvent(
            reason="import", previous_session_file=previous_session_file
        )
        if self._extension_runner is not None:
            await self._extension_runner.emit(self.session_start_event)
        self._emit(SessionReplacedEvent(reason="import"))
        return {"cancelled": False}

    async def new_agent_session(
        self, parent_session: Optional[str] = None
    ) -> Dict[str, Any]:
        """创建新会话并切换到该会话。"""
        previous_session_file = self.session_file
        self.session_manager.new_session(parent_session=parent_session)
        self.agent.state.messages = (
            self.session_manager.build_session_context().messages
        )
        self._sync_system_prompt()
        self.session_start_event = SessionStartEvent(
            reason="new", previous_session_file=previous_session_file
        )
        if self._extension_runner is not None:
            await self._extension_runner.emit(self.session_start_event)
        self._emit(SessionReplacedEvent(reason="new"))
        return {"cancelled": False}

    async def switch_agent_session(self, session_path: str) -> Dict[str, Any]:
        """切换到指定的会话文件。"""
        previous_session_file = self.session_file
        session_dir = self.session_manager.get_session_dir()
        self.session_manager = SessionManager.open(session_path, session_dir)
        self.agent.state.messages = (
            self.session_manager.build_session_context().messages
        )
        self._sync_system_prompt()
        self.session_start_event = SessionStartEvent(
            reason="resume", previous_session_file=previous_session_file
        )
        if self._extension_runner is not None:
            await self._extension_runner.emit(self.session_start_event)
        self._emit(SessionReplacedEvent(reason="resume"))
        return {"cancelled": False}

    def get_session_info(self) -> Dict[str, Any]:
        """获取当前会话摘要信息。"""
        header = self.session_manager.get_header()
        return {
            "id": self.session_manager.get_session_id(),
            "name": self.session_manager.get_session_name(),
            "cwd": getattr(header, "cwd", self.cwd) if header else self.cwd,
            "file": self.session_manager.get_session_file(),
            "entry_count": len(self.session_manager.get_entries()),
            "leaf_id": self.session_manager.get_leaf_id(),
            "persisted": self.session_manager.is_persisted(),
        }

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """列出当前 cwd 下的可用会话。"""
        sessions = await SessionManager.list_sessions(
            self.cwd, self.session_manager.get_session_dir()
        )
        return [s.model_dump() for s in sessions]

    def trust_project(self, trusted: bool = True) -> None:
        """保存项目信任决策（持久化 trust.json + 进程内即时生效）。

        进程内翻转（settings_manager）让当前会话立即按新裁决加载/卸载项目资源；
        持久化（ProjectTrustStore → trust.json）让重启后决策不丢——此前只做
        内存翻转，/trust 形同"重启即忘"。
        """
        if self.settings_manager is not None and hasattr(
            self.settings_manager, "set_project_trusted"
        ):
            self.settings_manager.set_project_trusted(trusted)
        from nova_harness.core.config.defaults import get_agent_dir
        from nova_harness.core.harness.project_trust import ProjectTrustStore

        ProjectTrustStore.for_agent_dir(str(get_agent_dir())).set(self.cwd, trusted)

    def _find_last_assistant_message(self) -> Optional[AssistantMessage]:
        """在 agent state 中查找最后一条 assistant 消息。"""
        messages = list(getattr(self.agent.state, "messages", []))
        for msg in reversed(messages):
            if getattr(msg, "role", None) == "assistant":
                return msg
        return None

    # -------------------------------------------------------------------------
    # 压缩
    # -------------------------------------------------------------------------

    async def compact(
        self, custom_instructions: Optional[str] = None
    ) -> CompactionResult:
        return await self._compaction.compact(custom_instructions)

    def abort_compaction(self) -> None:
        self._compaction.abort_compaction()

    def abort_branch_summary(self) -> None:
        self._compaction.abort_branch_summary()

    def set_auto_compaction_enabled(self, enabled: bool) -> None:
        self._compaction.set_auto_compaction_enabled(enabled)

    # -------------------------------------------------------------------------
    # 树导航
    # -------------------------------------------------------------------------

    async def navigate_tree(
        self,
        target_id: str,
        options: Optional[Union[NavigateOptions, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return await self._tree.navigate(target_id, options)

    def get_user_messages_for_forking(self) -> List[Dict[str, str]]:
        return self._tree.get_user_messages_for_forking()

    # -------------------------------------------------------------------------
    # 统计与上下文用量
    # -------------------------------------------------------------------------

    def get_context_usage(self) -> Optional[Dict[str, Any]]:
        return self._stats.get_context_usage()

    def get_session_stats(self) -> SessionStats:
        return self._stats.get_session_stats()

    def get_cache_waste(self) -> Any:
        """会话级缓存浪费汇总（定价查询源取当前 ModelRuntime）。"""
        return self._stats.get_cache_waste(self.model_runtime)

    # -------------------------------------------------------------------------
    # 订阅
    # -------------------------------------------------------------------------

    def subscribe(self, listener: Callable[[Any], None]) -> Callable[[], None]:
        """订阅 AgentSession 事件；返回取消订阅函数。"""
        self._event_listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

        return unsubscribe
