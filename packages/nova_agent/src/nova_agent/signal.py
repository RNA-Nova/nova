"""Abort signal / controller — 与 TypeScript AbortController 语义对齐。

提供 ``AbortController`` 作为中断源，``AbortSignal`` 作为可订阅、可轮询、可等待的中断信号。
"""

from __future__ import annotations

import asyncio
from typing import Callable, List


class AbortSignal:
    """中断信号。

    支持三种使用方式：
    1. 轮询 ``signal.aborted`` / ``bool(signal)``。
    2. 注册回调 ``add_event_listener(callback)``，abort 时同步触发。
    3. ``await signal.wait()`` 异步等待中断。

    与 TypeScript ``AbortSignal`` 不同点：
    - 为了兼容现有代码，保留 ``set()`` / ``clear()`` / ``reset()`` 方法。
    - 标准用法应通过 ``AbortController.abort()`` 触发。
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

    def set(self) -> None:
        """触发中断（推荐通过 ``AbortController.abort()`` 调用）。"""
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

    def clear(self) -> None:
        """清除中断状态与监听器（用于测试或信号复用场景）。"""
        self._aborted = False
        self._callbacks.clear()

    def reset(self) -> None:
        """重置中断信号（同 ``clear()``）。"""
        self.clear()

    def is_set(self) -> bool:
        """``asyncio.Event`` 风格的判断方法。"""
        return self._aborted

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

    def __bool__(self) -> bool:
        """可以直接用 ``if signal:`` 判断是否中断。"""
        return self._aborted

    def __repr__(self) -> str:
        status = "ABORTED" if self._aborted else "NORMAL"
        return f"<AbortSignal {self.name}: {status}>"

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        """Tell Pydantic to treat AbortSignal as an arbitrary Python object."""
        from pydantic_core import core_schema

        return core_schema.with_info_plain_validator_function(
            lambda value, info: value if isinstance(value, cls) else cls()
        )


class AbortController:
    """中断控制器 — 与 TypeScript ``AbortController`` 对齐。

    通过 ``abort()`` 触发，外部代码通过 ``controller.signal`` 观察和等待中断。
    """

    def __init__(self, name: str = ""):
        self._signal = AbortSignal(name)

    @property
    def signal(self) -> AbortSignal:
        """返回关联的中断信号。"""
        return self._signal

    def abort(self) -> None:
        """触发中断。多次调用无副作用。"""
        self._signal.set()


__all__ = ["AbortController", "AbortSignal"]
