"""Abort signal / controller — 与 TypeScript AbortController 语义对齐。

提供 ``AbortController`` 作为唯一中断源，``AbortSignal`` 作为只读、可订阅、可等待的
中断信号。signal 只能被观察，触发中断的唯一方式是调用 ``AbortController.abort()``。

登记纪律（对位 docs/crosscutting/data-modeling.md §八 行为登记制）：本模块是
枢纽的**登记制基础原语**（取消原语族，asyncio 白名单在册——纯洁性审计见
tests/test_purity.py 的 IMPORT_WHITELIST）。取消原语族成员：

- ``AbortedError`` / ``AbortSignal`` / ``AbortController`` / ``is_aborted``：
  取消词汇本体（TS 对齐）；
- ``await_with_signal``：**竞速型**可中断等待（asyncio 原生，signal 与
  operation 赛跑、败者取消并等落定）。与 nova_ai ``utils/abort.py`` 的
  ``await_with_abort``/``AbortScope``（anyio CancelScope 桥的 **scope 真杀型**）
  是不同海拔——命名有意区分：竞速 ``await_with_signal`` vs scope
  ``await_with_abort``；依赖方向铁律下本包不得 import nova_ai，竞速族在此以
  asyncio 原生实现（对位 docs/crosscutting/concurrency.md §二 取消原语族）。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Awaitable, Callable, Iterable, List, Optional, TypeVar

T = TypeVar("T")


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

        def _abort_from(_sig: "AbortSignal") -> None:
            controller.abort()

        def _detach(_sig: "AbortSignal") -> None:
            # 组合信号一旦终结，移除全部源监听器（长驻进程防泄漏）
            for source in collected:
                source.remove_event_listener(_abort_from)

        for source in collected:
            if source.aborted:
                controller.abort()
                break
            source.add_event_listener(_abort_from)
        controller.signal.add_event_listener(_detach)
        return controller.signal

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


async def await_with_signal(
    operation: Awaitable[T], signal: Optional[AbortSignal]
) -> T:
    """竞速等待（取消原语族成员）：``signal`` 触发即取消 ``operation`` 的等待并抛
    :class:`AbortedError`；正常完成返回结果。

    - ``signal`` 为 ``None`` → 直通（零开销：不建竞速任务，就地 await）；
    - 预触发信号：先 ``throw_if_aborted`` 快失败（举旗点语义——未启动的协程
      显式关闭，不留 "coroutine was never awaited" 告警；已就位的 Future 归
      调用方处置）；
    - 竞速裁决**完成优先**（completion-wins——同刻就绪时已完成的工作不丢弃，
      对位 docs/crosscutting/concurrency.md §六）；
    - signal 胜出：取消 operation 任务并 ``await`` 其取消落定（防悬挂任务泄漏），
      抛 :class:`AbortedError`；operation 落定中自带失败的归因取消词汇
      （signal 已胜出，不冒名也不放活）。

    海拔区分：本原语是 asyncio 原生**竞速型**等待；nova_ai 的
    ``await_with_abort``/``AbortScope`` 是 anyio CancelScope 桥的 **scope
    真杀型**（块内一切等待真死）——不同海拔不撞名。
    """
    if signal is None:
        return await operation
    if signal.aborted:
        # 举旗点快失败（对位 concurrency.md §八"举旗点：pre-aborted 不发请求"）
        if inspect.iscoroutine(operation):
            operation.close()
        signal.throw_if_aborted()
    op_task = asyncio.ensure_future(operation)
    abort_task = asyncio.create_task(signal.wait())
    try:
        try:
            done, _pending = await asyncio.wait(
                (op_task, abort_task), return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            # 本任务被外部取消（基础设施收尾语义，非 signal）：两路一并取消
            # 等落定后照常传播——两词汇永不混用，也不留悬挂任务
            op_task.cancel()
            abort_task.cancel()
            await asyncio.gather(op_task, abort_task, return_exceptions=True)
            raise
        if op_task in done:
            # 完成优先：同刻就绪时已完成的工作不丢弃（operation 自身异常同样
            # 原样归属——result() 会把它重新抛出）
            return op_task.result()
        # signal 胜出：取消 operation 并等其取消落定
        op_task.cancel()
        try:
            await op_task
        except asyncio.CancelledError:
            if not op_task.cancelled():
                raise  # 落定等待期本任务被外部取消——取消照常传播
        except Exception:
            # operation 以自身失败落定——signal 已胜出，归因取消词汇
            pass
        # 胜出分支信号必已触发——throw_if_aborted 恒抛 AbortedError
        signal.throw_if_aborted()
        raise AssertionError("await_with_signal: 胜出分支 throw_if_aborted 未抛")
    finally:
        # abort 等待任务收尾：完成/异常路径即取消落定，不悬挂
        if not abort_task.done():
            abort_task.cancel()
            try:
                await abort_task
            except asyncio.CancelledError:
                pass


__all__ = [
    "AbortController",
    "AbortedError",
    "AbortSignal",
    "await_with_signal",
    "is_aborted",
]
