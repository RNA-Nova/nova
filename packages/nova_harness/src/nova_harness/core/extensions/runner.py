"""ExtensionRunner — 扩展生命周期、事件分发与上下文管理。

核心能力：
- bind_core / bind_command_context / set_ui_context
- 事件 emit 与专用 emitXxx 方法
- 工具/命令/flag/快捷键发现
- create_context / create_command_context
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

from nova_agent import AgentMessage

from nova_harness.core.types.events import (
    AfterProviderResponseEvent,
    BeforeAgentStartEvent,
    BeforeProviderHeadersEvent,
    BeforeProviderRequestEvent,
    ContextEvent,
    ExtensionErrorEvent,
    InputEvent,
    MessageEndEvent,
    ModelSelectEvent,
    PrepareNextTurnEvent,
    ResourcesDiscoverEvent,
    SessionShutdownEvent,
    ShouldStopAfterTurnEvent,
    ThinkingLevelSelectEvent,
    ToolCallEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolResultEvent,
    TurnEndEvent,
    UserBashEvent,
)
from nova_harness.core.types.events.constants import (
    AFTER_PROVIDER_RESPONSE,
    BEFORE_PROVIDER_HEADERS,
    BEFORE_PROVIDER_REQUEST,
    CONTEXT,
    INPUT,
    MESSAGE_END,
    MODEL_SELECT,
    SESSION_SHUTDOWN,
    THINKING_LEVEL_SELECT,
    TOOL_CALL,
    TOOL_EXECUTION_END,
    TOOL_EXECUTION_START,
    TOOL_EXECUTION_UPDATE,
    TOOL_RESULT,
    USER_BASH,
)
from nova_harness.core.types.events.results import (
    AfterProviderResponseEventResult,
    BeforeAgentStartEventResult,
    BeforeProviderRequestEventResult,
    ContextEventResult,
    InputEventResult,
    MessageEndEventResult,
    ModelSelectEventResult,
    PrepareNextTurnEventResult,
    ResourcesDiscoverEventResult,
    ShouldStopAfterTurnEventResult,
    ThinkingLevelSelectEventResult,
    ToolCallEventResult,
    ToolResultEventResult,
    UserBashEventResult,
)
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionActions,
    ExtensionCommandContext,
    ExtensionCommandContextActions,
    ExtensionContext,
    ExtensionContextActions,
    ExtensionFlag,
    ExtensionProviderActions,
    ExtensionRuntime,
    ExtensionShortcut,
    LoadedExtensionsResult,
    RegisteredCommand,
)
from nova_harness.core.types.project_trust import (
    ProjectTrustContext,
    ProjectTrustEvent,
    ProjectTrustEventResult,
)
from nova_harness.core.types.protocols import ModelRuntimeProtocol
from nova_harness.core.types.ui import NoOpUIContext, ScopedUIContext, UIContext


@dataclass(frozen=True)
class BeforeAgentStartCombinedResult:
    """before_agent_start 事件合并结果。"""

    messages: List[Any] = field(default_factory=list)
    system_prompt: Optional[str] = None


async def emit_session_shutdown_event(
    runner: ExtensionRunner,
    event: SessionShutdownEvent,
) -> bool:
    """触发 session_shutdown 事件。返回是否有 handler 处理。"""
    if not runner.has_handlers("session_shutdown"):
        return False
    await runner.emit(event)
    return True


async def emit_project_trust_event(
    extensions_result: Any,
    event: ProjectTrustEvent,
    context: ProjectTrustContext,
    on_error: Optional[Callable[[str], None]] = None,
) -> Tuple[Optional[ProjectTrustEventResult], List[str]]:
    """触发扩展的 project_trust 事件并收集结果与错误。

    独立函数形态（对齐 TS emitProjectTrustEvent）：只消费扩展加载结果，
    不依赖 ExtensionRunner 实例——trust 裁决发生在 runner 装配之前。
    第一个返回 ``yes``/``no`` 的 handler 获胜；返回 ``undecided`` 的 handler 会
    被跳过，继续询问后续扩展。
    """
    errors: List[str] = []

    def _collect_error(msg: str) -> None:
        errors.append(msg)
        if on_error is not None:
            try:
                on_error(msg)
            except Exception:
                pass

    extensions = getattr(extensions_result, "extensions", []) or []
    for extension in extensions:
        handlers = extension.handlers.get("project_trust", [])
        for handler in handlers:
            try:
                raw = handler(event, context)
                if inspect.isawaitable(raw):
                    raw = await raw
                if raw is None:
                    # 对齐 TS：返回 None（通常是忘了 return）视为 handler 错误
                    # 收集后继续询问后续扩展，不静默弃权
                    _collect_error(
                        f'Extension "{extension.path}" project_trust error: '
                        'handler returned None (expect "yes"/"no"/"undecided")'
                    )
                    continue

                if isinstance(raw, dict):
                    trusted_value = raw.get("trusted", False)
                    remember_value = bool(raw.get("remember", False))
                else:
                    trusted_value = getattr(raw, "trusted", False)
                    remember_value = bool(getattr(raw, "remember", False))

                if trusted_value == "undecided":
                    continue

                # 只认字符串 "yes"（对齐 TS 与 Literal 类型契约）；
                # True、垃圾值等一律归 no
                trusted = "yes" if trusted_value == "yes" else "no"
                return (
                    ProjectTrustEventResult(trusted=trusted, remember=remember_value),
                    errors,
                )
            except Exception as exc:
                _collect_error(
                    f'Extension "{extension.path}" project_trust error: {exc}'
                )

    return None, errors


class ExtensionRunner:
    """管理一组扩展的事件分发与运行时上下文。"""

    def __init__(
        self,
        extensions: List[Extension],
        runtime: ExtensionRuntime,
        cwd: str,
        session_manager: Any,
        model_runtime: Optional[ModelRuntimeProtocol] = None,
    ) -> None:
        self.extensions = list(extensions)
        self.runtime = runtime
        self.cwd = cwd
        self.session_manager = session_manager
        self.model_runtime = model_runtime

        self.ui_context: Optional[UIContext] = None
        self.project_trusted: Optional[bool] = None

        self._command_context_actions = ExtensionCommandContextActions()
        self._error_listeners: List[Callable[[Any], None]] = []
        self._shortcut_diagnostics: List[Any] = []
        self._command_diagnostics: List[Any] = []

    # -------------------------------------------------------------------------
    # 绑定
    # -------------------------------------------------------------------------

    def bind_core(
        self,
        actions: ExtensionActions,
        context_actions: ExtensionContextActions,
        provider_actions: Optional[ExtensionProviderActions] = None,
    ) -> None:
        """注入核心 action 与上下文 action，并 flush provider 注册队列。"""
        runtime = self.runtime

        # 核心 actions
        runtime.actions = actions

        # 上下文 actions
        runtime.context_actions = context_actions

        # provider actions
        runtime.provider_actions = provider_actions or ExtensionProviderActions()

        # flush 加载阶段排队的 provider 注册
        for reg in list(runtime.pending_provider_registrations):
            provider_action = (
                runtime.provider_actions.unregister_provider
                if reg.config is None
                else runtime.provider_actions.register_provider
            )
            # config 为 None 表示注销
            target = provider_action or (
                lambda name, config=None: (
                    self.model_runtime.unregister_provider(name)
                    if config is None and self.model_runtime is not None
                    else (
                        self.model_runtime.register_provider(name, config)
                        if self.model_runtime is not None
                        else None
                    )
                )
            )
            try:
                if reg.config is None:
                    target(reg.name)
                else:
                    target(reg.name, reg.config)
            except Exception as err:
                self.emit_error(
                    {
                        "extension_path": reg.extension_path or "<unknown>",
                        "event": "register_provider",
                        "error": str(err),
                    }
                )
        runtime.pending_provider_registrations.clear()

        # bind 之后直接生效：扩展调用 runtime.register_provider 时直连真实 registry
        def _register_provider(
            name: str, config: Any, extension_path: str = "<unknown>"
        ) -> None:
            if runtime.provider_actions.register_provider:
                runtime.provider_actions.register_provider(name, config)
            elif self.model_runtime is not None:
                self.model_runtime.register_provider(name, config)

        def _unregister_provider(name: str, extension_path: str = "<unknown>") -> None:
            if runtime.provider_actions.unregister_provider:
                runtime.provider_actions.unregister_provider(name)
            elif self.model_runtime is not None:
                self.model_runtime.unregister_provider(name)

        runtime.register_provider = _register_provider
        runtime.unregister_provider = _unregister_provider

    def bind_command_context(
        self, actions: Optional[ExtensionCommandContextActions] = None
    ) -> None:
        """注入命令上下文 action。"""
        if actions is not None:
            self._command_context_actions = actions
        else:

            async def _noop(*args: Any, **kwargs: Any) -> Any:
                if "cancelled" in kwargs:
                    return {"cancelled": False}
                return None

            self._command_context_actions = ExtensionCommandContextActions(
                wait_for_idle=_noop,
                new_session=lambda *a, **k: {"cancelled": False},
                fork=lambda *a, **k: {"cancelled": False},
                navigate_tree=lambda *a, **k: {"cancelled": False},
                switch_session=lambda *a, **k: {"cancelled": False},
                reload=_noop,
            )

    def set_ui_context(self, ui_context: Optional[UIContext] = None) -> None:
        self.ui_context = ui_context or NoOpUIContext()

    def has_ui(self) -> bool:
        return self.ui_context is not None and not isinstance(
            self.ui_context, NoOpUIContext
        )

    # -------------------------------------------------------------------------
    # 发现
    # -------------------------------------------------------------------------

    def get_extension_paths(self) -> List[str]:
        return [ext.path for ext in self.extensions]

    def get_flags(self) -> Dict[str, ExtensionFlag]:
        flags: Dict[str, ExtensionFlag] = {}
        for ext in self.extensions:
            for name, flag in ext.flags.items():
                if name not in flags:
                    flags[name] = flag
        return flags

    def set_flag_value(self, name: str, value: Any) -> None:
        self.runtime.flag_values[name] = value

    def get_flag_values(self) -> Dict[str, Any]:
        return dict(self.runtime.flag_values)

    def get_shortcuts(self) -> Dict[str, ExtensionShortcut]:
        """收集扩展快捷键（归一化键名 → shortcut），检测扩展间冲突。

        内置键位表与用户自定义归前端（架构 2.0：键位绑定是前端状态），
        运行时只裁决"扩展 vs 扩展"冲突——先注册者获胜，后者记诊断。
        """
        self._shortcut_diagnostics = []
        shortcuts: Dict[str, ExtensionShortcut] = {}

        for ext in self.extensions:
            for key, shortcut in ext.shortcuts.items():
                normalized = key.lower()
                if normalized in shortcuts:
                    self._shortcut_diagnostics.append(
                        {
                            "type": "warning",
                            "message": (
                                f"Extension shortcut conflict: '{key}' registered by both "
                                f"{shortcuts[normalized].extension_path} and {shortcut.extension_path}. "
                                f"Using {shortcuts[normalized].extension_path}."
                            ),
                            "path": shortcut.extension_path,
                        }
                    )
                    continue
                shortcuts[normalized] = shortcut

        return shortcuts

    def get_shortcut_diagnostics(self) -> List[Any]:
        return list(self._shortcut_diagnostics)

    async def invoke_shortcut(self, key: str) -> bool:
        """执行指定键名对应的扩展 shortcut handler（前端键位捕获后的回调）。

        返回是否找到并执行了 handler；handler 异常不抛出——经扩展错误
        事件透出（对齐命令 handler 的错误通道）。
        """
        normalized = key.lower()
        shortcuts = self.get_shortcuts()
        shortcut = shortcuts.get(normalized)
        if shortcut is None:
            return False

        extension = next(
            (ext for ext in self.extensions if ext.path == shortcut.extension_path),
            None,
        )
        ctx = self.create_context(extension)
        try:
            result = shortcut.handler(ctx)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            self.emit_error(
                ExtensionErrorEvent(
                    extension_path=shortcut.extension_path or "<unknown>",
                    event="shortcut",
                    error=str(exc),
                    stack=traceback.format_exc() if __debug__ else None,
                )
            )
        return True

    def get_registered_commands(self) -> List[RegisteredCommand]:
        """收集扩展命令并自动为同名命令生成 `:N` 调用名。

        当多个扩展注册同名命令时，按出现顺序依次生成
        ``name:1``、``name:2``、``name:3`` 等调用名；如果某个调用名已被占用
        （例如某个命令本身就叫做 ``x:2``），则继续递增直到找到可用名称。

        原始命令名保留在 ``name`` 字段，自动生成的调用名写入 ``invocation_name``。
        """
        self._command_diagnostics = []

        # 1. 按加载顺序收集所有命令
        all_commands: List[RegisteredCommand] = []
        name_counts: Dict[str, int] = {}
        for ext in self.extensions:
            for cmd in ext.commands.values():
                all_commands.append(cmd)
                name_counts[cmd.name] = name_counts.get(cmd.name, 0) + 1

        # 2. 生成 invocation_name
        seen_occurrences: Dict[str, int] = {}
        taken_invocation_names: set = set()
        resolved: List[RegisteredCommand] = []

        for cmd in all_commands:
            occurrence = seen_occurrences.get(cmd.name, 0) + 1
            seen_occurrences[cmd.name] = occurrence

            if name_counts.get(cmd.name, 0) > 1:
                invocation_name = f"{cmd.name}:{occurrence}"
            else:
                invocation_name = cmd.name

            # 处理极端情况：invocation_name 已被占用（例如某个命令本身就叫做 "x:2"）
            if invocation_name in taken_invocation_names:
                suffix = occurrence
                while True:
                    suffix += 1
                    candidate = f"{cmd.name}:{suffix}"
                    if candidate not in taken_invocation_names:
                        invocation_name = candidate
                        break

            taken_invocation_names.add(invocation_name)

            # 保留原始名，自动调用名写入 invocation_name
            if invocation_name != cmd.name:
                resolved.append(
                    RegisteredCommand(
                        name=cmd.name,
                        invocation_name=invocation_name,
                        description=cmd.description,
                        source_info=cmd.source_info,
                        handler=cmd.handler,
                        get_argument_completions=cmd.get_argument_completions,
                    )
                )
            else:
                resolved.append(cmd)

        return resolved

    def get_command(self, name: str) -> Optional[RegisteredCommand]:
        """按调用名（invocation_name）查找命令。"""
        for cmd in self.get_registered_commands():
            if (cmd.invocation_name or cmd.name) == name:
                return cmd
        return None

    def get_command_by_original_name(
        self, original_name: str
    ) -> Optional[RegisteredCommand]:
        """按扩展注册的原始命令名查找命令（忽略自动重命名）。"""
        for ext in self.extensions:
            cmd = ext.commands.get(original_name)
            if cmd is not None:
                return cmd
        return None

    def get_command_diagnostics(self) -> List[Any]:
        """返回命令冲突与诊断信息。"""
        # 确保 diagnostics 已计算
        self.get_registered_commands()
        return list(self._command_diagnostics)

    # 上下文创建
    # -------------------------------------------------------------------------

    def create_context(self, extension: Optional[Extension] = None) -> ExtensionContext:
        """创建供扩展 handler 使用的上下文对象。"""
        runtime = self.runtime
        context_actions = runtime.context_actions
        return ExtensionContext(
            send_message=runtime.actions.send_message,
            send_user_message=runtime.actions.send_user_message,
            exec=runtime.actions.exec,
            append_entry=runtime.actions.append_entry,
            set_session_name=runtime.actions.set_session_name,
            get_session_name=runtime.actions.get_session_name,
            set_label=runtime.actions.set_label,
            get_active_tools=runtime.actions.get_active_tools,
            get_all_tools=runtime.actions.get_all_tools,
            set_active_tools=runtime.actions.set_active_tools,
            refresh_tools=runtime.actions.refresh_tools,
            get_commands=runtime.actions.get_commands,
            set_model=runtime.actions.set_model,
            get_thinking_level=runtime.actions.get_thinking_level,
            set_thinking_level=runtime.actions.set_thinking_level,
            is_idle=context_actions.is_idle,
            is_project_trusted=context_actions.is_project_trusted,
            get_signal=context_actions.get_signal,
            abort=context_actions.abort,
            has_pending_messages=context_actions.has_pending_messages,
            shutdown=context_actions.shutdown,
            get_context_usage=context_actions.get_context_usage,
            compact=context_actions.compact,
            get_system_prompt=context_actions.get_system_prompt,
            get_system_prompt_options=context_actions.get_system_prompt_options,
            get_personas=context_actions.get_personas,
            get_persona_override=context_actions.get_persona_override,
            set_persona_override=context_actions.set_persona_override,
            clear_persona_override=context_actions.clear_persona_override,
            get_agents=context_actions.get_agents,
            change_agent=context_actions.change_agent,
            save_agent=context_actions.save_agent,
            refresh_system_prompt=context_actions.refresh_system_prompt,
            ui=ScopedUIContext(
                self.ui_context or NoOpUIContext(),
                # 注入点织入 abort 竞速：扩展的 ui 调用自动与当前 run 的
                # abort signal 竞速（Esc → ui/cancel 撤销 + cancelled），
                # handler 无需自己处理 signal
                lambda: context_actions.get_signal(),
            ),
            has_ui=self.has_ui(),
            cwd=self.cwd,
            extension_path=extension.path if extension else None,
            session_manager=self.session_manager,
            model_runtime=self.model_runtime,
            _get_model=context_actions.get_model,
            assert_active=runtime.assert_active,
        )

    def create_command_context(
        self, extension: Optional[Extension] = None
    ) -> ExtensionCommandContext:
        """创建供命令 handler 使用的上下文（包含 session 控制 action）。"""
        ctx = self.create_context(extension)
        actions = self._command_context_actions
        return ExtensionCommandContext(
            **ctx.__dict__,
            wait_for_idle=actions.wait_for_idle,
            new_session=actions.new_session,
            fork=actions.fork,
            navigate_tree=actions.navigate_tree,
            switch_session=actions.switch_session,
            reload=actions.reload,
            get_session_info=actions.get_session_info,
            get_scoped_models=actions.get_scoped_models,
            trust_project=actions.trust_project,
            untrust_project=actions.untrust_project,
            clone=actions.clone,
            export=actions.export,
            import_session=actions.import_session,
        )

    # -------------------------------------------------------------------------
    # 事件分发
    # -------------------------------------------------------------------------

    def has_handlers(self, event_type: str) -> bool:
        return any(
            event_type in ext.handlers and len(ext.handlers[event_type]) > 0
            for ext in self.extensions
        )

    _SESSION_BEFORE_TYPES = (
        "session_before_switch",
        "session_before_fork",
        "session_before_compact",
        "session_before_tree",
    )

    async def emit(self, event: Any) -> Any:
        """通用事件分发。

        ``session_before_*`` 事件返回最后一个**非 None** 的 handler 结果
        （对齐 TS： falsy 结果不覆盖已收集的结果），并支持 cancel 短路；
        其余事件类型只分发不返回。
        """
        event_type = getattr(event, "type", None)
        is_session_before = event_type in self._SESSION_BEFORE_TYPES
        result: Any = None

        for ext in self.extensions:
            handlers = ext.handlers.get(event_type, [])
            for handler in handlers:
                try:
                    ctx = self.create_context(ext)
                    raw = handler(event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if not is_session_before:
                        continue
                    if raw is None:
                        continue
                    result = raw
                    # cancel 短路：遇到显式 cancel=True 时立即停止
                    if isinstance(raw, dict) and raw.get("cancel"):
                        return raw
                    if hasattr(raw, "cancel") and getattr(raw, "cancel"):
                        return raw
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": event_type,
                            "error": str(exc),
                        }
                    )

        return result if is_session_before else None

    async def _emit_single_result(self, event: Any, default_factory: Any) -> Any:
        """触发事件并返回最后一个非 None 结果。"""
        event_type = getattr(event, "type", None)
        result: Any = None
        for ext in self.extensions:
            handlers = ext.handlers.get(event_type, [])
            for handler in handlers:
                try:
                    ctx = self.create_context(ext)
                    raw = handler(event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if raw is not None:
                        result = raw
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": event_type,
                            "error": str(exc),
                        }
                    )
        return result if result is not None else default_factory()

    async def emit_after_provider_response(
        self, event: AfterProviderResponseEvent
    ) -> AfterProviderResponseEventResult:
        """触发 after_provider_response 事件。"""
        return await self._emit_single_result(event, AfterProviderResponseEventResult)

    async def emit_tool_execution_start(self, event: ToolExecutionStartEvent) -> None:
        """触发 tool_execution_start 事件。"""
        await self.emit(event)

    async def emit_tool_execution_update(self, event: ToolExecutionUpdateEvent) -> None:
        """触发 tool_execution_update 事件。"""
        await self.emit(event)

    async def emit_tool_execution_end(self, event: ToolExecutionEndEvent) -> None:
        """触发 tool_execution_end 事件。"""
        await self.emit(event)

    async def emit_model_select(
        self, event: ModelSelectEvent
    ) -> ModelSelectEventResult:
        """触发 model_select 事件。"""
        return await self._emit_single_result(event, ModelSelectEventResult)

    async def emit_thinking_level_select(
        self, event: ThinkingLevelSelectEvent
    ) -> ThinkingLevelSelectEventResult:
        """触发 thinking_level_select 事件。"""
        return await self._emit_single_result(event, ThinkingLevelSelectEventResult)

    async def emit_message_end(self, event: MessageEndEvent) -> Optional[AgentMessage]:
        """触发 message_end 事件。多个 handler 链式修改 message，并校验 role 不变。"""
        ctx = self.create_context()
        current_message = event.message
        modified = False

        for ext in self.extensions:
            handlers = ext.handlers.get("message_end", [])
            for handler in handlers:
                try:
                    current_event = MessageEndEvent(message=current_message)
                    raw = handler(current_event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if (
                        isinstance(raw, MessageEndEventResult)
                        and raw.message is not None
                    ):
                        if raw.message.role != current_message.role:
                            self.emit_error(
                                {
                                    "extension_path": ext.path,
                                    "event": "message_end",
                                    "error": "message_end handlers must return a message with the same role",
                                }
                            )
                            continue
                        current_message = raw.message
                        modified = True
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": "message_end",
                            "error": str(exc),
                        }
                    )

        return current_message if modified else None

    async def emit_tool_call(
        self, event: ToolCallEvent
    ) -> Optional[ToolCallEventResult]:
        """触发 tool_call 事件。返回第一个 block 的结果，否则返回最后一个结果。

        fail-closed（对齐 TS 语义）：tool_call handler 抛异常**不放行**——
        拦截类扩展（permission gate 等）崩溃时若静默放行，危险操作会径直
        穿过门禁。异常转为 block 结果（reason 含原始错误），同时照常发
        extension_error 事件便于观测；其余事件类型保持 fail-open。
        """
        ctx = self.create_context()
        result: Optional[ToolCallEventResult] = None

        for ext in self.extensions:
            handlers = ext.handlers.get(TOOL_CALL, [])
            for handler in handlers:
                try:
                    raw = handler(event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if isinstance(raw, ToolCallEventResult):
                        result = raw
                        if raw.block:
                            return result
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": TOOL_CALL,
                            "error": str(exc),
                        }
                    )
                    return ToolCallEventResult(
                        block=True,
                        reason=f"Extension failed, blocking execution: {exc}",
                    )

        return result

    async def emit_tool_result(
        self, event: ToolResultEvent
    ) -> Optional[ToolResultEventResult]:
        """触发 tool_result 事件。多个 handler 链式修改 content/details/is_error。"""
        ctx = self.create_context()
        current_event = ToolResultEvent(
            tool_call_id=event.tool_call_id,
            tool_name=event.tool_name,
            args=event.args,
            content=list(event.content),
            details=event.details,
            is_error=event.is_error,
        )
        modified = False

        for ext in self.extensions:
            handlers = ext.handlers.get(TOOL_RESULT, [])
            for handler in handlers:
                try:
                    raw = handler(current_event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if isinstance(raw, ToolResultEventResult):
                        if raw.content is not None:
                            current_event.content = raw.content
                            modified = True
                        if raw.details is not None:
                            current_event.details = raw.details
                            modified = True
                        if raw.is_error is not None:
                            current_event.is_error = raw.is_error
                            modified = True
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": TOOL_RESULT,
                            "error": str(exc),
                        }
                    )

        if not modified:
            return None

        return ToolResultEventResult(
            content=current_event.content,
            details=current_event.details,
            is_error=current_event.is_error,
        )

    async def emit_user_bash(
        self, event: UserBashEvent
    ) -> Optional[UserBashEventResult]:
        """触发 user_bash 事件。返回第一个非 None 结果。"""
        ctx = self.create_context()

        for ext in self.extensions:
            handlers = ext.handlers.get(USER_BASH, [])
            for handler in handlers:
                try:
                    raw = handler(event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if raw is not None:
                        if isinstance(raw, UserBashEventResult):
                            return raw
                        if isinstance(raw, dict):
                            return UserBashEventResult(
                                operations=raw.get("operations"),
                                result=raw.get("result"),
                            )
                        return raw
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": USER_BASH,
                            "error": str(exc),
                        }
                    )

        return None

    async def emit_context(self, messages: List[Any]) -> List[Any]:
        """触发 context 事件。多个 handler 链式修改 messages。"""
        ctx = self.create_context()
        # 与 TS structuredClone 对齐，避免 handler 原地修改原始 message 对象。
        current_messages = copy.deepcopy(messages)

        for ext in self.extensions:
            handlers = ext.handlers.get(CONTEXT, [])
            for handler in handlers:
                try:
                    event = ContextEvent(messages=current_messages)
                    raw = handler(event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if isinstance(raw, ContextEventResult) and raw.messages is not None:
                        current_messages = raw.messages
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": CONTEXT,
                            "error": str(exc),
                        }
                    )

        return current_messages

    async def emit_before_agent_start(
        self, event: BeforeAgentStartEvent
    ) -> Optional[BeforeAgentStartCombinedResult]:
        """触发 before_agent_start 事件；返回合并后的 messages 与 system_prompt。

        与 TS 对齐：无修改时返回 None；ctx.get_system_prompt 返回链上最新值。
        """
        current_system_prompt = event.system_prompt
        messages: List[Any] = []
        system_prompt_modified = False
        event_type = getattr(event, "type", None)

        for ext in self.extensions:
            handlers = ext.handlers.get(event_type, [])
            for handler in handlers:
                try:
                    ctx = self.create_context(ext)
                    # 让扩展看到当前链上最新的 system prompt
                    ctx.get_system_prompt = (
                        lambda _current=current_system_prompt: _current
                    )
                    raw = handler(event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if isinstance(raw, BeforeAgentStartEventResult):
                        if raw.message is not None:
                            messages.append(raw.message)
                        if raw.system_prompt is not None:
                            current_system_prompt = raw.system_prompt
                            system_prompt_modified = True
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": event_type,
                            "error": str(exc),
                        }
                    )

        if not messages and not system_prompt_modified:
            return None

        return BeforeAgentStartCombinedResult(
            messages=messages,
            system_prompt=current_system_prompt if system_prompt_modified else None,
        )

    async def emit_before_provider_request(self, payload: Any) -> Any:
        """异步触发 before_provider_request；多个 handler 链式修改 payload。"""
        if not self.has_handlers(BEFORE_PROVIDER_REQUEST):
            return payload

        event = BeforeProviderRequestEvent(payload=payload)
        ctx = self.create_context()
        current_payload = payload

        for ext in self.extensions:
            handlers = ext.handlers.get(BEFORE_PROVIDER_REQUEST, [])
            for handler in handlers:
                try:
                    raw = handler(event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if raw is not None:
                        if isinstance(raw, BeforeProviderRequestEventResult):
                            current_payload = raw.payload
                        else:
                            current_payload = raw
                        event = BeforeProviderRequestEvent(payload=current_payload)
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": BEFORE_PROVIDER_REQUEST,
                            "error": str(exc),
                        }
                    )

        return current_payload

    async def emit_before_provider_headers(
        self, headers: Dict[str, str]
    ) -> Dict[str, str]:
        """触发 before_provider_headers：handler 原地修改 headers，返回值忽略。

        对齐 pi ``emitBeforeProviderHeaders``：串行 handler 共享同一 headers
        字典（后者见前者修改）；handler 异常 fail-open（转 error 事件，请求继续）。
        """
        if not self.has_handlers(BEFORE_PROVIDER_HEADERS):
            return headers

        # model_construct：不校验不拷贝——headers 保持原引用（pi 原地修改语义）
        event = BeforeProviderHeadersEvent.model_construct(
            type=BEFORE_PROVIDER_HEADERS, headers=headers
        )
        ctx = self.create_context()

        for ext in self.extensions:
            handlers = ext.handlers.get(BEFORE_PROVIDER_HEADERS, [])
            for handler in handlers:
                try:
                    raw = handler(event, ctx)
                    if inspect.isawaitable(raw):
                        await raw
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": BEFORE_PROVIDER_HEADERS,
                            "error": str(exc),
                        }
                    )

        return headers

    async def emit_prepare_next_turn(
        self, event: TurnEndEvent
    ) -> PrepareNextTurnEventResult:
        pnt_event = PrepareNextTurnEvent(
            message=event.message,
            tool_results=event.tool_results,
        )
        return await self._emit_single_result(pnt_event, PrepareNextTurnEventResult)

    async def emit_should_stop_after_turn(
        self, event: TurnEndEvent
    ) -> ShouldStopAfterTurnEventResult:
        ssr_event = ShouldStopAfterTurnEvent(
            message=event.message,
            tool_results=event.tool_results,
        )
        result = await self._emit_single_result(
            ssr_event, ShouldStopAfterTurnEventResult
        )
        return (
            result
            if isinstance(result, ShouldStopAfterTurnEventResult)
            else ShouldStopAfterTurnEventResult()
        )

    async def emit_input(self, event: InputEvent) -> InputEventResult:
        """触发 input 事件。transform 结果链式传递，handled 直接短路返回。"""
        current_text = event.text
        current_images = list(event.images) if event.images else []
        transformed = False

        for ext in self.extensions:
            handlers = ext.handlers.get(INPUT, [])
            for handler in handlers:
                try:
                    ctx = self.create_context(ext)
                    current_event = InputEvent(
                        type=INPUT,
                        text=current_text,
                        images=current_images,
                        source=event.source,
                        streaming_behavior=event.streaming_behavior,
                    )
                    raw = handler(current_event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if isinstance(raw, InputEventResult):
                        if raw.action == "handled":
                            return raw
                        if raw.action == "transform":
                            if raw.text is not None:
                                current_text = raw.text
                            if raw.images is not None:
                                current_images = list(raw.images)
                            transformed = True
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": INPUT,
                            "error": str(exc),
                        }
                    )

        if transformed:
            return InputEventResult(
                action="transform", text=current_text, images=current_images
            )
        return InputEventResult(action="continue")

    async def emit_resources_discover(
        self, cwd: str, reason: Literal["startup", "reload"] = "startup"
    ) -> Dict[str, List[Dict[str, str]]]:
        """触发 resources_discover 事件。返回带 extensionPath 的资源路径列表。"""
        event = ResourcesDiscoverEvent(cwd=cwd, reason=reason)
        skill_paths: List[Dict[str, str]] = []
        prompt_paths: List[Dict[str, str]] = []
        persona_paths: List[Dict[str, str]] = []

        for ext in self.extensions:
            handlers = ext.handlers.get("resources_discover", [])
            for handler in handlers:
                try:
                    ctx = self.create_context(ext)
                    raw = handler(event, ctx)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if isinstance(raw, ResourcesDiscoverEventResult):
                        skill_paths.extend(
                            [
                                {"path": p, "extensionPath": ext.path}
                                for p in raw.skill_paths
                            ]
                        )
                        prompt_paths.extend(
                            [
                                {"path": p, "extensionPath": ext.path}
                                for p in raw.prompt_paths
                            ]
                        )
                        persona_paths.extend(
                            [
                                {"path": p, "extensionPath": ext.path}
                                for p in raw.persona_paths
                            ]
                        )
                    elif isinstance(raw, dict):
                        skill_paths.extend(
                            [
                                {"path": p, "extensionPath": ext.path}
                                for p in raw.get("skill_paths", [])
                            ]
                        )
                        prompt_paths.extend(
                            [
                                {"path": p, "extensionPath": ext.path}
                                for p in raw.get("prompt_paths", [])
                            ]
                        )
                        persona_paths.extend(
                            [
                                {"path": p, "extensionPath": ext.path}
                                for p in raw.get("persona_paths", [])
                            ]
                        )
                except Exception as exc:
                    self.emit_error(
                        {
                            "extension_path": ext.path,
                            "event": "resources_discover",
                            "error": str(exc),
                        }
                    )

        return {
            "skill_paths": skill_paths,
            "prompt_paths": prompt_paths,
            "persona_paths": persona_paths,
        }

    # -------------------------------------------------------------------------
    # 错误处理
    # -------------------------------------------------------------------------

    def on_error(self, listener: Callable[[Any], None]) -> Callable[[], None]:
        self._error_listeners.append(listener)

        def remove() -> None:
            try:
                self._error_listeners.remove(listener)
            except ValueError:
                pass

        return remove

    def emit_error(self, error: Any) -> None:
        for listener in list(self._error_listeners):
            try:
                listener(error)
            except Exception:
                pass

    def invalidate(self, message: Optional[str] = None) -> None:
        self.runtime.invalidate(message)
