"""nova_async —— 取消/等待机制族的家。

词汇在 ``nova_protocol``（``AbortSignal`` / ``AbortController`` /
``AbortedError`` / ``is_aborted``——被契约注解锚定，留枢纽），机制在这里：
scope 真杀（``AbortScope`` / ``await_with_abort``）、竞速等待
（``await_with_signal``）、可中断退避（``abortable_sleep``）、信号组合
（``any_signal`` / ``operation_signal``）、属主级任务台账（``TaskTracker``）。

族纪律：可中断等待原语单点归本族，**禁第三份实现**
（``docs/crosscutting/concurrency.md`` §二 取消原语族；
住所升格依据 ``docs/crosscutting/structure-modeling.md`` §二 升格出口）。
"""

from .abort import (
    AbortScope,
    abortable_sleep,
    any_signal,
    await_with_abort,
    await_with_signal,
    operation_signal,
)
from .task_tracker import TaskTracker

__all__ = [
    "AbortScope",
    "TaskTracker",
    "abortable_sleep",
    "any_signal",
    "await_with_abort",
    "await_with_signal",
    "operation_signal",
]
