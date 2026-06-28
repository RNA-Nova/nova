"""AgentSession - Agent 生命周期与会话管理核心。

本实现持续向 TypeScript 版 ``agent-session.ts`` 全额对齐，
在保持 Python 侧现有结构（``AgentSessionConfig``、``ExtensionRunner``、
``SystemPromptManager`` 等）的前提下，补齐事件体系、自动重试、自动压缩、
模型循环、队列管理、工具元数据等能力。

为降低 ``AgentSession`` 的复杂度，具体的领域逻辑已拆分到
``core/controllers/`` 下的各控制器；``AgentSession`` 负责编排入口、
事件总线与生命周期管理。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Union

from nova_agent import (
    Agent,
    AgentMessage,
    ThinkingLevel,
)
from nova_ai import (
    AssistantMessage,
    ImageContent,
    TextContent,
    UserMessage,
)

from nova_harness.core.agent_session.controllers import (
    BashController,
    CommandDispatcher,
    CompactionController,
    EventController,
    ModelController,
    QueueController,
    RetryController,
    StatsCollector,
    ToolController,
    TreeNavigator,
)
from nova_harness.core.agent_session.extensions import ExtensionRunner
from nova_harness.core.agent_session.options import AgentSessionConfig
from nova_harness.core.harness.skills import expand_skill_command
from nova_harness.core.resources.loaders.prompt_templates import expand_prompt_template
from nova_harness.core.types.agent import (
    ModelCycleResult,
    NavigateOptions,
    PromptOptions,
    ScopedModelConfig,
    SessionStats,
)
from nova_harness.core.types.compaction import CompactionResult
from nova_harness.core.types.events import (
    AutoRetryEndEvent,
    MessageEndEvent,
    MessageStartEvent,
    SessionInfoChangedEvent,
    SessionShutdownEvent,
    SessionStartEvent,
    TurnEndEvent,
)
from nova_harness.core.types.messages import BashExecutionMessage
from nova_harness.core.types.resource import (
    ResourceExtensionPathEntry,
    ResourceExtensionPaths,
)
from nova_harness.core.utils.messages import extract_text_from_content

# ============================================================================
# AgentSession
# ============================================================================


class AgentSession:
    """Agent 会话核心：状态、事件、模型、工具、压缩、树导航。"""

    # 为了让 MagicMock(spec=AgentSession) 能访问到这些实例属性，
    # 在类层级做类型标注并给默认占位值（实际值仍在 __init__ 中设置）。
    config: AgentSessionConfig = None  # type: ignore[assignment]
    agent: Agent = None  # type: ignore[assignment]
    session_manager: Any = None
    settings_manager: Any = None
    cwd: str = ""
    system_prompt_manager: Any = None
    _resource_loader: Any = None
    _model_registry: Any = None
    scoped_models: List[ScopedModelConfig] = []
    initial_active_tool_names: List[str] = []
    base_tools_override: Optional[Dict[str, Any]] = None
    services: Any = None
    extension_runner_ref: Optional[Dict[str, Optional[Any]]] = None
    session_start_event: SessionStartEvent = None  # type: ignore[assignment]
    _runtime: Optional[Any] = None
    _extension_runner: Optional[ExtensionRunner] = None
    _unsubscribe_agent: Optional[Callable[[], None]] = None
    _event_listeners: List[Callable[[Any], None]] = []
    _tool_registry: Dict[str, Any] = {}
    _tool_definitions: Dict[str, Any] = {}
    _steering_messages: List[str] = []
    _follow_up_messages: List[str] = []
    _pending_next_turn_messages: List[AgentMessage] = []
    _pending_bash_messages: List[BashExecutionMessage] = []
    _last_assistant_message: Optional[AssistantMessage] = None
    _bash_abort_event: Optional[asyncio.Event] = None
    _retry_attempt: int = 0
    _retry_abort_event: Optional[asyncio.Event] = None
    _overflow_recovery_attempted: bool = False
    _compaction_abort_controller: Optional[Any] = None
    _auto_compaction_abort_controller: Optional[Any] = None
    _branch_summary_abort_controller: Optional[Any] = None
    _base_system_prompt: str = ""

    # Extension binding state（用于 reload 后恢复绑定）
    _extension_ui_context: Optional[Any] = None
    _extension_mode: Optional[str] = None
    _extension_command_context_actions: Optional[Any] = None
    _extension_abort_handler: Optional[Callable[[], None]] = None
    _extension_shutdown_handler: Optional[Callable[[], None]] = None
    _extension_error_listener: Optional[Callable[[Any], None]] = None

    # 领域控制器
    _retry: RetryController = None  # type: ignore[assignment]
    _compaction: CompactionController = None  # type: ignore[assignment]
    _bash: BashController = None  # type: ignore[assignment]
    _events: EventController = None  # type: ignore[assignment]
    _model: ModelController = None  # type: ignore[assignment]
    _tools: ToolController = None  # type: ignore[assignment]
    _queue: QueueController = None  # type: ignore[assignment]
    _tree: TreeNavigator = None  # type: ignore[assignment]
    _stats: StatsCollector = None  # type: ignore[assignment]
    _commands: CommandDispatcher = None  # type: ignore[assignment]

    def __init__(self, config: AgentSessionConfig) -> None:
        self.config = config
        self.agent: Agent = config.agent
        self.session_manager = config.session_manager
        self.settings_manager = config.settings_manager
        self.cwd: str = config.cwd
        self.system_prompt_manager = config.system_prompt_manager
        self._resource_loader = config.resource_loader
        self._model_registry = config.model_registry
        self.scoped_models: List[ScopedModelConfig] = config.scoped_models or []
        self.initial_active_tool_names: List[str] = (
            config.initial_active_tool_names or []
        )
        self.base_tools_override: Optional[Dict[str, Any]] = config.base_tools_override

        # 服务集合：优先使用 config.services，否则用 SimpleNamespace 构造最小对象
        self.services = config.services or self._make_services()
        self.extension_runner_ref = config.extension_runner_ref
        self.session_start_event: SessionStartEvent = (
            config.session_start_event or SessionStartEvent(reason="new")
        )

        self._runtime: Optional[Any] = None
        self._extension_runner: Optional[ExtensionRunner] = None
        self._unsubscribe_agent: Optional[Callable[[], None]] = None
        self._event_listeners: List[Callable[[Any], None]] = []

        self._tool_registry: Dict[str, Any] = {}
        self._tool_definitions: Dict[str, Any] = {}
        self._steering_messages: List[str] = []
        self._follow_up_messages: List[str] = []
        self._pending_next_turn_messages: List[AgentMessage] = []
        self._pending_bash_messages: List[BashExecutionMessage] = []
        self._last_assistant_message: Optional[AssistantMessage] = None
        self._bash_abort_event: Optional[asyncio.Event] = None
        self._retry_attempt: int = 0
        self._retry_abort_event: Optional[asyncio.Event] = None
        self._overflow_recovery_attempted: bool = False
        self._compaction_abort_controller: Optional[Any] = None
        self._auto_compaction_abort_controller: Optional[Any] = None
        self._branch_summary_abort_controller: Optional[Any] = None
        self._base_system_prompt: str = ""

        self._extension_ui_context: Optional[Any] = None
        self._extension_mode: Optional[str] = None
        self._extension_command_context_actions: Optional[Any] = None
        self._extension_abort_handler: Optional[Callable[[], None]] = None
        self._extension_shutdown_handler: Optional[Callable[[], None]] = None
        self._extension_error_listener: Optional[Callable[[Any], None]] = None

        # 初始化控制器
        self._retry = RetryController(self)
        self._compaction = CompactionController(self)
        self._bash = BashController(self)
        self._events = EventController(self)
        self._model = ModelController(self)
        self._tools = ToolController(self)
        self._queue = QueueController(self)
        self._tree = TreeNavigator(self)
        self._stats = StatsCollector(self)
        self._commands = CommandDispatcher(self)

        self._subscribe_agent_events()
        self._install_agent_hooks()
        self._build_runtime()
        self.sync_queue_modes_from_settings()

    # -------------------------------------------------------------------------
    # 内部构造
    # -------------------------------------------------------------------------

    def _make_services(self) -> Any:
        """当 config.services 未提供时，构造 ExtensionRunner 需要的最小 services。"""
        return SimpleNamespace(
            cwd=self.cwd,
            session_manager=self.session_manager,
            settings_manager=self.settings_manager,
            model_registry=self.model_registry,
            resource_loader=self.resource_loader,
        )

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
            if runner is None or not runner.has_handlers("tool_call"):
                return None
            tool_call = ctx.tool_call
            result = await runner.emit_tool_call(
                type(
                    "ToolCallEvent",
                    (),
                    {
                        "type": "tool_call",
                        "tool_call_id": getattr(tool_call, "id", ""),
                        "tool_name": getattr(tool_call, "name", ""),
                        "args": getattr(ctx, "args", {}),
                    },
                )()
            )
            if getattr(result, "block", False):
                from nova_agent import BeforeToolCallResult

                return BeforeToolCallResult(
                    block=True, reason=getattr(result, "reason", None)
                )
            return None

        async def after_tool_call(ctx: Any, signal: Optional[Any] = None) -> Any:
            runner = self._extension_runner
            if runner is None or not runner.has_handlers("tool_result"):
                return None
            tool_call = ctx.tool_call
            result = await runner.emit_tool_result(
                type(
                    "ToolResultEvent",
                    (),
                    {
                        "type": "tool_result",
                        "tool_call_id": getattr(tool_call, "id", ""),
                        "tool_name": getattr(tool_call, "name", ""),
                        "args": getattr(ctx, "args", {}),
                        "content": getattr(ctx.result, "content", []),
                        "details": getattr(ctx.result, "details", None),
                        "is_error": getattr(ctx, "is_error", False),
                    },
                )()
            )
            if result is None:
                return None
            from nova_agent import AfterToolCallResult

            return AfterToolCallResult(
                content=getattr(result, "content", None),
                details=getattr(result, "details", None),
                is_error=getattr(result, "is_error", None),
            )

        async def prepare_next_turn(ctx: Any) -> Any:
            runner = self._extension_runner
            if runner is None:
                return None
            event = TurnEndEvent(
                message=getattr(ctx, "message", None),
                tool_results=getattr(ctx, "tool_results", []),
            )
            return await runner.emit_prepare_next_turn(event)

        async def should_stop_after_turn(ctx: Any) -> bool:
            runner = self._extension_runner
            if runner is None:
                return False
            event = TurnEndEvent(
                message=getattr(ctx, "message", None),
                tool_results=getattr(ctx, "tool_results", []),
            )
            return await runner.emit_should_stop_after_turn(event)

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

    def _build_runtime(
        self,
        active_tool_names: Optional[List[str]] = None,
        flag_values: Optional[Dict[str, Any]] = None,
        include_all_extension_tools: bool = True,
    ) -> None:
        """初始化扩展 runner、工具注册表与系统提示词。"""
        from nova_harness.core.types.extensions import LoadedExtensionsResult

        raw_extensions_result = self.resource_loader.get_extensions()
        if isinstance(raw_extensions_result, LoadedExtensionsResult):
            extensions = raw_extensions_result.extensions
        else:
            # 兼容测试用 mock（未配置 get_extensions 返回值）
            extensions = []

        self._extension_runner = ExtensionRunner(
            services=self.services,
            extensions=extensions,
        )
        if flag_values:
            self._extension_runner._flag_values = dict(flag_values)
        if self.extension_runner_ref is not None:
            self.extension_runner_ref["current"] = self._extension_runner

        self._extension_runner.bind_session(self)
        self._apply_extension_bindings(self._extension_runner)
        self._tools.refresh_registry(
            active_tool_names=active_tool_names,
            include_all_extension_tools=include_all_extension_tools,
        )
        self._sync_system_prompt()

    def _sync_system_prompt(self) -> None:
        """用当前配置和工具白名单重建系统提示词。"""
        from nova_harness.core.types.agent_config import DynamicContext

        context = DynamicContext(cwd=self.cwd, session_id=str(self.session_id))
        self._base_system_prompt = self.system_prompt_manager.build_system_prompt(
            context
        )
        self.agent.state.system_prompt = self._base_system_prompt

    # -------------------------------------------------------------------------
    # 属性
    # -------------------------------------------------------------------------

    @property
    def state(self) -> Any:
        return self.agent.state

    @property
    def model(self) -> Optional[Any]:
        return getattr(self.agent.state, "model", None)

    @property
    def thinking_level(self) -> Optional[ThinkingLevel]:
        return getattr(self.agent.state, "thinking_level", None)

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
        return bool(self.settings_manager.get_retry_enabled())

    @property
    def auto_compaction_enabled(self) -> bool:
        return bool(self.settings_manager.get_compaction_enabled())

    @property
    def retry_attempt(self) -> int:
        return self._retry.attempt

    @property
    def resource_loader(self) -> Any:
        return self._resource_loader

    @property
    def model_registry(self) -> Any:
        return self._model_registry

    @property
    def prompt_templates(self) -> List[Any]:
        return list(self._resource_loader.get_prompts().get("prompts", []))

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

    def bind_runtime(self, runtime: Any) -> None:
        """绑定 AgentSessionRuntime，激活扩展中的会话控制 action。"""
        self._runtime = runtime
        if self._extension_runner is not None:
            self._extension_runner.bind_runtime(runtime)

    async def bind_extensions(self, bindings: Optional[Dict[str, Any]] = None) -> None:
        """扩展加载完成后绑定 UI/动作回调，并触发 session_start 与资源发现。"""
        if bindings is not None:
            self._extension_ui_context = bindings.get("ui_context")
            self._extension_mode = bindings.get("mode")
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

    def _apply_extension_bindings(self, runner: ExtensionRunner) -> None:
        """把保存的错误监听器等回调应用到指定 runner。

        UI context / mode / command actions 当前由 runner 在构造后通过
        session/runtime 绑定间接使用，这里仅注册显式错误监听器。
        """
        if self._extension_error_listener is not None:
            runner.register_error_handler(self._extension_error_listener)

    async def reload(self) -> None:
        """重新加载设置、资源与扩展，并刷新当前 session 的系统提示词。"""
        previous_flag_values: Dict[str, Any] = {}
        if self._extension_runner is not None:
            previous_flag_values = self._extension_runner.get_flag_values()
            await self._extension_runner.emit(SessionShutdownEvent(reason="reload"))

        if hasattr(self.settings_manager, "reload"):
            self.settings_manager.reload()
        self.sync_queue_modes_from_settings()

        if hasattr(self.resource_loader, "reload"):
            await self.resource_loader.reload()

        self._build_runtime(
            active_tool_names=self.get_active_tool_names(),
            flag_values=previous_flag_values,
            include_all_extension_tools=True,
        )

        if self._has_extension_bindings() and self._extension_runner is not None:
            self.session_start_event = SessionStartEvent(reason="reload")
            await self._extension_runner.emit(self.session_start_event)
            await self._extend_resources_from_extensions(reason="reload")

    def _has_extension_bindings(self) -> bool:
        return any(
            (
                self._extension_ui_context is not None,
                self._extension_mode is not None,
                self._extension_command_context_actions is not None,
                self._extension_shutdown_handler is not None,
                self._extension_error_listener is not None,
            )
        )

    async def _extend_resources_from_extensions(self, reason: str = "startup") -> None:
        """收集扩展贡献的临时资源路径并重新加载受影响资源。"""
        if self._extension_runner is None:
            return
        if not self._extension_runner.has_handlers("resources_discover"):
            return

        result = await self._extension_runner.emit_resources_discover(self.cwd, reason)
        if not (result.skill_paths or result.prompt_paths or result.theme_paths):
            return

        paths = ResourceExtensionPaths(
            skill_paths=[
                ResourceExtensionPathEntry(path=p) for p in result.skill_paths
            ],
            prompt_paths=[
                ResourceExtensionPathEntry(path=p) for p in result.prompt_paths
            ],
            theme_paths=[
                ResourceExtensionPathEntry(path=p) for p in result.theme_paths
            ],
        )
        self.resource_loader.extend_resources(paths)
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
            self.abort_bash()
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
            # 扩展命令立即执行（不排队）
            if expand_prompt_templates and current_text.startswith("/"):
                handled = await self._commands.try_execute(current_text)
                if handled:
                    if preflight_result is not None:
                        preflight_result(True)
                    return

            # 扩展 input 事件拦截
            if (
                self._extension_runner is not None
                and self._extension_runner.has_handlers("input")
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

            # skill 命令与 prompt 模板展开
            if expand_prompt_templates:
                current_text = expand_skill_command(
                    current_text, self.resource_loader.get_skills()
                )
                current_text = expand_prompt_template(
                    current_text, self.resource_loader.get_prompts().get("prompts", [])
                )

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

            # 刷新待处理 bash 消息
            self._bash.flush_pending()

            # 校验模型
            if self.model is None:
                raise RuntimeError("No model selected")

            api_key = None
            if hasattr(self.model_registry, "get_api_key"):
                api_key = await self.model_registry.get_api_key(self.model)
            if not api_key:
                raise RuntimeError(
                    f"No API key found for provider {self.model.provider}"
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
                    self._bash.flush_pending()

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
                current_system_prompt, extra_messages = (
                    await self._extension_runner.emit_before_agent_start(
                        current_text,
                        current_images,
                        self._base_system_prompt,
                        {"cwd": self.cwd},
                    )
                )
                if current_system_prompt is not None:
                    self.agent.state.system_prompt = current_system_prompt
                if extra_messages:
                    messages.extend(extra_messages)
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

    async def execute_bash(
        self,
        command: str,
        on_chunk: Optional[Callable[[str], None]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Any:
        return await self._bash.execute_bash(command, on_chunk, options)

    def record_bash_result(
        self,
        command: str,
        result: Any,
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._bash.record_bash_result(command, result, options)

    def abort_bash(self) -> None:
        self._bash.abort_bash()

    @property
    def is_bash_running(self) -> bool:
        return self._bash.is_running

    @property
    def has_pending_bash_messages(self) -> bool:
        return self._bash.has_pending_messages

    async def _run_agent_prompt(
        self, messages: Union[AgentMessage, List[AgentMessage]]
    ) -> None:
        """执行一次 Agent prompt，并在之后处理续话/重试/压缩。"""
        try:
            await self.agent.prompt(messages)
            while await self._handle_post_agent_run():
                await self.agent.continue_()
        finally:
            self._bash.flush_pending()

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
        if text.startswith("/"):
            self._commands.throw_if_extension_command(text)
        expanded = expand_skill_command(text, self.resource_loader.get_skills())
        expanded = expand_prompt_template(
            expanded, self.resource_loader.get_prompts().get("prompts", [])
        )
        await self._queue.steer(expanded, images)

    async def follow_up(
        self, text: str, images: Optional[List[ImageContent]] = None
    ) -> None:
        """在 Agent 完成当前 turn 后追加一条 follow-up 消息。"""
        if text.startswith("/"):
            self._commands.throw_if_extension_command(text)
        expanded = expand_skill_command(text, self.resource_loader.get_skills())
        expanded = expand_prompt_template(
            expanded, self.resource_loader.get_prompts().get("prompts", [])
        )
        await self._queue.follow_up(expanded, images)

    def clear_queue(self) -> Dict[str, List[str]]:
        return self._queue.clear()

    def get_steering_messages(self) -> List[str]:
        return self._queue.get_steering()

    def get_follow_up_messages(self) -> List[str]:
        return self._queue.get_follow_up()

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
        custom_type = message.get("custom_type", message.get("customType", ""))
        content = message.get("content", "")
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

    async def abort(self) -> None:
        """中止当前 Agent 运行并等待空闲。"""
        self.abort_retry()
        self.abort_compaction()
        self.abort_bash()
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

    async def set_model(self, model: Any) -> None:
        await self._model.set_model(model)

    async def cycle_model(
        self, direction: str = "forward"
    ) -> Optional[ModelCycleResult]:
        return await self._model.cycle_model(direction)

    def set_scoped_models(self, scoped_models: List[ScopedModelConfig]) -> None:
        self._model.set_scoped_models(scoped_models)

    async def set_thinking_level(
        self, level: Optional[Union[str, ThinkingLevel]]
    ) -> None:
        await self._model.set_thinking_level(level)

    def supports_thinking(self) -> bool:
        return self._model.supports_thinking()

    _supports_thinking = supports_thinking

    def cycle_thinking_level(self) -> Optional[ThinkingLevel]:
        return self._model.cycle_thinking_level()

    def get_available_thinking_levels(self) -> List[ThinkingLevel]:
        return self._model.get_available_thinking_levels()

    def change_agent(self, name: str) -> None:
        """切换当前 Agent 配置。"""
        self.system_prompt_manager.change_agent(name)
        self._sync_system_prompt()

    # -------------------------------------------------------------------------
    # 工具管理
    # -------------------------------------------------------------------------

    def get_active_tool_names(self) -> List[str]:
        return self._tools.get_active_names()

    def get_all_tools(self) -> List[Any]:
        return self._tools.get_all_tools()

    def get_tool_definition(self, name: str) -> Optional[Any]:
        return self._tools.get_definition(name)

    def refresh_tools(self) -> None:
        self._tools.refresh()

    def set_active_tools_by_name(self, tool_names: List[str]) -> None:
        self._tools.set_active_by_name(tool_names)

    # -------------------------------------------------------------------------
    # 会话管理
    # -------------------------------------------------------------------------

    def set_session_name(self, name: str) -> None:
        """设置会话显示名称并发射事件。"""
        self.session_manager.append_session_info(name)
        self._emit(
            SessionInfoChangedEvent(name=self.session_manager.get_session_name())
        )

    def get_user_message_text(self, message: Any) -> str:
        """从 UserMessage 中提取文本。"""
        if getattr(message, "role", None) != "user":
            return ""
        return extract_text_from_content(getattr(message, "content", ""))

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
