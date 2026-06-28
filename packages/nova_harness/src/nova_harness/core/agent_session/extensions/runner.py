"""
ExtensionRunner — 扩展系统的中央调度器。

负责：
1. 持有所有已加载扩展
2. 事件分发（普通 emit + 特殊合并语义）
3. 绑定 AgentSession / AgentSessionRuntime 提供的 action
4. 把扩展工具包装成 AgentTool
5. 在扩展出错时广播 extension_error
"""

from __future__ import annotations

import inspect
import traceback
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
)

from nova_agent import AgentLoopTurnUpdate, AgentTool

from nova_harness.core.agent_session.extensions.context import (
    ExtensionCommandContext,
    ExtensionContext,
)
from nova_harness.core.types.diagnostics import AgentSessionRuntimeDiagnostic
from nova_harness.core.types.events import (
    ExtensionErrorEvent,
    ResourcesDiscoverEventResult,
)
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionCommand,
    ExtensionEventBus,
    ExtensionFlag,
    ExtensionMessageRenderer,
    ExtensionShortcut,
    ExtensionToolDefinition,
)
from nova_harness.core.types.tools import DynamicTool, ToolDefinition

if TYPE_CHECKING:
    from nova_harness.core.agent_session.services import AgentSessionServices


class ExtensionRunner:
    """
    扩展 runner：由 AgentSession 持有，负责扩展事件分发和 action 委托。
    """

    def __init__(
        self,
        services: AgentSessionServices,
        extensions: List[Extension],
        event_bus: Optional[ExtensionEventBus] = None,
    ) -> None:
        self.services = services
        self.extensions = list(extensions)
        self._event_bus = event_bus or ExtensionEventBus()
        self._error_handlers: List[Callable[[ExtensionErrorEvent], Any]] = []
        self._diagnostics: List[AgentSessionRuntimeDiagnostic] = []
        self._session: Optional[Any] = None
        self._runtime: Optional[Any] = None
        self._invalid = False
        self._flag_values: Dict[str, Any] = {}

    # -------------------------------------------------------------------------
    # Binding lifecycle
    # -------------------------------------------------------------------------

    def bind_session(self, session: Any) -> None:
        """绑定当前 AgentSession，激活需要会话的 action。"""
        self._assert_active()
        self._session = session

    def bind_runtime(self, runtime: Any) -> None:
        """绑定 AgentSessionRuntime，激活会话控制 action。"""
        self._assert_active()
        self._runtime = runtime

    def invalidate(self) -> None:
        """使 runner 失效（session 切换/销毁时调用）。"""
        self._invalid = True
        self._session = None
        self._runtime = None
        self.event_bus.clear()

    def add_diagnostic(
        self, diagnostic_type: Literal["info", "warning", "error"], message: str
    ) -> None:
        """记录一条非致命诊断信息（ExtensionAPIContext 协议方法）。"""
        self._diagnostics.append(
            AgentSessionRuntimeDiagnostic(type=diagnostic_type, message=message)
        )

    def drain_diagnostics(self) -> List[AgentSessionRuntimeDiagnostic]:
        """取出并清空当前累积的诊断信息。"""
        drained = self._diagnostics.copy()
        self._diagnostics.clear()
        return drained

    def _assert_active(self) -> None:
        if self._invalid:
            raise RuntimeError("ExtensionRunner has been invalidated")

    # -------------------------------------------------------------------------
    # Accessors
    # -------------------------------------------------------------------------

    @property
    def cwd(self) -> str:
        return self.services.cwd

    @property
    def session_manager(self) -> Any:
        return self.services.session_manager

    @property
    def model_registry(self) -> Any:
        return self.services.model_registry

    @property
    def settings_manager(self) -> Any:
        return self.services.settings_manager

    # ExtensionAPIContext 协议实现
    @property
    def event_bus(self) -> ExtensionEventBus:
        return self._event_bus

    @property
    def model(self) -> Optional[Any]:
        session = self._session
        return session.model if session is not None else None

    # -------------------------------------------------------------------------
    # Generic event dispatch
    # -------------------------------------------------------------------------

    async def emit(self, event: Any) -> Any:
        """
        通用事件分发。
        - 按加载顺序遍历扩展
        - 同一扩展的多个 handler 顺序执行
        - 返回最后一个非 None 结果
        - 遇到 ``cancel=True`` 的结果立即返回
        """
        self._assert_active()
        event_type = getattr(event, "type", None)
        if event_type is None:
            return None

        last_result: Any = None
        for extension in self.extensions:
            handlers = extension.handlers.get(event_type, [])
            for handler in handlers:
                try:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if result is not None:
                        last_result = result
                    if getattr(result, "cancel", False):
                        return result
                except Exception as exc:
                    await self._emit_error(extension, event_type, exc)
        return last_result

    async def _emit_error(
        self, extension: Extension, event_type: str, exc: Exception
    ) -> None:
        error_event = ExtensionErrorEvent(
            extension_path=extension.path,
            event=event_type,
            error=str(exc),
            stack=traceback.format_exc() if __debug__ else None,
        )
        for handler in self._error_handlers:
            try:
                handler(error_event)
            except Exception:
                pass

    def on_error(self, handler: Callable[[ExtensionErrorEvent], Any]) -> None:
        self._error_handlers.append(handler)

    def emit_error(self, error_event: ExtensionErrorEvent) -> None:
        """公共方法：广播扩展错误事件。"""
        for handler in self._error_handlers:
            try:
                handler(error_event)
            except Exception:
                pass

    def has_handlers(self, event_type: str) -> bool:
        """检查是否有扩展订阅了某类事件。"""
        for extension in self.extensions:
            handlers = extension.handlers.get(event_type)
            if handlers:
                return True
        return False

    # -------------------------------------------------------------------------
    # Special dispatchers with merge semantics
    # -------------------------------------------------------------------------

    async def emit_context(
        self, messages: List[Any], signal: Optional[Any] = None
    ) -> List[Any]:
        from nova_harness.core.types.events import ContextEvent, ContextEventResult

        event = ContextEvent(messages=list(messages), signal=signal)
        for extension in self.extensions:
            for handler in extension.handlers.get(CONTEXT, []):
                try:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if isinstance(result, ContextEventResult) and result.messages:
                        event.messages = result.messages
                except Exception as exc:
                    await self._emit_error(extension, CONTEXT, exc)
        return event.messages

    async def emit_before_agent_start(
        self,
        prompt: str,
        images: List[Any],
        system_prompt: Optional[str],
        system_prompt_options: Dict[str, Any],
    ):
        from nova_harness.core.types.events import (
            BeforeAgentStartEvent,
            BeforeAgentStartEventResult,
        )

        event = BeforeAgentStartEvent(
            prompt=prompt,
            images=list(images),
            system_prompt=system_prompt,
            system_prompt_options=system_prompt_options,
        )
        extra_messages: List[Any] = []
        current_system_prompt = system_prompt
        for extension in self.extensions:
            for handler in extension.handlers.get(BEFORE_AGENT_START, []):
                try:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if isinstance(result, BeforeAgentStartEventResult):
                        if result.system_prompt is not None:
                            current_system_prompt = result.system_prompt
                        if result.message is not None:
                            extra_messages.append(result.message)
                except Exception as exc:
                    await self._emit_error(extension, BEFORE_AGENT_START, exc)
        return current_system_prompt, extra_messages

    async def emit_tool_call(self, event: Any) -> Any:
        from nova_harness.core.types.events import ToolCallEventResult

        for extension in self.extensions:
            for handler in extension.handlers.get(TOOL_CALL, []):
                try:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if isinstance(result, ToolCallEventResult) and result.block:
                        return result
                except Exception as exc:
                    await self._emit_error(extension, TOOL_CALL, exc)
        return ToolCallEventResult(block=False)

    async def emit_tool_result(self, event: Any) -> Any:
        from nova_harness.core.types.events import ToolResultEventResult

        current_content = list(event.content)
        current_details = event.details
        current_is_error = event.is_error
        changed = False
        for extension in self.extensions:
            for handler in extension.handlers.get(TOOL_RESULT, []):
                try:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if isinstance(result, ToolResultEventResult):
                        if result.content is not None:
                            current_content = result.content
                            changed = True
                        if result.details is not None:
                            current_details = result.details
                            changed = True
                        if result.is_error is not None:
                            current_is_error = result.is_error
                            changed = True
                except Exception as exc:
                    await self._emit_error(extension, TOOL_RESULT, exc)
        if not changed:
            return None
        return ToolResultEventResult(
            content=current_content,
            details=current_details,
            is_error=current_is_error,
        )

    async def emit_message_end(self, message: Any) -> Any:
        from nova_harness.core.types.events import MessageEndEventResult

        current_message = message
        for extension in self.extensions:
            for handler in extension.handlers.get(MESSAGE_END, []):
                try:
                    result = handler(current_message)
                    if inspect.isawaitable(result):
                        result = await result
                    if isinstance(result, MessageEndEventResult) and result.message:
                        # 必须保持相同 role
                        if getattr(result.message, "role", None) == getattr(
                            current_message, "role", None
                        ):
                            current_message = result.message
                        else:
                            await self._emit_error(
                                extension,
                                MESSAGE_END,
                                ValueError(
                                    "message_end replacement must keep the same role"
                                ),
                            )
                except Exception as exc:
                    await self._emit_error(extension, MESSAGE_END, exc)
        return current_message

    async def emit_input(self, event: Any) -> Any:
        from nova_harness.core.types.events import InputEventResult

        text = event.text
        images = list(event.images)
        action = "continue"
        for extension in self.extensions:
            for handler in extension.handlers.get(INPUT, []):
                try:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if isinstance(result, InputEventResult):
                        if result.action == "handled":
                            return result
                        if result.action == "transform":
                            action = "transform"
                            if result.text is not None:
                                text = result.text
                            if result.images is not None:
                                images = result.images
                except Exception as exc:
                    await self._emit_error(extension, INPUT, exc)
        return InputEventResult(action=action, text=text, images=images)

    async def emit_user_bash(self, event: Any) -> Any:
        for extension in self.extensions:
            for handler in extension.handlers.get(USER_BASH, []):
                try:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if result is not None:
                        return result
                except Exception as exc:
                    await self._emit_error(extension, USER_BASH, exc)
        return None

    async def emit_resources_discover(
        self, cwd: str, reason: str
    ) -> ResourcesDiscoverEventResult:
        from nova_harness.core.types.events import (
            ResourcesDiscoverEvent,
            ResourcesDiscoverEventResult,
        )

        event = ResourcesDiscoverEvent(cwd=cwd, reason=reason)  # type: ignore[arg-type]
        merged = ResourcesDiscoverEventResult()
        for extension in self.extensions:
            for handler in extension.handlers.get(RESOURCES_DISCOVER, []):
                try:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if isinstance(result, ResourcesDiscoverEventResult):
                        merged.skill_paths.extend(result.skill_paths)
                        merged.prompt_paths.extend(result.prompt_paths)
                        merged.theme_paths.extend(result.theme_paths)
                except Exception as exc:
                    await self._emit_error(extension, RESOURCES_DISCOVER, exc)
        return merged

    async def emit_prepare_next_turn(self, event: Any) -> Optional[AgentLoopTurnUpdate]:
        from nova_harness.core.types.events import PrepareNextTurnEventResult

        current_context = getattr(event, "context", None)
        current_model = None
        current_thinking_level = None
        changed = False
        for extension in self.extensions:
            for handler in extension.handlers.get(PREPARE_NEXT_TURN, []):
                try:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if isinstance(result, PrepareNextTurnEventResult):
                        if result.context is not None:
                            current_context = result.context
                            changed = True
                        if result.model is not None:
                            current_model = result.model
                            changed = True
                        if result.thinking_level is not None:
                            current_thinking_level = result.thinking_level
                            changed = True
                except Exception as exc:
                    await self._emit_error(extension, PREPARE_NEXT_TURN, exc)
        if not changed:
            return None
        return AgentLoopTurnUpdate(
            context=current_context,
            model=current_model,
            thinking_level=current_thinking_level,
        )

    async def emit_should_stop_after_turn(self, event: Any) -> bool:
        for extension in self.extensions:
            for handler in extension.handlers.get(SHOULD_STOP_AFTER_TURN, []):
                try:
                    result = handler(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if getattr(result, "stop", False):
                        return True
                except Exception as exc:
                    await self._emit_error(extension, SHOULD_STOP_AFTER_TURN, exc)
        return False

    # -------------------------------------------------------------------------
    # Tool wrapping
    # -------------------------------------------------------------------------

    def get_extension_tools(self) -> List[AgentTool]:
        """把扩展注册的工具统一包装成 AgentTool。"""
        tools: List[AgentTool] = []
        for extension in self.extensions:
            for definition in extension.tools:
                adapted = self._adapt_extension_definition(definition)
                tools.append(DynamicTool(adapted))
        return tools

    def _adapt_extension_definition(
        self, definition: ExtensionToolDefinition
    ) -> ToolDefinition:
        """把扩展工具的执行签名统一为 (tool_call_id, params, signal, on_update)。"""
        original_execute = definition.execute

        def execute(
            tool_call_id: str,
            params: Dict[str, Any],
            signal: Optional[Any] = None,
            on_update: Optional[Any] = None,
        ) -> Any:
            ctx = self.create_context(signal)
            return original_execute(ctx, tool_call_id, params, signal)

        return definition.model_copy(update={"execute": execute})

    def get_commands(self) -> List[ExtensionCommand]:
        commands: List[ExtensionCommand] = []
        for extension in self.extensions:
            commands.extend(extension.commands)
        return commands

    def _resolve_registered_commands(self) -> List[ExtensionCommand]:
        """
        解析命令并处理同名冲突。

        当多个扩展注册同名命令时，为后续命令追加 ``:N`` 后缀生成 invocation name，
        与 TS 的 ``resolveRegisteredCommands`` 行为对齐。
        """
        commands: List[ExtensionCommand] = []
        counts: Dict[str, int] = {}
        for extension in self.extensions:
            for command in extension.commands:
                commands.append(command)
                counts[command.name] = counts.get(command.name, 0) + 1

        seen: Dict[str, int] = {}
        taken: set = set()
        resolved: List[ExtensionCommand] = []
        for command in commands:
            count = counts.get(command.name, 0)
            occurrence = seen.get(command.name, 0) + 1
            seen[command.name] = occurrence

            invocation = command.name if count <= 1 else f"{command.name}:{occurrence}"
            if invocation in taken:
                suffix = occurrence
                while True:
                    suffix += 1
                    candidate = f"{command.name}:{suffix}"
                    if candidate not in taken:
                        invocation = candidate
                        break
            taken.add(invocation)
            resolved.append(
                ExtensionCommand(
                    name=invocation,
                    description=command.description,
                    handler=command.handler,
                )
            )
        return resolved

    def get_registered_commands(self) -> List[ExtensionCommand]:
        """返回已解析冲突的扩展命令列表。"""
        return self._resolve_registered_commands()

    def get_command(self, name: str) -> Optional[ExtensionCommand]:
        """按名称获取命令（支持带 ``:N`` 后缀的 invocation name）。"""
        for command in self._resolve_registered_commands():
            if command.name == name:
                return command
        return None

    def get_tool_definition(self, tool_name: str) -> Optional[ToolDefinition]:
        """按名称获取扩展工具定义。"""
        for extension in self.extensions:
            for definition in extension.tools:
                if definition.name == tool_name:
                    return definition
        return None

    def get_flags(self) -> Dict[str, ExtensionFlag]:
        """获取所有扩展注册的 flag（按名称去重）。"""
        flags: Dict[str, ExtensionFlag] = {}
        for extension in self.extensions:
            for flag in extension.flags:
                if flag.name not in flags:
                    flags[flag.name] = flag
        return flags

    def get_flag_values(self) -> Dict[str, Any]:
        """获取 flag 当前值。"""
        return dict(self._flag_values)

    def set_flag_value(self, name: str, value: Any) -> None:
        """设置 flag 当前值。"""
        self._flag_values[name] = value

    def get_flag_value(self, name: str) -> Any:
        """获取 flag 当前值。"""
        return self._flag_values.get(name)

    def get_message_renderer(
        self, custom_type: str
    ) -> Optional[ExtensionMessageRenderer]:
        """按 custom_type 获取扩展消息渲染器。"""
        for extension in self.extensions:
            for renderer in extension.message_renderers:
                if renderer.custom_type == custom_type:
                    return renderer
        return None

    def get_shortcuts(
        self, resolved_keybindings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, ExtensionShortcut]:
        """
        获取扩展快捷键，并可选地与内置 keybindings 做冲突检测。

        当前仅做扩展之间的同名冲突检测；若提供 ``resolved_keybindings``，
        保留与内置快捷键冲突的诊断信息。
        """
        from nova_harness.core.types.diagnostics import ResourceDiagnostic

        diagnostics: List[ResourceDiagnostic] = []
        shortcuts: Dict[str, ExtensionShortcut] = {}
        for extension in self.extensions:
            for shortcut in extension.shortcuts:
                key = shortcut.key
                normalized = key.lower()
                if normalized in shortcuts:
                    diagnostics.append(
                        ResourceDiagnostic(
                            category="warning",
                            message=(
                                f"Extension shortcut '{key}' conflict: "
                                f"{shortcuts[normalized]} vs {shortcut.extension_path}"
                            ),
                            path=shortcut.extension_path,
                        )
                    )
                shortcuts[normalized] = shortcut

        if diagnostics:
            self._diagnostics.extend(diagnostics)
        return shortcuts

    # -------------------------------------------------------------------------
    # Context factories
    # -------------------------------------------------------------------------

    def create_context(self, signal: Optional[Any] = None) -> ExtensionContext:
        return ExtensionContext(runner=self, _signal=signal)

    def create_command_context(
        self, signal: Optional[Any] = None
    ) -> ExtensionCommandContext:
        return ExtensionCommandContext(runner=self, _signal=signal)

    # -------------------------------------------------------------------------
    # Actions delegated to AgentSession / Runtime
    # -------------------------------------------------------------------------

    def _require_session(self) -> Any:
        if self._session is None:
            raise RuntimeError("Extension action called before session bound")
        return self._session

    def _require_runtime(self) -> Any:
        if self._runtime is None:
            raise RuntimeError("Extension command action called before runtime bound")
        return self._runtime

    async def send_message(self, text: str, options: Optional[Any] = None) -> None:
        session = self._require_session()
        return await session.prompt(text, options)

    async def send_user_message(
        self, content: Any, options: Optional[Any] = None
    ) -> None:
        session = self._require_session()
        return await session.send_user_message(content, options)

    def append_entry(self, entry_type: str, data: Optional[Any] = None) -> str:
        return self.services.session_manager.append_custom_entry(entry_type, data)

    def set_session_name(self, name: str) -> None:
        session = self._require_session()
        session.set_session_name(name)

    def get_session_name(self) -> Optional[str]:
        return self.services.session_manager.get_session_name()

    def set_label(self, entry_id: str, label: Optional[str]) -> None:
        self.services.session_manager.append_label_change(entry_id, label)

    def get_active_tools(self) -> list:
        session = self._require_session()
        return session.get_active_tool_names()

    def get_all_tools(self) -> list:
        session = self._require_session()
        return session.get_all_tools()

    def set_active_tools(self, tool_names: list) -> None:
        session = self._require_session()
        session.set_active_tools_by_name(tool_names)

    def refresh_tools(self) -> None:
        """刷新当前 session 的工具注册表（重新加载扩展工具）。"""
        session = self._require_session()
        session.refresh_tools()

    async def set_model(self, model: Any) -> None:
        session = self._require_session()
        return await session.set_model(model)

    def get_thinking_level(self) -> Any:
        session = self._require_session()
        return session.thinking_level

    async def set_thinking_level(self, level: Any) -> None:
        session = self._require_session()
        return await session.set_thinking_level(level)

    async def compact(self, custom_instructions: Optional[str] = None) -> Any:
        session = self._require_session()
        return await session.compact(custom_instructions)

    def get_system_prompt(self) -> str:
        session = self._require_session()
        return getattr(session, "_base_system_prompt", "") or ""

    def get_system_prompt_options(self) -> Dict[str, Any]:
        """获取构建系统提示词时的选项（当前返回 cwd）。"""
        return {"cwd": self.cwd}

    def is_idle(self) -> bool:
        session = self._require_session()
        return not session.is_streaming

    def is_project_trusted(self) -> bool:
        """当前项目是否受信任（当前未实现信任模型，默认返回 True）。"""
        return True

    def has_pending_messages(self) -> bool:
        session = self._require_session()
        return bool(
            getattr(session, "_steering_messages", [])
            or getattr(session, "_follow_up_messages", [])
        )

    def get_context_usage(self) -> Optional[Any]:
        session = self._require_session()
        return session.get_context_usage()

    # Runtime-bound actions

    async def new_session(self, options: Optional[Any] = None) -> Any:
        runtime = self._require_runtime()
        return await runtime.new_session(options)

    async def fork(self, entry_id: Optional[str] = None) -> Any:
        runtime = self._require_runtime()
        return await runtime.fork(entry_id)

    async def navigate_tree(self, target_id: str, options: Optional[Any] = None) -> Any:
        session = self._require_session()
        return await session.navigate_tree(target_id, options)

    async def switch_session(self, path: str) -> Any:
        runtime = self._require_runtime()
        return await runtime.switch_session(path)

    async def reload(self) -> Any:
        runtime = self._require_runtime()
        return await runtime.reload()

    async def create_subagent_session(
        self, name: str, options: Optional[Any] = None
    ) -> Any:
        """根据已安装的 agent 名称创建子 agent 会话。"""
        # 延迟导入避免循环依赖
        from nova_harness.core.sdk import (
            CreateAgentSessionOptions,
            create_agent_session,
        )

        subagent_cwd = (
            options.cwd if options and getattr(options, "cwd", None) else self.cwd
        )
        subagent_model = (
            options.model if options and getattr(options, "model", None) else None
        )
        subagent_thinking = (
            options.thinking_level
            if options and getattr(options, "thinking_level", None)
            else None
        )

        opts = CreateAgentSessionOptions(
            cwd=subagent_cwd,
            agent_dir=self.services.agent_dir,
            auth_storage=self.services.auth_storage,
            model_registry=self.services.model_registry,
            settings_manager=self.services.settings_manager,
            resource_loader=self.services.resource_loader,
            system_prompt_manager=self.services.system_prompt_manager,
            model=subagent_model,
            thinking_level=subagent_thinking,
            agent_name=name,
        )
        return await create_agent_session(opts)

    async def wait_for_idle(self) -> None:
        session = self._require_session()
        agent = getattr(session, "agent", None)
        if agent is not None and hasattr(agent, "wait_for_idle"):
            await agent.wait_for_idle()


# Keep constant imports local to avoid circular issues at module load
from nova_harness.core.types.events import (  # noqa: E402
    BEFORE_AGENT_START,
    CONTEXT,
    INPUT,
    MESSAGE_END,
    PREPARE_NEXT_TURN,
    RESOURCES_DISCOVER,
    SHOULD_STOP_AFTER_TURN,
    TOOL_CALL,
    TOOL_RESULT,
    USER_BASH,
)
