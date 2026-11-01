"""Deadline —— 单调时钟预算值对象（时间族·预算值对象）。

族与纪律（``docs/crosscutting/structure-modeling.md`` §二第 2 层族文件）：

- **预算制多步操作**：整轮操作共享一个 deadline，各阶段只花剩余时间
  （codex 对位：``deadline - now`` 剩余预算逐阶段下发）——防多阶段
  各自为政、累积成无穷等待；
- **纯值对象**：数据建模规则 5——frozen dataclass 锁死不可变；
- **策略留消费方**：具体预算缺省（如 refresh 的 90s）是业务策略件，
  不进本件。

升格自 nova_ai ``gateway/deadline.py``（SDK recovery 编排出现第二个
消费方，按 §二升格路径搬家）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

__all__ = ["Deadline"]


@dataclass(frozen=True)
class Deadline:
    """单调时钟 deadline：``remaining_ms`` 随时间递减，耗尽归零（不返负）。

    时钟经 ``time.monotonic`` 模块属性运行期读取（测试可注入——
    ``default_factory`` 会在类定义时固化函数引用，故用 ``__post_init__``）。
    """

    timeout_ms: float
    _started: Optional[float] = None

    def __post_init__(self) -> None:
        if self._started is None:
            object.__setattr__(self, "_started", time.monotonic())

    @property
    def remaining_ms(self) -> float:
        """剩余预算（毫秒）；已耗尽时为 0。"""
        assert self._started is not None
        elapsed_ms = (time.monotonic() - self._started) * 1000
        return max(0.0, self.timeout_ms - elapsed_ms)

    @property
    def remaining_s(self) -> float:
        """剩余预算（秒）——``asyncio.wait_for`` / ``asyncio.timeout`` 吃秒。"""
        return self.remaining_ms / 1000

    @property
    def expired(self) -> bool:
        """预算是否已耗尽。"""
        return self.remaining_ms <= 0
