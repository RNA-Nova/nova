"""扩展 API 实现。

把扩展工厂的注册写入 ``Extension`` 对象，action 调用委托给共享
``ExtensionRuntime``。

Python 侧兼容 TS 的 fire-and-forget 语义：所有委托给 async action 的调用
会被自动 schedule 到当前事件循环，扩展代码无论是否 ``await`` 都能触发实际
行为。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Dict, Optional

from nova_harness.core.extensions.event_bus import ExtensionEventBus
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionAPI,
    ExtensionFlag,
    ExtensionRuntime,
    ExtensionShortcut,
    MessageRenderer,
    RegisteredCommand,
)


def _noop_handler(*args: Any, **kwargs: Any) -> Any:
    """默认空 handler。"""
    return None


class NovaExtensionAPI:
    """扩展工厂接收的具体 API（Python 版）。"""

    def __init__(
        self,
        extension: Extension,
        runtime: ExtensionRuntime,
        cwd: str = "",
        event_bus: Optional[ExtensionEventBus] = None,
    ) -> None:
        self.extension = extension
        self.runtime = runtime
        self.cwd = cwd
        self.events = event_bus or ExtensionEventBus()

    def _assert_active(self) -> None:
        self.runtime.assert_active()

    @staticmethod
    def _schedule(result: Any) -> Any:
        """把 async action 的返回值 schedule 到当前事件循环。

        如果返回的是 coroutine/awaitable，就包装成 Task 并返回，使扩展代码
        不写 ``await`` 也能触发实际行为（对齐 TS 的 Promise 语义）。
        不在事件循环中时原样返回 awaitable，方便测试里直接 await。
        """
        if not inspect.isawaitable(result):
            return result
        try:
            return asyncio.get_running_loop().create_task(result)
        except RuntimeError:
            return result

    # -------------------------------------------------------------------------
    # 事件订阅
    # -------------------------------------------------------------------------

    def on(self, event_type: str, handler: Callable[..., Any]) -> Callable[[], None]:
        """订阅扩展事件。支持直接传事件名字符串。"""
        self._assert_active()
        handlers = self.extension.handlers.setdefault(event_type, [])
        handlers.append(handler)

        def remove() -> None:
            try:
                handlers.remove(handler)
            except ValueError:
                pass

        return remove

    def on_input(self, handler: Callable[..., Any]) -> Callable[[], None]:
        """订阅 input 事件。"""
        return self.on("input", handler)

    # -------------------------------------------------------------------------
    # 注册方法（与 TS 对齐：name + options）
    # -------------------------------------------------------------------------

    def registerCommand(
        self, name: str, options: Optional[Dict[str, Any]] = None
    ) -> None:
        """注册一个 slash 命令。"""
        self._assert_active()
        opts = options or {}
        handler = opts.get("handler")
        if handler is None or not callable(handler):
            raise ValueError(
                f"Extension '{self.extension.path}' registerCommand('{name}') "
                "requires a callable 'handler'"
            )
        command = RegisteredCommand(
            name=name,
            description=opts.get("description"),
            source_info=opts.get("source_info") or self.extension.source_info,
            handler=handler,
            get_argument_completions=opts.get("get_argument_completions"),
        )
        self.extension.commands[command.name] = command

    def registerShortcut(
        self, shortcut: str, options: Optional[Dict[str, Any]] = None
    ) -> None:
        """注册一个快捷键。"""
        self._assert_active()
        opts = options or {}
        shortcut_obj = ExtensionShortcut(
            shortcut=shortcut,
            description=opts.get("description"),
            extension_path=self.extension.path,
            handler=opts.get("handler") or _noop_handler,
        )
        self.extension.shortcuts[shortcut_obj.shortcut] = shortcut_obj

    def registerFlag(
        self, name: str, options: Optional[Dict[str, Any]] = None
    ) -> None:
        """注册一个 flag。"""
        self._assert_active()
        opts = options or {}
        flag = ExtensionFlag(
            name=name,
            type=opts.get("type", "boolean"),
            description=opts.get("description"),
            default=opts.get("default"),
            extension_path=self.extension.path,
        )
        self.extension.flags[flag.name] = flag
        if flag.default is not None and flag.name not in self.runtime.flag_values:
            self.runtime.flag_values[flag.name] = flag.default

    def registerMessageRenderer(
        self, custom_type: str, renderer: MessageRenderer
    ) -> None:
        """注册消息渲染器。"""
        self._assert_active()
        self.extension.message_renderers[custom_type] = renderer

    def registerBashSpawnHook(self, hook: Callable[..., Any]) -> None:
        """注册一个 bash spawn hook，可修改 command/cwd/env。"""
        self._assert_active()
        self.runtime.bash_spawn_hooks.append(hook)


    # -------------------------------------------------------------------------
    # Flag 读取
    # -------------------------------------------------------------------------

    def getFlag(self, name: str) -> Any:
        """读取当前扩展注册的 flag 值。"""
        self._assert_active()
        if name not in self.extension.flags:
            return None
        return self.runtime.flag_values.get(name)

    # -------------------------------------------------------------------------
    # Action 委托
    # -------------------------------------------------------------------------

    def sendMessage(self, message: Any, options: Optional[Any] = None) -> Any:
        self._assert_active()
        return self._schedule(self.runtime.actions.send_message(message, options))

    def sendUserMessage(self, content: Any, options: Optional[Any] = None) -> Any:
        self._assert_active()
        return self._schedule(
            self.runtime.actions.send_user_message(content, options)
        )

    def appendEntry(self, custom_type: str, data: Optional[Any] = None) -> Any:
        self._assert_active()
        return self._schedule(self.runtime.actions.append_entry(custom_type, data))

    def setSessionName(self, name: str) -> Any:
        self._assert_active()
        return self._schedule(self.runtime.actions.set_session_name(name))

    def getSessionName(self) -> Any:
        self._assert_active()
        return self.runtime.actions.get_session_name()

    def setLabel(self, entry_id: str, label: Optional[str] = None) -> Any:
        self._assert_active()
        return self._schedule(self.runtime.actions.set_label(entry_id, label))

    def exec(self, command: str, args: Any, options: Optional[Any] = None) -> Any:
        self._assert_active()
        return self._schedule(self.runtime.actions.exec(command, args, options))

    def getActiveTools(self) -> Any:
        self._assert_active()
        return self.runtime.actions.get_active_tools()

    def getAllTools(self) -> Any:
        self._assert_active()
        return self.runtime.actions.get_all_tools()

    def setActiveTools(self, tool_names: Any) -> Any:
        self._assert_active()
        return self._schedule(self.runtime.actions.set_active_tools(tool_names))

    def getCommands(self) -> Any:
        self._assert_active()
        return self.runtime.actions.get_commands()

    def setModel(self, model: Any) -> Any:
        self._assert_active()
        return self._schedule(self.runtime.actions.set_model(model))

    def getThinkingLevel(self) -> Any:
        self._assert_active()
        return self.runtime.actions.get_thinking_level()

    def setThinkingLevel(self, level: Any) -> Any:
        self._assert_active()
        return self._schedule(self.runtime.actions.set_thinking_level(level))

    def registerProvider(self, name: str, config: Any) -> None:
        self._assert_active()
        self.runtime.register_provider(name, config, self.extension.path)

    def unregisterProvider(self, name: str) -> None:
        self._assert_active()
        self.runtime.unregister_provider(name, self.extension.path)

    # -------------------------------------------------------------------------
    # Python 风格别名
    # -------------------------------------------------------------------------

    register_command = registerCommand
    register_shortcut = registerShortcut
    register_flag = registerFlag
    register_message_renderer = registerMessageRenderer
    register_bash_spawn_hook = registerBashSpawnHook


def create_extension_api(
    extension: Extension,
    runtime: ExtensionRuntime,
    cwd: str = "",
    event_bus: Optional[ExtensionEventBus] = None,
) -> NovaExtensionAPI:
    """创建 ``NovaExtensionAPI`` 实例的工厂函数。"""
    return NovaExtensionAPI(extension, runtime, cwd, event_bus)
