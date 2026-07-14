"""扩展运行时类型。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from nova_harness.core.types.extensions.actions import (
    ExtensionActions,
    ExtensionContextActions,
    ExtensionProviderActions,
)
from nova_harness.core.types.extensions.commands import _ProviderRegistration
from nova_harness.core.types.runtime.bash import BashSpawnHook

if TYPE_CHECKING:
    from nova_harness.core.extensions.event_bus import ExtensionEventBus


class ExtensionRuntime:
    """扩展共享运行时。

    初始状态下所有 action 均为抛出 stub；``ExtensionRunner.bind_core`` 之后
    才由 AgentSession 替换为真实实现。
    """

    def __init__(
        self,
        cwd: str = "",
        event_bus: Optional["ExtensionEventBus"] = None,
        model_registry: Any = None,
        session_manager: Any = None,
        **kwargs: Any,
    ) -> None:
        self.cwd = cwd
        self.event_bus = event_bus
        self.model_registry = model_registry
        self.session_manager = session_manager

        self.flag_values: Dict[str, Any] = {}
        self.pending_provider_registrations: List[_ProviderRegistration] = []
        self.bash_spawn_hooks: List[BashSpawnHook] = []

        # 核心 actions（初始为抛出 stub，bind_core 后替换）
        self.actions = ExtensionActions()

        # 上下文 actions（存储为对象便于 create_context 一次性暴露）
        self.context_actions = ExtensionContextActions()

        # provider actions（bind_core 后替换）
        self.provider_actions = ExtensionProviderActions()

        self._active = True
        self._invalidate_message: Optional[str] = None

    # --- provider 注册 API（扩展调用）---

    def register_provider(
        self, name: str, config: Any, extension_path: Optional[str] = None
    ) -> None:
        """排队 provider 注册（bind_core 之后由 runner 直连 model_registry）。"""
        self.pending_provider_registrations.append(
            _ProviderRegistration(
                name=name, config=config, extension_path=extension_path
            )
        )

    def unregister_provider(
        self, name: str, extension_path: Optional[str] = None
    ) -> None:
        """排队 provider 注销；bind_core 后 runner 会直连 model_registry。"""
        # 当前实现仅记录，后续可扩展为立即生效/排队处理
        self.pending_provider_registrations.append(
            _ProviderRegistration(name=name, config=None, extension_path=extension_path)
        )

    def assert_active(self) -> None:
        """防 stale 使用。"""
        if not self._active:
            message = (
                self._invalidate_message or "Extension runtime is no longer active"
            )
            raise RuntimeError(message)

    def invalidate(self, message: Optional[str] = None) -> None:
        """使当前 runtime 失效。"""
        self._active = False
        self._invalidate_message = message


__all__ = ["ExtensionRuntime"]
