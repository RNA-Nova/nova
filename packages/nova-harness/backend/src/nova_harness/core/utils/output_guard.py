"""Protect stdout channel from accidental writes by third-party code.

When running in RPC mode over stdio, every byte written to ``sys.stdout``
belongs to the protocol. Logs, stray ``print()`` calls from dependencies, or
extension code must not corrupt the NDJSON stream. ``OutputGuard`` redirects
all non-protocol writes to ``stderr`` while allowing the transport to write
protocol frames normally.

This mirrors pi's ``output-guard.ts``. In addition to the context-manager API,
module-level helpers ``take_over_stdout`` / ``restore_stdout`` /
``write_raw_stdout`` / ``flush_raw_stdout`` are provided for callers that
prefer an explicit global singleton style.
"""

import logging
import sys
import threading
from contextlib import contextmanager
from types import TracebackType
from typing import Optional, TextIO, Type


class OutputGuard:
    """Context manager that redirects non-protocol stdout writes to stderr."""

    def __init__(
        self,
        stdout: Optional[TextIO] = None,
        stderr: Optional[TextIO] = None,
    ) -> None:
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self._original_write: Optional[callable] = None
        self._local = threading.local()
        self._logging_handlers_changed: list[logging.Handler] = []

    @property
    def is_installed(self) -> bool:
        """Return whether the guard is currently active."""
        return self._original_write is not None

    def _in_protocol_write(self) -> bool:
        return getattr(self._local, "protocol_depth", 0) > 0

    @contextmanager
    def protocol_write(self):
        """Mark a code block as an allowed protocol write to stdout."""
        old = getattr(self._local, "protocol_depth", 0)
        self._local.protocol_depth = old + 1
        try:
            yield
        finally:
            self._local.protocol_depth = old

    def _guarded_write(self, data: str) -> int:
        """Write data to stdout only if inside a protocol write block."""
        if self._in_protocol_write():
            # type: ignore[misc] # original_write is set when installed
            return self._original_write(data)
        try:
            self._stderr.write(data)
            self._stderr.flush()
        except Exception:
            pass
        return (
            len(data)
            if isinstance(data, str)
            else len(data.encode("utf-8", errors="replace"))
        )

    def install(self) -> None:
        """Install the guard. Must be paired with :meth:`uninstall`."""
        if self._original_write is not None:
            return
        self._original_write = self._stdout.write
        self._stdout.write = self._guarded_write
        self._redirect_logging_handlers()

    def uninstall(self) -> None:
        """Restore original stdout.write and logging handlers."""
        if self._original_write is not None:
            self._stdout.write = self._original_write
            self._original_write = None
        self._restore_logging_handlers()

    def write_raw_stdout(self, text: str) -> None:
        """Write text to the original stdout, bypassing redirection to stderr.

        This is useful for callers that have taken over stdout but still need
        to emit raw protocol or print-mode output.
        """
        if not text:
            return
        with self.protocol_write():
            self._stdout.write(text)

    def flush_raw_stdout(self) -> None:
        """Flush any buffered raw stdout output."""
        with self.protocol_write():
            self._stdout.flush()

    def _redirect_logging_handlers(self) -> None:
        """Redirect logging handlers targeting stdout to stderr."""
        handlers: list[logging.Handler] = []
        for logger in self._walk_loggers():
            handlers.extend(logger.handlers)
        for handler in handlers:
            if (
                isinstance(handler, logging.StreamHandler)
                and handler.stream is self._stdout
            ):
                handler.stream = self._stderr
                self._logging_handlers_changed.append(handler)

    def _restore_logging_handlers(self) -> None:
        """Restore logging handlers previously redirected to stderr."""
        for handler in self._logging_handlers_changed:
            if (
                isinstance(handler, logging.StreamHandler)
                and handler.stream is self._stderr
            ):
                handler.stream = self._stdout
        self._logging_handlers_changed = []

    @staticmethod
    def _walk_loggers() -> list[logging.Logger]:
        """Return root logger and all registered loggers."""
        manager = getattr(logging.root, "manager", None)
        logger_dict = getattr(manager, "loggerDict", {}) if manager is not None else {}
        loggers: list[logging.Logger] = [logging.root]
        for logger in logger_dict.values():
            if isinstance(logger, logging.Logger):
                loggers.append(logger)
        return loggers

    def __enter__(self) -> "OutputGuard":
        self.install()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.uninstall()


# ---------------------------------------------------------------------------
# Module-level global singleton helpers (TS output-guard.ts style)
# ---------------------------------------------------------------------------

_global_guard: Optional[OutputGuard] = None


def take_over_stdout(
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> OutputGuard:
    """Install a global stdout takeover guard.

    Repeated calls return the same guard instance.
    """
    global _global_guard
    if _global_guard is None:
        _global_guard = OutputGuard(stdout=stdout, stderr=stderr)
        _global_guard.install()
    return _global_guard


def restore_stdout() -> None:
    """Uninstall the global stdout takeover guard."""
    global _global_guard
    if _global_guard is not None:
        _global_guard.uninstall()
        _global_guard = None


def is_stdout_taken_over() -> bool:
    """Return whether stdout is currently taken over by the global guard."""
    return _global_guard is not None and _global_guard.is_installed


def write_raw_stdout(text: str) -> None:
    """Write raw text to stdout when a global takeover is active.

    Falls back to plain ``sys.stdout.write`` if no takeover is active.
    """
    if _global_guard is not None:
        _global_guard.write_raw_stdout(text)
    else:
        sys.stdout.write(text)


async def wait_for_raw_stdout_backpressure() -> None:
    """Wait for any pending raw stdout writes to complete.

    Python ``sys.stdout.write`` is synchronous, so this is a no-op kept for
    API parity with the TypeScript implementation.
    """
    return


async def flush_raw_stdout() -> None:
    """Flush raw stdout output."""
    await wait_for_raw_stdout_backpressure()
    if _global_guard is not None:
        _global_guard.flush_raw_stdout()
    else:
        sys.stdout.flush()


__all__ = [
    "OutputGuard",
    "take_over_stdout",
    "restore_stdout",
    "is_stdout_taken_over",
    "write_raw_stdout",
    "wait_for_raw_stdout_backpressure",
    "flush_raw_stdout",
]
