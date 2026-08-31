"""扩展 API 协议与工厂类型。

``ExtensionAPI`` 是扩展工厂在**装载时**收到的对象：只做声明式注册
（事件订阅、命令/快捷键/flag/spawn hook/provider）。运行期动作与环境
感知统一走事件 handler 的 ``ctx``（``ExtensionContext``）——
``nova.*`` 装载期声明，``ctx.*`` 运行期使用，两个生命周期不混。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol


class ExtensionAPI(Protocol):
    """扩展工厂接收的 API 协议（装载期注册面）。"""

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

    # -------------------------------------------------------------------------
    # Flag 读取
    # -------------------------------------------------------------------------

    def getFlag(self, name: str) -> Any: ...

    # -------------------------------------------------------------------------
    # Provider 注册
    # -------------------------------------------------------------------------

    def registerProvider(self, name: str, config: Any) -> None: ...

    def unregisterProvider(self, name: str) -> None: ...

    # -------------------------------------------------------------------------
    # 消息类型注册
    # -------------------------------------------------------------------------

    def register_message_types(self, types: List[Any]) -> None:
        """注册包级消息类型（会话 JSONL 回载按 ``role`` 复原）。

        与 on/registerCommand 同族的装载期声明式注册；机制（role 键/幂等/
        碰撞警告/包缺席降级 Opaque）见 ``harness/session/message_types``。
        """

    # -------------------------------------------------------------------------
    # 扩展间事件总线
    # -------------------------------------------------------------------------

    @property
    def events(self) -> Any: ...


ExtensionFactory = Callable[["ExtensionAPI"], Any]


__all__ = ["ExtensionAPI", "ExtensionFactory"]
