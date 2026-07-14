"""扩展 API 协议与工厂类型。"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol

from nova_harness.core.types.extensions.extension import MessageRenderer


class ExtensionAPI(Protocol):
    """扩展工厂接收的 API 协议。"""

    # -------------------------------------------------------------------------
    # 事件订阅
    # -------------------------------------------------------------------------

    def on(
        self, event_type: str, handler: Callable[..., Any]
    ) -> Callable[[], None]: ...

    def on_input(self, handler: Callable[..., Any]) -> Callable[[], None]: ...

    # -------------------------------------------------------------------------
    # 注册方法
    # -------------------------------------------------------------------------

    def registerCommand(
        self, name: str, options: Optional[Dict[str, Any]] = None
    ) -> None: ...

    def registerShortcut(
        self, shortcut: str, options: Optional[Dict[str, Any]] = None
    ) -> None: ...

    def registerFlag(
        self, name: str, options: Optional[Dict[str, Any]] = None
    ) -> None: ...

    def registerMessageRenderer(
        self, custom_type: str, renderer: MessageRenderer
    ) -> None: ...

    # -------------------------------------------------------------------------
    # Flag 读取
    # -------------------------------------------------------------------------

    def getFlag(self, name: str) -> Any: ...

    # -------------------------------------------------------------------------
    # Action 方法（委托给 ExtensionRuntime）
    # -------------------------------------------------------------------------

    def sendMessage(self, message: Any, options: Any = None) -> Any: ...

    def sendUserMessage(self, content: Any, options: Any = None) -> Any: ...

    def appendEntry(self, custom_type: str, data: Any = None) -> Any: ...

    def setSessionName(self, name: str) -> Any: ...

    def getSessionName(self) -> Any: ...

    def setLabel(self, entry_id: str, label: Any = None) -> Any: ...

    def exec(self, command: str, args: Any, options: Any = None) -> Any: ...

    def getActiveTools(self) -> Any: ...

    def getAllTools(self) -> Any: ...

    def setActiveTools(self, tool_names: Any) -> Any: ...

    def getCommands(self) -> Any: ...

    def setModel(self, model: Any) -> Any: ...

    def getThinkingLevel(self) -> Any: ...

    def setThinkingLevel(self, level: Any) -> Any: ...

    # -------------------------------------------------------------------------
    # Provider 注册
    # -------------------------------------------------------------------------

    def registerProvider(self, name: str, config: Any) -> None: ...

    def unregisterProvider(self, name: str) -> None: ...

    # -------------------------------------------------------------------------
    # 扩展间事件总线
    # -------------------------------------------------------------------------

    @property
    def events(self) -> Any: ...


ExtensionFactory = Callable[["ExtensionAPI"], Any]


__all__ = ["ExtensionAPI", "ExtensionFactory"]
