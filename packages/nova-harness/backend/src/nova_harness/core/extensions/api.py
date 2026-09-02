"""扩展 API 实现（装载期注册面）。

``NovaExtensionAPI`` 是扩展工厂在**装载时**收到的唯一对象，职责收敛为一类：
声明式注册——订阅事件、注册命令/快捷键/flag/spawn hook/provider。

运行期动作（send_message / exec / set_active_tools / …）与环境感知
（ui / has_ui / session_manager / …）**不在本对象上**，统一经事件 handler
的 ``ctx``（``ExtensionContext``）触达——注册与动作分生命周期切分：
``nova.*`` 只在工厂里写（装载期声明），``ctx.*`` 只在 handler 里写（运行期）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from nova_harness.core.extensions.event_bus import ExtensionEventBus
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionAPI,
    ExtensionFlag,
    ExtensionRuntime,
    ExtensionShortcut,
    RegisteredCommand,
)


def _noop_handler(*args: Any, **kwargs: Any) -> Any:
    """默认空 handler。"""
    return None


class NovaExtensionAPI:
    """扩展工厂接收的具体 API（装载期注册面）。"""

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
        """注册一个快捷键（handler 在前端经 ``invokeShortcut`` 回调时执行）。"""
        self._assert_active()
        opts = options or {}
        shortcut_obj = ExtensionShortcut(
            shortcut=shortcut,
            description=opts.get("description"),
            extension_path=self.extension.path,
            handler=opts.get("handler") or _noop_handler,
        )
        self.extension.shortcuts[shortcut_obj.shortcut] = shortcut_obj

    def registerFlag(self, name: str, options: Optional[Dict[str, Any]] = None) -> None:
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

    def registerSpawnHook(self, hook: Callable[..., Any]) -> None:
        """注册一个子进程 spawn hook，可修改 command/cwd/env。"""
        self._assert_active()
        self.runtime.spawn_hooks.append(hook)

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
    # Provider 注册
    # -------------------------------------------------------------------------

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
    register_spawn_hook = registerSpawnHook


def create_extension_api(
    extension: Extension,
    runtime: ExtensionRuntime,
    cwd: str = "",
    event_bus: Optional[ExtensionEventBus] = None,
) -> NovaExtensionAPI:
    """创建 ``NovaExtensionAPI`` 实例的工厂函数。"""
    return NovaExtensionAPI(extension, runtime, cwd, event_bus)
