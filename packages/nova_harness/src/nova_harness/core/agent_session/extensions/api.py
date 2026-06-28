"""
Nova extension API implementation (`nova`).

每个扩展工厂接收一个 `NovaExtensionAPI` 实例（扩展代码里通常命名为 `nova`），
通过它注册事件处理器、工具、命令等。注册内容写入对应的 `Extension` 对象；
需要会话/运行时能力时委托给 `ExtensionRunner`。

本模块位于 ``resources/extensions``，使资源加载层在加载扩展时不必向上依赖
``core/extensions``。
"""

from __future__ import annotations

from typing import Any, Optional

from nova_harness.core.types.events import ExtensionEventHandler
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionAPIContext,
    ExtensionCommand,
    ExtensionFlag,
    ExtensionMessageRenderer,
    ExtensionProviderRegistration,
    ExtensionShortcut,
    ExtensionToolDefinition,
)


class NovaExtensionAPI:
    """扩展实际拿到的 `nova` 对象。"""

    def __init__(self, extension: Extension, context: ExtensionAPIContext) -> None:
        self._extension = extension
        self._context = context

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def on(self, event: str, handler: ExtensionEventHandler) -> None:
        """订阅扩展事件。"""
        self._extension.handlers.setdefault(event, []).append(handler)

    def register_tool(self, tool: ExtensionToolDefinition) -> None:
        """注册一个扩展工具。"""
        self._extension.tools.append(tool)

    def register_command(self, command: ExtensionCommand) -> None:
        """注册一个命令。"""
        command.extension_path = self._extension.path
        self._extension.commands.append(command)

    def register_shortcut(self, shortcut: ExtensionShortcut) -> None:
        """注册一个快捷键。"""
        shortcut.extension_path = self._extension.path
        self._extension.shortcuts.append(shortcut)

    def register_flag(self, flag: ExtensionFlag) -> None:
        """注册一个 flag。"""
        flag.extension_path = self._extension.path
        self._extension.flags.append(flag)

    def register_message_renderer(self, renderer: ExtensionMessageRenderer) -> None:
        """注册自定义消息渲染器。"""
        renderer.extension_path = self._extension.path
        self._extension.message_renderers.append(renderer)

    def register_provider(self, name: str, config: Any = None) -> None:
        """注册一个模型 provider（委托给 ModelRegistry）。"""
        self._extension.providers.append(
            ExtensionProviderRegistration(name=name, config=config)
        )
        # 立即应用到 ModelRegistry（如果上下文已能访问注册表）
        model_registry = self._context.model_registry
        if model_registry is not None:
            try:
                model_registry.register_provider(name, config)
            except Exception as exc:
                self._context.add_diagnostic(
                    "error",
                    f'Extension "{self._extension.path}" provider '
                    f'"{name}" registration failed: {exc}',
                )

    def unregister_provider(self, name: str) -> None:
        """注销一个模型 provider。"""
        self._extension.providers = [
            p for p in self._extension.providers if p.name != name
        ]
        model_registry = self._context.model_registry
        if model_registry is not None:
            try:
                model_registry.unregister_provider(name)
            except Exception as exc:
                self._context.add_diagnostic(
                    "error",
                    f'Extension "{self._extension.path}" provider '
                    f'"{name}" unregistration failed: {exc}',
                )

    def get_flag(self, name: str) -> Any:
        """读取已注册 flag 的默认值。"""
        for flag in self._extension.flags:
            if flag.name == name:
                return flag.default
        return None

    def get_flag_value(self, name: str) -> Any:
        """读取 flag 当前运行时值（未设置则回退到默认值）。"""
        value = self._context.get_flag_value(name)
        if value is not None:
            return value
        return self.get_flag(name)

    def set_flag_value(self, name: str, value: Any) -> None:
        """设置 flag 当前运行时值。"""
        self._context.set_flag_value(name, value)

    # -------------------------------------------------------------------------
    # Actions (delegate to runner/session)
    # -------------------------------------------------------------------------

    async def send_message(self, text: str, options: Optional[Any] = None) -> None:
        """发送一条用户消息并触发 agent 回复。"""
        return await self._context.send_message(text, options)

    async def send_user_message(
        self, content: Any, options: Optional[Any] = None
    ) -> None:
        """发送一条用户消息。"""
        return await self._context.send_user_message(content, options)

    def append_entry(self, entry_type: str, data: Optional[Any] = None) -> str:
        """向会话追加一个自定义 entry。"""
        return self._context.append_entry(entry_type, data)

    def set_session_name(self, name: str) -> None:
        self._context.set_session_name(name)

    def get_session_name(self) -> Optional[str]:
        return self._context.get_session_name()

    def set_label(self, entry_id: str, label: Optional[str]) -> None:
        self._context.set_label(entry_id, label)

    def get_active_tools(self) -> list:
        return self._context.get_active_tools()

    def get_all_tools(self) -> list:
        return self._context.get_all_tools()

    def set_active_tools(self, tool_names: list) -> None:
        self._context.set_active_tools(tool_names)

    def refresh_tools(self) -> None:
        """重新刷新当前工具注册表。"""
        self._context.refresh_tools()

    def get_commands(self) -> list:
        return self._context.get_commands()

    async def set_model(self, model: Any) -> None:
        await self._context.set_model(model)

    def get_thinking_level(self) -> Any:
        return self._context.get_thinking_level()

    async def set_thinking_level(self, level: Any) -> None:
        await self._context.set_thinking_level(level)

    async def compact(self, custom_instructions: Optional[str] = None) -> Any:
        return await self._context.compact(custom_instructions)

    def get_system_prompt(self) -> str:
        return self._context.get_system_prompt()

    @property
    def events(self) -> Any:
        """扩展间事件总线。"""
        return self._context.event_bus

    async def create_subagent_session(
        self, name: str, options: Optional[Any] = None
    ) -> Any:
        """根据已安装的 agent 名称创建一个子 agent 会话。"""
        return await self._context.create_subagent_session(name, options)


__all__ = ["NovaExtensionAPI"]
