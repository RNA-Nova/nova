"""扩展 Actions 类型。

这些类型用于把 AgentSession 能力注入 ``ExtensionRuntime``。
字段默认值为抛出 stub，实际实现由 ``ExtensionRunner.bind_core`` 替换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


def _not_initialized(name: str) -> Callable[..., Any]:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"Extension action '{name}' is not initialized")

    return _raise


def _noop(*args: Any, **kwargs: Any) -> Any:
    return None


def _false(*args: Any, **kwargs: Any) -> bool:
    return False


@dataclass
class ExtensionActions:
    """核心 actions（由 AgentSession 注入）。"""

    send_message: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("send_message")
    )
    send_user_message: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("send_user_message")
    )
    exec: Callable[..., Any] = field(default_factory=lambda: _not_initialized("exec"))
    append_entry: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("append_entry")
    )
    set_session_name: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("set_session_name")
    )
    get_session_name: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_session_name")
    )
    set_label: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("set_label")
    )
    get_active_tools: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_active_tools")
    )
    get_all_tools: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_all_tools")
    )
    set_active_tools: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("set_active_tools")
    )
    refresh_tools: Callable[..., Any] = field(default_factory=_noop)
    get_commands: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_commands")
    )
    set_model: Callable[..., Any] = field(default_factory=_false)
    get_thinking_level: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_thinking_level")
    )
    set_thinking_level: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("set_thinking_level")
    )


@dataclass
class ExtensionContextActions:
    """扩展上下文 actions（由 AgentSession 注入）。"""

    get_model: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_model")
    )
    is_idle: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("is_idle")
    )
    is_project_trusted: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("is_project_trusted")
    )
    get_signal: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_signal")
    )
    abort: Callable[..., Any] = field(default_factory=lambda: _not_initialized("abort"))
    has_pending_messages: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("has_pending_messages")
    )
    shutdown: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("shutdown")
    )
    get_context_usage: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_context_usage")
    )
    compact: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("compact")
    )
    get_system_prompt: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_system_prompt")
    )
    get_system_prompt_options: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_system_prompt_options")
    )


@dataclass
class ExtensionCommandContextActions:
    """扩展命令上下文 actions（由 AgentSession 或外部 runtime 注入）。"""

    wait_for_idle: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("wait_for_idle")
    )
    new_session: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("new_session")
    )
    fork: Callable[..., Any] = field(default_factory=lambda: _not_initialized("fork"))
    navigate_tree: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("navigate_tree")
    )
    switch_session: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("switch_session")
    )
    reload: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("reload")
    )
    get_session_info: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("get_session_info")
    )
    trust_project: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("trust_project")
    )
    untrust_project: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("untrust_project")
    )
    clone: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("clone")
    )
    export: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("export")
    )
    import_session: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("import_session")
    )


@dataclass
class ExtensionProviderActions:
    """Provider 注册 actions（由 AgentSession 注入）。"""

    register_provider: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("register_provider")
    )
    unregister_provider: Callable[..., Any] = field(
        default_factory=lambda: _not_initialized("unregister_provider")
    )


__all__ = [
    "_not_initialized",
    "_noop",
    "_false",
    "ExtensionActions",
    "ExtensionContextActions",
    "ExtensionCommandContextActions",
    "ExtensionProviderActions",
]
