"""
ExtensionContext / ExtensionCommandContext.

扩展在事件处理器中拿到的 `ctx` / `cmdCtx`。
它们本身不直接持有 AgentSession，而是通过 ExtensionRunner 委托调用，
从而保持扩展系统对 AgentSession 的从属关系。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from nova_harness.core.agent_session.extensions.runner import ExtensionRunner


@dataclass
class ExtensionContext:
    """扩展运行时上下文。"""

    runner: ExtensionRunner
    _signal: Any = None

    @property
    def cwd(self) -> str:
        return self.runner.cwd

    @property
    def session_manager(self) -> Any:
        return self.runner.session_manager

    @property
    def model_registry(self) -> Any:
        return self.runner.model_registry

    @property
    def settings_manager(self) -> Any:
        return self.runner.settings_manager

    @property
    def model(self) -> Optional[Any]:
        return self.runner.model

    def is_idle(self) -> bool:
        return self.runner.is_idle()

    def is_project_trusted(self) -> bool:
        return self.runner.is_project_trusted()

    def has_pending_messages(self) -> bool:
        return self.runner.has_pending_messages()

    def get_context_usage(self) -> Optional[Any]:
        return self.runner.get_context_usage()

    async def compact(self, custom_instructions: Optional[str] = None) -> Any:
        return await self.runner.compact(custom_instructions)

    def get_system_prompt(self) -> str:
        return self.runner.get_system_prompt()

    def get_system_prompt_options(self) -> Dict[str, Any]:
        return self.runner.get_system_prompt_options()

    @property
    def signal(self) -> Optional[Any]:
        return self._signal

    @property
    def ui(self) -> Any:
        """UI 上下文（当前未接入 TUI，返回 None）。"""
        return None

    @property
    def mode(self) -> str:
        """当前运行模式（当前固定返回 ``print``）。"""
        return "print"

    @property
    def has_ui(self) -> bool:
        """是否有可用 UI（当前固定返回 False）。"""
        return False

    def abort(self) -> None:
        if self._signal is not None and hasattr(self._signal, "set"):
            self._signal.set()

    def shutdown(self) -> None:
        self.runner.invalidate()

    def get_flag_value(self, name: str) -> Any:
        return self.runner.get_flag_value(name)

    def set_flag_value(self, name: str, value: Any) -> None:
        self.runner.set_flag_value(name, value)


@dataclass
class ExtensionCommandContext(ExtensionContext):
    """命令上下文，在 ExtensionContext 基础上增加会话控制方法。"""

    async def new_session(self, options: Optional[Any] = None) -> Any:
        return await self.runner.new_session(options)

    async def fork(self, entry_id: Optional[str] = None) -> Any:
        return await self.runner.fork(entry_id)

    async def navigate_tree(self, target_id: str, options: Optional[Any] = None) -> Any:
        return await self.runner.navigate_tree(target_id, options)

    async def switch_session(self, path: str) -> Any:
        return await self.runner.switch_session(path)

    async def reload(self) -> Any:
        return await self.runner.reload()

    async def wait_for_idle(self) -> None:
        await self.runner.wait_for_idle()
