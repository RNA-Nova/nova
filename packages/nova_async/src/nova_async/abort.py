"""Abort 竞速与信号基建（对齐 TS ``src/utils/abort.ts``）。

全仓可中断等待的公共底座：把"可中断地等待一个操作"做成原语，让 abort
语义贯穿流式请求、鉴权解析、模型刷新、executor 客户端等一切入口。

取消词汇纪律（codex 三层模型的 Python 映射）：signal 中断一律抛
``AbortedError``——它是 Exception 子类、可精确匹配；不用裸
``asyncio.CancelledError`` 假冒（那是"任务自身被取消"的 asyncio 内部
语义，混用会干扰 Task/Group 的取消判定与统计）。

执行层单轨：``AbortScope`` / ``await_with_abort`` 是**真杀**（anyio
CancelScope——一切可取消的等待：网络请求、登录流程、auth 解析、refresh
的 provider 操作、锁等待）。``CredentialStore`` 已同步化（不在取消面；
``ModelsStore`` 无锁无网络，async 形状无害，留作例外）——不存在第二种
取消原语。

竞速型成员：``await_with_signal``（asyncio 原生，signal 与 operation 赛跑、
败者取消并等落定）——与 scope 真杀型不同海拔，命名有意区分（竞速
``await_with_signal`` vs scope ``await_with_abort``）；自
``nova_protocol/signal.py`` 迁出归本族，signal.py 只留取消词汇
（对位 docs/crosscutting/concurrency.md §二 取消原语族）。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Iterable, Optional, TypeVar

import anyio

from .signal import AbortController, AbortedError, AbortSignal, is_aborted

T = TypeVar("T")

__all__ = [
    "AbortScope",
    "abortable_sleep",
    "any_signal",
    "await_with_abort",
    "await_with_signal",
    "operation_signal",
]


def operation_signal(signal: Optional[AbortSignal]) -> AbortSignal:
    """为 signal 可选的公开入口补一个永不上报中断的操作内信号（对齐 TS operationSignal）。"""
    return signal if signal is not None else AbortController(name="operation").signal


def any_signal(signals: Iterable[Optional[AbortSignal]]) -> AbortSignal:
    """任一输入中断即中断；全空时返回永不中断的操作内信号。"""
    combined = AbortSignal.any(signals)
    return combined if combined is not None else operation_signal(None)


async def abortable_sleep(ms: float, signal: Optional[AbortSignal]) -> None:
    """至多睡 ``ms`` 毫秒；期间 ``signal`` 中断立即抛 :class:`AbortedError`。

    ``signal.wait()`` 是正常返回型（中断即返回，不抛异常）——从 ``await``
    正常返回的唯一原因就是中断已经发生，必须立即抛错，否则调用方会把
    abort 静默吞掉（继续执行后续操作）。超时路径经 ``asyncio.timeout``
    的 TimeoutError 识别；任务被外部真正取消时 CancelledError 照常传播。
    """
    if is_aborted(signal):
        raise AbortedError("The operation was aborted")
    if signal is None:
        await asyncio.sleep(max(0.0, ms / 1000))
        return
    try:
        async with asyncio.timeout(max(0.0, ms / 1000)):
            await signal.wait()
        # wait() 在超时前正常返回 = 中断已发生
        raise AbortedError("The operation was aborted")
    except TimeoutError:
        return


class AbortScope:
    """signal → anyio CancelScope 的桥（真杀执行层，codex token→drop 对位）。

    用法（两 ``_stream.py`` 的请求+消费段）：

    .. code-block:: python

        abort_scope = AbortScope(options.signal)
        with abort_scope:
            ...  # 请求与消费段——signal 触发即真死
        abort_scope.raise_if_aborted()  # 块后归因翻译
        # 清理段（关流/关 client）在 scope 块外——level-triggered 语义下
        # 块内 await 会持续抛取消，cleanup 必须在块外（或 shield 区）

    语义要点（迁移尖刺实证）：

    - CancelScope 在块边界**吞掉自己的** CancelledError——块是安静退出的，
      归因必须在块后做（``cancelled_caught`` + 自家 ``_fired`` 旗标）；
    - ``_fired`` 由 signal listener 先置位再 cancel——区分"用户取消"
      （→ ``AbortedError``）与 deadline 等其它死因（超时通道独立：
      ``asyncio.timeout`` / ``idle_guarded``，与 scope 嵌套兼容已实证）；
    - ``signal=None`` 时零开销（不注册监听，scope 永不取消）。
    """

    def __init__(self, signal: Optional[AbortSignal]) -> None:
        self._signal = signal
        self._fired = False
        self._scope = anyio.CancelScope()
        self._detach: Callable[[], None] = lambda: None
        if signal is not None:

            def _on_abort(_sig: AbortSignal) -> None:
                self._fired = True
                self._scope.cancel()

            if signal.aborted:
                # 预取消：listener 不会为已触发信号补火，手动对齐
                _on_abort(signal)
            else:
                signal.add_event_listener(_on_abort)
                self._detach = lambda: signal.remove_event_listener(_on_abort)

    def __enter__(self) -> "AbortScope":
        self._scope.__enter__()
        return self

    def __exit__(self, *exc_info: Any) -> Optional[bool]:
        # 解绑在块出口（__exit__ 恒执行）——异常路径下 raise_if_aborted
        # 不会到达，监听器若只在那里解绑会随长寿命 signal 泄漏累积
        self._detach()
        return self._scope.__exit__(*exc_info)

    def raise_if_aborted(self) -> None:
        """块后归因翻译：signal 触发（或 scope 被取消）→ :class:`AbortedError`。

        约定在 scope 块**退出后**调用；signal 在块正常完成后才触发的，同样
        判取消（对齐旧版"举旗即取消"语义——块后直读 ``signal.aborted``，
        不依赖已解绑的 listener）。
        """
        if self._fired or self._scope.cancelled_caught or is_aborted(self._signal):
            raise AbortedError("Request was aborted")


async def await_with_abort(operation: Awaitable[T], signal: Optional[AbortSignal]) -> T:
    """真杀等待的一次性糖（``AbortScope`` 的便捷形态）。

    ``operation`` 在 scope 内执行——signal 触发即真杀，归因翻译抛
    :class:`AbortedError`；正常完成则返回结果。
    """
    abort_scope = AbortScope(signal)
    with abort_scope:
        result = await operation
    abort_scope.raise_if_aborted()
    return result


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

    海拔区分：本原语是 asyncio 原生**竞速型**等待；本模块的
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
