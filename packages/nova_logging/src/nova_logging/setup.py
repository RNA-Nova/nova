"""组合根日志装配：多 sink、有界队列、flush 回执与超时关停。"""

from __future__ import annotations

import copy
import logging
import logging.handlers
import queue
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from types import TracebackType
from typing import Optional, cast

from structlog.contextvars import get_contextvars

from .events import CONTEXT_FIELDS
from .formatter import Redactor, build_formatter
from .sinks import FailureReporter, SinkSettings, TargetFilter, create_sink

_CONFIGURATION_LOCK = threading.Lock()
_ACTIVE_RUNTIME: Optional[LoggingRuntime] = None


@dataclass(frozen=True, kw_only=True)
class LoggingStats:
    """运行期丢弃快照；计数只增，快照本身不可变。"""

    dropped_records: int
    sink_failures: int


class _Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dropped_records = 0
        self._sink_failures = 0

    def record_drop(self) -> None:
        with self._lock:
            self._dropped_records += 1

    def record_sink_failure(self) -> None:
        with self._lock:
            self._sink_failures += 1

    def snapshot(self) -> LoggingStats:
        with self._lock:
            return LoggingStats(
                dropped_records=self._dropped_records,
                sink_failures=self._sink_failures,
            )


def _report_failure(error: Optional[BaseException]) -> None:
    """诊断出口只报异常类型，绝不回显可能含密钥的记录或异常文案。"""
    try:
        name = type(error).__name__ if error is not None else "UnknownError"
        sys.stderr.write(f"nova_logging: record dropped ({name})\n")
    except Exception:
        pass


@dataclass(eq=False)
class _FlushRequest:
    done: threading.Event


class _NovaQueueHandler(logging.handlers.QueueHandler):
    """在调用线程捕获 context/异常；队列与线程仍完全由 stdlib 实现。"""

    def __init__(self, records: queue.Queue[object], stats: _Stats) -> None:
        super().__init__(records)
        self._stats = stats
        self._drop_reported = False

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        record = copy.copy(record)
        for key, value in get_contextvars().items():
            if key in CONTEXT_FIELDS:
                setattr(record, key, value)
        if record.exc_info is not None:
            # QueueHandler 默认会把 traceback 拼进 event 并丢掉 exc_info；
            # 先把异常放进独立字段，ProcessorFormatter 才能统一清洗出口。
            record.exception = "".join(traceback.format_exception(*record.exc_info))
            record.exc_info = None
            record.exc_text = None
        return super().prepare(record)

    def handleError(self, record: logging.LogRecord) -> None:
        # 默认实现会把原始 record 打到 stderr；满队列也只丢日志，不泄漏载荷。
        self._stats.record_drop()
        if not self._drop_reported:
            self._drop_reported = True
            _report_failure(sys.exc_info()[1])


class _NovaQueueListener(logging.handlers.QueueListener):
    """支持 flush 屏障，并保护监听线程不被意外出口异常杀死。"""

    def __init__(
        self,
        records: queue.Queue[object],
        handlers: tuple[logging.Handler, ...],
        on_failure: FailureReporter,
    ) -> None:
        super().__init__(records, *handlers, respect_handler_level=True)
        self._on_failure = on_failure

    def handle(self, record: object) -> None:
        if isinstance(record, _FlushRequest):
            record.done.set()
            return
        try:
            super().handle(cast(logging.LogRecord, record))
        except Exception as error:
            self._on_failure(error)


@dataclass(frozen=True, kw_only=True)
class LoggingSettings:
    """进程级日志配置；出口独立过滤，队列与清洗策略全局共享。"""

    sinks: tuple[SinkSettings, ...] = (SinkSettings(),)
    queue_capacity: int = 1000
    redactor: Optional[Redactor] = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.sinks:
            raise ValueError("At least one log sink is required")
        if self.queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")


class LoggingRuntime:
    """一套已装配的日志出口；只能经 ``configure_logging()`` 创建。"""

    def __init__(self, settings: LoggingSettings) -> None:
        self._root = logging.getLogger()
        self._previous_handlers = list(self._root.handlers)
        self._previous_level = self._root.level
        self._stats = _Stats()
        self._sink_failure_reported = False
        self._sinks = tuple(
            create_sink(sink, self._record_sink_failure) for sink in settings.sinks
        )
        for sink_settings, handler in zip(settings.sinks, self._sinks, strict=True):
            # Handler 先按级别粗筛、Filter 后运行；粗筛必须放到最低目标级，
            # 否则 target_levels 中更宽松的 DEBUG 根本没有机会生效。
            handler.setLevel(
                min((sink_settings.level, *sink_settings.target_levels.values()))
            )
            handler.addFilter(
                TargetFilter(sink_settings.level, sink_settings.target_levels)
            )
            handler.setFormatter(
                build_formatter(
                    json_output=sink_settings.json_output,
                    redactor=settings.redactor,
                )
            )
        self._queue: queue.Queue[object] = queue.Queue(maxsize=settings.queue_capacity)
        self._handler = _NovaQueueHandler(self._queue, self._stats)
        self._listener = _NovaQueueListener(
            self._queue, self._sinks, self._record_sink_failure
        )
        self._state_lock = threading.Lock()
        self._close_started = False
        self._close_complete = False
        self._close_worker: Optional[threading.Thread] = None
        self._listener.start()
        self._root.handlers = [self._handler]
        levels = [
            level
            for sink in settings.sinks
            for level in (sink.level, *sink.target_levels.values())
        ]
        self._root.setLevel(min(levels))

    def __enter__(self) -> LoggingRuntime:
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.close()

    def _record_sink_failure(self, error: Optional[BaseException]) -> None:
        self._stats.record_sink_failure()
        if not self._sink_failure_reported:
            self._sink_failure_reported = True
            _report_failure(error)

    def stats(self) -> LoggingStats:
        return self._stats.snapshot()

    def flush(self, timeout: Optional[float] = None) -> None:
        """等待本调用之前接受的记录写完；超时抛 ``TimeoutError``。

        ``timeout=None`` 表示无限等待。超时只放弃等待，不撤销 flush
        屏障，也不丢弃尚未处理的记录。
        """
        with self._state_lock:
            if self._close_complete:
                return
            if self._close_started:
                raise RuntimeError("Cannot flush while logging runtime is closing")
        self._flush_records(timeout)

    def _flush_records(self, timeout: Optional[float]) -> None:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")
        deadline = None if timeout is None else time.monotonic() + timeout
        request = _FlushRequest(done=threading.Event())
        try:
            if timeout is None:
                self._queue.put(request)
            else:
                self._queue.put(request, timeout=timeout)
        except queue.Full:
            raise TimeoutError("Log flush timed out while entering the queue") from None
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        if not request.done.wait(remaining):
            raise TimeoutError("Log flush timed out while draining the queue")

    def close(self, timeout: Optional[float] = 5.0) -> None:
        """有界等待关停；超时后台仍继续排干，可再次调用等待完成。

        ``timeout=None`` 表示无限等待。后台线程为非 daemon，解释器也会
        等待它；阻塞 sink 仍可能挡住进程退出，强制退出须由外层 watchdog 决定。
        """
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")
        with self._state_lock:
            if self._close_complete:
                return
            if not self._close_started:
                self._close_started = True
                self._root.removeHandler(self._handler)
                self._close_worker = threading.Thread(
                    target=self._finish_close,
                    name="nova-logging-shutdown",
                    daemon=False,
                )
                self._close_worker.start()
            worker = cast(threading.Thread, self._close_worker)
        worker.join(timeout)
        if worker.is_alive():
            raise TimeoutError("Log shutdown exceeded its time budget")

    def _finish_close(self) -> None:
        global _ACTIVE_RUNTIME
        try:
            try:
                self._flush_records(None)
            except Exception as error:
                _report_failure(error)
            try:
                self._listener.stop()
            except Exception as error:
                _report_failure(error)
            try:
                self._handler.close()
            except Exception as error:
                _report_failure(error)
            for sink in self._sinks:
                try:
                    sink.close()
                except Exception as error:
                    self._record_sink_failure(error)
        finally:
            current = [
                handler
                for handler in self._root.handlers
                if handler not in self._previous_handlers
            ]
            self._root.handlers = current + [
                handler for handler in self._previous_handlers if handler not in current
            ]
            self._root.setLevel(self._previous_level)
            with _CONFIGURATION_LOCK:
                if _ACTIVE_RUNTIME is self:
                    _ACTIVE_RUNTIME = None
                with self._state_lock:
                    self._close_complete = True


def configure_logging(settings: Optional[LoggingSettings] = None) -> LoggingRuntime:
    """为当前进程装配唯一活跃日志出口；仅组合根调用。

    上一代完全关闭前再次装配会响亮失败，避免 root handler 与级别交叉恢复。
    """
    global _ACTIVE_RUNTIME
    settings = settings or LoggingSettings()
    with _CONFIGURATION_LOCK:
        if _ACTIVE_RUNTIME is not None:
            raise RuntimeError(
                "Logging is already configured; close the active runtime first"
            )
        runtime = LoggingRuntime(settings)
        _ACTIVE_RUNTIME = runtime
        return runtime


__all__ = [
    "LoggingRuntime",
    "LoggingSettings",
    "LoggingStats",
    "SinkSettings",
    "configure_logging",
]
