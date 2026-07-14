"""Package install/update progress events."""

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class ProgressEvent:
    """Progress event emitted during package install/update operations.

    - ``type``: 事件阶段
    - ``action``: 操作类型
    - ``source``: 正在处理的包 source spec
    - ``message``: 可选人类可读信息
    - ``percent``: 可选进度百分比
    """

    type: Literal["start", "progress", "complete", "error"]
    action: Literal["install", "remove", "update", "clone", "pull"]
    source: str
    message: Optional[str] = None
    percent: Optional[float] = None


ProgressCallback = "callable"


__all__ = ["ProgressEvent", "ProgressCallback"]
