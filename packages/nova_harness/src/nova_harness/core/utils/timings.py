"""启动耗时埋点工具。

- 通过 ``NOVA_TIMING=1`` 环境变量开启。
- 提供 ``reset_timings()``、``time(label)``、``print_timings()`` 三个入口。
- 用于在 CLI / RPC 启动阶段对资源加载、扩展加载、会话创建等步骤做基础耗时分析。
"""

import os
import sys
import time
from typing import List, Tuple

# 是否启用启动耗时埋点
_ENABLED = os.environ.get("NOVA_TIMING") == "1"

# 已记录的 (label, ms) 列表
_timings: List[Tuple[str, int]] = []

# 上一次记录的时间戳（毫秒）
_last_time_ms: int = 0


def reset_timings() -> None:
    """重置计时器。"""
    global _timings, _last_time_ms
    if not _ENABLED:
        return
    _timings = []
    _last_time_ms = _now_ms()


def time(label: str) -> None:
    """记录当前时刻与上一次 ``time()`` 之间的间隔。"""
    global _last_time_ms
    if not _ENABLED:
        return
    now = _now_ms()
    _timings.append((label, now - _last_time_ms))
    _last_time_ms = now


def print_timings() -> None:
    """将累计的耗时打印到 stderr。"""
    if not _ENABLED or not _timings:
        return
    lines = ["\n--- Startup Timings ---"]
    for label, ms in _timings:
        lines.append(f"  {label}: {ms}ms")
    total = sum(ms for _, ms in _timings)
    lines.append(f"  TOTAL: {total}ms")
    lines.append("------------------------\n")
    sys.stderr.write("\n".join(lines))


def _now_ms() -> int:
    """返回当前时间戳（毫秒）。"""
    return int(time.time() * 1000)


__all__ = ["reset_timings", "time", "print_timings"]
