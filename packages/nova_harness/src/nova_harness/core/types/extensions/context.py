"""扩展 handler 上下文类型。

用于替代 ``SimpleNamespace``，提供更强的类型提示与运行时安全性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from nova_harness.core.types.protocols import (
    ModelRegistryProtocol,
    SessionManagerProtocol,
)
from nova_harness.core.types.ui import UIContext


def _noop(*args: Any, **kwargs: Any) -> Any:
    """默认空实现。"""
    return None


def _noop_ui_context() -> UIContext:
    """延迟导入，避免循环依赖。"""
    from nova_harness.core.ui.noop import NoOpUIContext

    return NoOpUIContext()


@dataclass
class ExtensionContext:
    """扩展事件 handler 使用的上下文对象。"""

    # 核心 action（由 ExtensionRuntime.actions 代理）
    send_message: Callable[..., Any] = field(default_factory=lambda: _noop)
    send_user_message: Callable[..., Any] = field(default_factory=lambda: _noop)
    exec: Callable[..., Any] = field(default_factory=lambda: _noop)
    append_entry: Callable[..., Any] = field(default_factory=lambda: _noop)
    set_session_name: Callable[..., Any] = field(default_factory=lambda: _noop)
    get_session_name: Callable[..., Any] = field(default_factory=lambda: _noop)
    set_label: Callable[..., Any] = field(default_factory=lambda: _noop)
    get_active_tools: Callable[..., Any] = field(default_factory=lambda: _noop)
    get_all_tools: Callable[..., Any] = field(default_factory=lambda: _noop)
    set_active_tools: Callable[..., Any] = field(default_factory=lambda: _noop)
    refresh_tools: Callable[..., Any] = field(default_factory=lambda: _noop)
    get_commands: Callable[..., Any] = field(default_factory=lambda: _noop)
    set_model: Callable[..., Any] = field(default_factory=lambda: _noop)
    get_thinking_level: Callable[..., Any] = field(default_factory=lambda: _noop)
    set_thinking_level: Callable[..., Any] = field(default_factory=lambda: _noop)

    # 上下文 action（由 ExtensionRuntime.context_actions 代理）
    get_model: Callable[..., Any] = field(default_factory=lambda: _noop)
    is_idle: Callable[..., Any] = field(default_factory=lambda: _noop)
    is_project_trusted: Callable[..., Any] = field(default_factory=lambda: _noop)
    get_signal: Callable[..., Any] = field(default_factory=lambda: _noop)
    abort: Callable[..., Any] = field(default_factory=lambda: _noop)
    has_pending_messages: Callable[..., Any] = field(default_factory=lambda: _noop)
    shutdown: Callable[..., Any] = field(default_factory=lambda: _noop)
    get_context_usage: Callable[..., Any] = field(default_factory=lambda: _noop)
    compact: Callable[..., Any] = field(default_factory=lambda: _noop)
    get_system_prompt: Callable[..., Any] = field(default_factory=lambda: _noop)
    get_system_prompt_options: Callable[..., Any] = field(default_factory=lambda: _noop)

    # 环境信息
    ui: UIContext = field(default_factory=_noop_ui_context)
    has_ui: bool = False
    mode: str = "print"
    cwd: str = ""
    extension_path: Optional[str] = None

    # Session / model 访问（与 TS 对齐）
    session_manager: Optional[SessionManagerProtocol] = None
    model_registry: Optional[ModelRegistryProtocol] = None
    _get_model: Callable[..., Any] = field(default_factory=lambda: _noop)

    @property
    def model(self) -> Any:
        """每次访问都返回当前模型，避免创建 context 时快照导致过期。"""
        return self._get_model()

    assert_active: Callable[[], None] = field(default_factory=lambda: _noop)


@dataclass
class ExtensionCommandContext(ExtensionContext):
    """扩展命令 handler 使用的上下文对象。

    在 ``ExtensionContext`` 基础上增加会话控制 action。
    """

    wait_for_idle: Callable[..., Any] = field(default_factory=lambda: _noop)
    new_session: Callable[..., Any] = field(default_factory=lambda: _noop)
    fork: Callable[..., Any] = field(default_factory=lambda: _noop)
    navigate_tree: Callable[..., Any] = field(default_factory=lambda: _noop)
    switch_session: Callable[..., Any] = field(default_factory=lambda: _noop)
    reload: Callable[..., Any] = field(default_factory=lambda: _noop)
    get_session_info: Callable[..., Any] = field(default_factory=lambda: _noop)
    trust_project: Callable[..., Any] = field(default_factory=lambda: _noop)
    untrust_project: Callable[..., Any] = field(default_factory=lambda: _noop)
    clone: Callable[..., Any] = field(default_factory=lambda: _noop)
    export: Callable[..., Any] = field(default_factory=lambda: _noop)
    import_session: Callable[..., Any] = field(default_factory=lambda: _noop)


__all__ = ["ExtensionContext", "ExtensionCommandContext"]
