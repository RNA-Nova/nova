"""Abort signal / controller — 与 TypeScript AbortController 语义对齐。

提供 ``AbortController`` 作为唯一中断源，``AbortSignal`` 作为只读、可订阅、可等待的
中断信号。signal 只能被观察，触发中断的唯一方式是调用 ``AbortController.abort()``。
"""

from __future__ import annotations

import asyncio
from typing import Callable, List


class AbortSignal:
    """只读中断信号。

    使用方式：
    1. 轮询 ``signal.aborted``。
    2. 注册回调 ``add_event_listener(callback)``，abort 时同步触发。
    3. ``await signal.wait()`` 异步等待中断。

    ``AbortSignal`` 本身不提供任何触发或重置方法；触发权只属于 ``AbortController``。
    """

    def __init__(self, name: str = ""):
        self.name = name
        self._aborted = False
        self._callbacks: List[Callable[["AbortSignal"], None]] = []

    @property
    def aborted(self) -> bool:
        """是否已被中断（只读）。"""
        return self._aborted

    def add_event_listener(self, callback: Callable[["AbortSignal"], None]) -> None:
        """注册 abort 事件监听器。"""
        self._callbacks.append(callback)

    def remove_event_listener(self, callback: Callable[["AbortSignal"], None]) -> None:
        """移除 abort 事件监听器。"""
        try:
            self._callbacks.remove(callback)
        except ValueError:
            pass

    def _trigger(self) -> None:
        """触发中断。仅允许 ``AbortController`` 调用。"""
        if self._aborted:
            return
        self._aborted = True
        # 复制列表避免回调里修改监听器列表导致异常
        for cb in list(self._callbacks):
            try:
                cb(self)
            except Exception:
                # 监听器异常不应影响其他监听器和中断传播
                pass

    async def wait(self) -> None:
        """异步等待中断。若已中断则立即返回。"""
        if self._aborted:
            return

        event = asyncio.Event()

        def _on_abort(_signal: AbortSignal) -> None:
            event.set()

        self.add_event_listener(_on_abort)
        try:
            await event.wait()
        finally:
            self.remove_event_listener(_on_abort)

    def __repr__(self) -> str:
        status = "ABORTED" if self._aborted else "NORMAL"
        return f"<AbortSignal {self.name}: {status}>"


class AbortController:
    """中断控制器 — 唯一允许触发中断的对象。

    通过 ``abort()`` 触发，外部代码通过 ``controller.signal`` 观察和等待中断。
    """

    def __init__(self, name: str = ""):
        self._signal = AbortSignal(name)

    @property
    def signal(self) -> AbortSignal:
        """返回关联的只读中断信号。"""
        return self._signal

    def abort(self) -> None:
        """触发中断。多次调用无副作用。"""
        self._signal._trigger()


__all__ = ["AbortController", "AbortSignal"]
