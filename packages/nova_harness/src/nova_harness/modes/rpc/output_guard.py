"""Compatibility re-export for stdout guard utilities.

The canonical implementation now lives in :mod:`nova_harness.core.utils.output_guard`
so that non-RPC modules (e.g. package backends) can also guard child-process stdio.
"""

from nova_harness.core.utils.output_guard import (
    OutputGuard,
    flush_raw_stdout,
    is_stdout_taken_over,
    restore_stdout,
    take_over_stdout,
    wait_for_raw_stdout_backpressure,
    write_raw_stdout,
)

__all__ = [
    "OutputGuard",
    "flush_raw_stdout",
    "is_stdout_taken_over",
    "restore_stdout",
    "take_over_stdout",
    "wait_for_raw_stdout_backpressure",
    "write_raw_stdout",
]
