"""nova_async —— 取消词汇与等待机制族的家。

``AbortSignal`` / ``AbortController`` / ``AbortedError`` / ``is_aborted``
与 scope 真杀（``AbortScope`` / ``await_with_abort``）、竞速等待
（``await_with_signal``）、可中断退避（``abortable_sleep``）、信号组合
（``any_signal`` / ``operation_signal``）、属主级任务台账（``TaskTracker``）、
per-key 编排锁（``KeyedLockFactory`` / ``in_memory_keyed_locks``）、
时间预算值对象（``Deadline``）、指数退避序列（``backoff_delay_ms``）统一归本包。

族纪律：可中断等待原语单点归本族，**禁第三份实现**
（``docs/crosscutting/concurrency.md`` §二 取消原语族；
住所依据 ``docs/crosscutting/structure-modeling.md``）。
"""

from .abort import (
    AbortScope,
    abortable_sleep,
    any_signal,
    await_with_abort,
    await_with_signal,
    operation_signal,
)
from .backoff import backoff_delay_ms
from .deadline import Deadline
from .keyed_lock import KeyedLockFactory, default_keyed_locks, in_memory_keyed_locks
from .signal import AbortController, AbortedError, AbortSignal, is_aborted
from .task_tracker import TaskTracker

__all__ = [
    "AbortController",
    "AbortScope",
    "AbortSignal",
    "AbortedError",
    "Deadline",
    "KeyedLockFactory",
    "TaskTracker",
    "abortable_sleep",
    "any_signal",
    "await_with_abort",
    "await_with_signal",
    "backoff_delay_ms",
    "default_keyed_locks",
    "in_memory_keyed_locks",
    "is_aborted",
    "operation_signal",
]
