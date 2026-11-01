"""扩展间事件总线实现。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from nova_async import TaskTracker


class ExtensionEventBus:
    """扩展间事件总线。

    - ``on`` 返回取消订阅函数；
    - ``emit`` 不收集返回值，逐个触发 handler；
    - 单个 handler 异常不影响其它 handler；
    - async handler 经本总线的 TaskTracker 派发（台账持有防 GC 蒸发，
      异常终结经台账日志浮出）。
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[..., Any]]] = {}
        self._tasks = TaskTracker(name="extension-event-bus")

    def on(self, event_type: str, handler: Callable[..., Any]) -> Callable[[], None]:
        """订阅指定事件类型。"""
        handlers = self._handlers.setdefault(event_type, [])
        handlers.append(handler)

        def remove() -> None:
            try:
                handlers.remove(handler)
            except ValueError:
                pass

        return remove

    def emit(self, event_type: str, *args: Any, **kwargs: Any) -> None:
        """触发事件；不收集返回值。"""
        import asyncio

        for handler in list(self._handlers.get(event_type, [])):
            try:
                result = handler(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    self._tasks.spawn(result, name=f"bus:{event_type}")
            except Exception:
                # 单个 handler 异常不应影响其它 handler
                pass

    def clear(self) -> None:
        """清空所有 handler。"""
        self._handlers.clear()


__all__ = ["ExtensionEventBus"]
