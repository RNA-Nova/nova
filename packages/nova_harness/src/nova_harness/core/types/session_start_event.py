"""会话启动事件。

单独存放于 ``events`` 包之外，避免引用 ``SessionStartEvent`` 时触发
``events`` 包及其依赖的循环初始化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class SessionStartEvent:
    type: Literal["session_start"] = "session_start"
    reason: Literal["startup", "reload", "new", "resume", "fork"] = "startup"
    previous_session_file: Optional[str] = None


__all__ = ["SessionStartEvent"]
