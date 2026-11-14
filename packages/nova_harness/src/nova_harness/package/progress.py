"""Package install/update progress events."""

from typing import Callable, Literal, Optional

from nova_protocol import NovaBaseModel

ProgressEventType = Literal["start", "progress", "complete", "error"]
ProgressAction = Literal["install", "remove", "update", "clone", "pull", "npm"]


class ProgressEvent(NovaBaseModel):
    """Progress event emitted during package install/update operations.

    经 ``package_progress`` 通知桥接到前端（RPC 边界），按数据建模规则 2
    使用 Pydantic——桥接处直接 ``model_dump()``，无需手动逐字段转换。

    - ``type``: 事件阶段
    - ``action``: 操作类型
    - ``source``: 正在处理的包 source spec
    - ``message``: 可选人类可读信息
    - ``percent``: 可选进度百分比
    """

    type: ProgressEventType
    action: ProgressAction
    source: str
    message: Optional[str] = None
    percent: Optional[float] = None


ProgressCallback = Callable[[ProgressEvent], None]

__all__ = ["ProgressAction", "ProgressEvent", "ProgressEventType", "ProgressCallback"]
