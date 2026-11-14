"""Abort signal / controller — 与 TypeScript AbortController 语义对齐。

提供 ``AbortController`` 作为唯一中断源，``AbortSignal`` 作为只读、可订阅、可等待的
中断信号。signal 只能被观察，触发中断的唯一方式是调用 ``AbortController.abort()``。

登记纪律（对位 docs/crosscutting/data-modeling.md §八 行为登记制）：本模块是
枢纽的**登记制基础原语**（取消词汇，asyncio 白名单在册——纯洁性审计见
tests/test_purity.py 的 IMPORT_WHITELIST）。本模块只留取消词汇本体（TS 对齐）：

- ``AbortedError`` / ``AbortSignal`` / ``AbortController`` / ``is_aborted``。

竞速型可中断等待 ``await_with_signal``（asyncio 原生，signal 与 operation
赛跑、败者取消并等落定）已迁出归 ``nova_async``（取消/等待机制族叶子包）——
与那里的 ``await_with_abort``/``AbortScope``（anyio CancelScope 桥的
**scope 真杀型**）是不同海拔，命名有意区分：竞速 ``await_with_signal`` vs
scope ``await_with_abort``（对位 docs/crosscutting/concurrency.md §二
取消原语族）。
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Callable, Iterable, List, Optional


class AbortedError(Exception):
    """操作被中断时抛出（对齐 TS AbortError）。"""


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
        # 组合信号（any()）强持其控制器保活——源侧闭包只弱持控制器，
        # 组合信号无人引用即全链可 GC（源不挽留组合信号）
        self._owner: Optional["AbortController"] = None

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

    def throw_if_aborted(self) -> None:
        """若已中断则抛出 :class:`AbortedError`（对齐 TS throwIfAborted）。"""
        if self._aborted:
            raise AbortedError(f"Operation aborted: {self.name or 'signal'}")

    @classmethod
    def timeout(cls, ms: int) -> "AbortSignal":
        """构造 ``ms`` 毫秒后自动中断的一次性信号（对齐 TS AbortSignal.timeout）。

        需要在运行中的事件循环内调用（定时器挂在当前 loop 上）。
        """
        controller = AbortController(name=f"timeout:{ms}ms")
        timer = asyncio.get_running_loop().call_later(ms / 1000, controller.abort)

        def _cancel_timer(_sig: "AbortSignal") -> None:
            timer.cancel()

        # 先于定时器中断（如被 any 组合或手动 abort）时撤销定时器
        controller.signal.add_event_listener(_cancel_timer)
        return controller.signal

    @classmethod
    def any(cls, signals: Iterable[Optional["AbortSignal"]]) -> Optional["AbortSignal"]:
        """构造一个任一输入中断即中断的组合信号（对齐 TS AbortSignal.any）。

        全部输入为空时返回 ``None``；单一输入直接返回它本身（零开销）。
        """
        collected = [s for s in signals if s is not None]
        if not collected:
            return None
        if len(collected) == 1:
            return collected[0]
        controller = AbortController(name="any")
        # 组合信号强持控制器保活；源侧闭包只弱持控制器——组合信号被丢弃
        # 即全链可 GC，已死组合信号在源下次 abort 时自摘全部源监听器
        # （此前闭包强持 controller，长寿命源上每轮 any() 累积一个监听器）
        signal = controller.signal
        signal._owner = controller
        controller_ref = weakref.ref(controller)

        def _abort_from(_sig: "AbortSignal") -> None:
            target = controller_ref()
            if target is None:
                for source in collected:
                    source.remove_event_listener(_abort_from)
                return
            target.abort()

        def _detach(_sig: "AbortSignal") -> None:
            # 组合信号一旦终结，移除全部源监听器（长驻进程防泄漏）
            for source in collected:
                source.remove_event_listener(_abort_from)

        signal.add_event_listener(_detach)
        for source in collected:
            if source.aborted:
                controller.abort()
                break
            source.add_event_listener(_abort_from)
        return signal

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


def is_aborted(signal: Optional[AbortSignal]) -> bool:
    """Optional 信号的统一判定（``signal is not None and signal.aborted``）。

    全仓惯用法单点化：跨包共享的判定从这里取，不在各模块重复手写
    （也不跨模块 import 私有实现）。
    """
    return signal is not None and signal.aborted


__all__ = [
    "AbortController",
    "AbortedError",
    "AbortSignal",
    "is_aborted",
]
