"""
扩展协议类型。

对应原 `nova_harness.extensions.types` 中的扩展注册与 API 协议类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol

from nova_harness.core.types.events import ExtensionEventHandler
from nova_harness.core.types.tools import ToolDefinition


@dataclass
class LoadedExtensionsResult:
    """扩展加载结果（由 ResourceLoader 持有，供 AgentSession 构建 ExtensionRunner）。"""

    extensions: List["Extension"] = field(default_factory=list)
    diagnostics: List[Any] = field(default_factory=list)


class ExtensionToolDefinition(ToolDefinition):
    """扩展注册的工具定义。

    与 ``ToolDefinition`` 完全等价，保留独立名称以便扩展 API 自描述。
    """


@dataclass
class ExtensionCommand:
    name: str
    description: Optional[str] = None
    handler: Callable[..., Any] = field(default=lambda *_, **__: None)
    extension_path: Optional[str] = None


@dataclass
class ExtensionShortcut:
    key: str
    description: Optional[str] = None
    handler: Callable[..., Any] = field(default=lambda *_, **__: None)
    extension_path: Optional[str] = None


@dataclass
class ExtensionFlag:
    name: str
    description: Optional[str] = None
    type: Literal["boolean", "string", "number"] = "boolean"
    default: Any = None
    extension_path: Optional[str] = None


@dataclass
class ExtensionMessageRenderer:
    custom_type: str
    renderer: Callable[[Any], Optional[str]] = field(default=lambda _: None)
    extension_path: Optional[str] = None


@dataclass
class ExtensionProviderRegistration:
    name: str
    config: Any = None


@dataclass
class Extension:
    """一个已加载的扩展实例。"""

    path: str
    name: str
    module: Any = None
    factory: Optional[Callable[..., Any]] = None
    handlers: Dict[str, List[ExtensionEventHandler]] = field(default_factory=dict)
    tools: List[ExtensionToolDefinition] = field(default_factory=list)
    commands: List[ExtensionCommand] = field(default_factory=list)
    shortcuts: List[ExtensionShortcut] = field(default_factory=list)
    flags: List[ExtensionFlag] = field(default_factory=list)
    message_renderers: List[ExtensionMessageRenderer] = field(default_factory=list)
    providers: List[ExtensionProviderRegistration] = field(default_factory=list)


class ExtensionAPI(Protocol):
    """扩展工厂接收的 `nova` 对象协议。"""

    def on(self, event: str, handler: ExtensionEventHandler) -> None: ...

    def register_tool(self, tool: ExtensionToolDefinition) -> None: ...

    def register_command(self, command: ExtensionCommand) -> None: ...

    def register_shortcut(self, shortcut: ExtensionShortcut) -> None: ...

    def register_flag(self, flag: ExtensionFlag) -> None: ...

    def register_message_renderer(self, renderer: ExtensionMessageRenderer) -> None: ...

    def register_provider(self, name: str, config: Any = None) -> None: ...

    def get_flag(self, name: str) -> Any: ...

    async def create_subagent_session(
        self, name: str, options: Optional[Any] = None
    ) -> Any: ...


class ExtensionAPIContext(Protocol):
    """
    扩展加载/注册阶段所需的上下文。

    ExtensionRunner 与 ResourceLoader 的加载上下文都实现此协议，
    使 NovaExtensionAPI 在加载时不必直接依赖 ExtensionRunner。
    """

    @property
    def event_bus(self) -> "ExtensionEventBus": ...

    @property
    def model_registry(self) -> Any: ...

    def add_diagnostic(
        self, type: Literal["info", "warning", "error"], message: str
    ) -> None: ...

    def get_flag_value(self, name: str) -> Any: ...

    def set_flag_value(self, name: str, value: Any) -> None: ...

    async def create_subagent_session(
        self, name: str, options: Optional[Any] = None
    ) -> Any: ...


class ExtensionEventBus:
    """扩展间通信用的简单事件总线。"""

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[..., Any]]] = {}

    def on(self, event: str, handler: Callable[..., Any]) -> Callable[[], None]:
        self._handlers.setdefault(event, []).append(handler)

        def remove() -> None:
            if handler in self._handlers.get(event, []):
                self._handlers[event].remove(handler)

        return remove

    def emit(self, event: str, *args: Any, **kwargs: Any) -> List[Any]:
        results: List[Any] = []
        for handler in self._handlers.get(event, []):
            try:
                results.append(handler(*args, **kwargs))
            except Exception as exc:
                # Event bus errors are best-effort
                results.append(exc)
        return results

    def clear(self) -> None:
        self._handlers.clear()


__all__ = [
    "ExtensionToolDefinition",
    "ExtensionCommand",
    "ExtensionShortcut",
    "ExtensionFlag",
    "ExtensionMessageRenderer",
    "ExtensionProviderRegistration",
    "Extension",
    "ExtensionAPI",
    "ExtensionAPIContext",
    "ExtensionEventBus",
    "LoadedExtensionsResult",
]
