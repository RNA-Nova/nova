"""backoff —— 指数退避序列（时间族·退避序列——机制件）。

族与纪律（``docs/crosscutting/structure-modeling.md`` §二第 2 层族文件）：

- **无状态函数**：形状对位 codex ``async-utils/backoff.rs`` 与
  ``codex-client/retry.rs`` 的 ``backoff(base, attempt)``——attempt
  计数由消费方各自的重试循环持有，本件只管"第 n 次等多久"；
- **down-only jitter**：``(1 - random()*jitter)`` 取 pi/OpenAI SDK 的
  下抖语义——与 codex 对称 ±10% 抖动的分歧是**有意**（传输层镜像 SDK
  语义优先）；
- **可中断性不归这里**：本件只算时长；睡眠的可中断性由消费方配
  ``abortable_sleep``（可中断等待单点，docs/crosscutting/concurrency.md
  §二）——本件不掺 signal。
"""

from __future__ import annotations

import random

__all__ = ["backoff_delay_ms"]


def backoff_delay_ms(
    attempt: int, *, base_ms: float, cap_ms: float, jitter: float = 0.25
) -> float:
    """第 ``attempt`` 次重试的退避时长（毫秒）：指数翻倍、封顶、下抖。

    - ``attempt``：1-indexed（对位 codex ``attempt.saturating_sub(1)``——
      小于 1 按 1 算）；
    - 序列：``min(base_ms * 2**(attempt-1), cap_ms)``；
    - ``jitter``：down-only 抖动幅度，合法域 ``[0, 1)``——越界
      ``ValueError``；``0`` 关闭抖动（精确值——无抖动消费方与测试用）。
    """
    if not 0 <= jitter < 1:
        raise ValueError(f"jitter 须在 [0, 1) 区间，收到 {jitter}")
    exponent = max(attempt, 1) - 1  # 饱和钳位（对位 saturating_sub(1)）
    delay_ms = min(base_ms * 2**exponent, cap_ms)
    if jitter == 0:
        return delay_ms
    return delay_ms * (1 - random.random() * jitter)
