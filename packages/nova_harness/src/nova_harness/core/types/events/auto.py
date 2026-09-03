"""AgentSession 自动触发的内部事件。"""

from __future__ import annotations

from enum import Enum, auto
from typing import List, Literal, Optional

from nova_ai import ModelThinkingLevel
from nova_ai.types.base_model import NovaBaseModel
from nova_harness.core.types.compaction import CompactionResult
from pydantic import Field

from .constants import (
    AUTO_COMPACTION_END,
    AUTO_COMPACTION_START,
    AUTO_RETRY_END,
    AUTO_RETRY_START,
    MODEL_CHANGED,
    QUEUE_UPDATE,
    SESSION_INFO_CHANGED,
    SESSION_RELOADED,
    SESSION_REPLACED,
    THINKING_LEVEL_CHANGED,
    USER_TOOL,
)


class AutoCompactionReason(str, Enum):
    """自动压缩触发原因（str 子类：线上序列化为干净值 "threshold"/"overflow"）"""

    THRESHOLD = "threshold"
    OVERFLOW = "overflow"


class AutoCompactionStartEvent(NovaBaseModel):
    type: Literal["auto_compaction_start"] = AUTO_COMPACTION_START
    reason: AutoCompactionReason = Field(
        default_factory=lambda: AutoCompactionReason.THRESHOLD
    )


class AutoCompactionEndEvent(NovaBaseModel):
    type: Literal["auto_compaction_end"] = AUTO_COMPACTION_END
    result: Optional[CompactionResult] = None
    aborted: bool = False
    will_retry: bool = False
    error_message: Optional[str] = None


class AutoRetryStartEvent(NovaBaseModel):
    type: Literal["auto_retry_start"] = AUTO_RETRY_START
    attempt: int = 0
    max_attempts: int = 0
    delay_ms: int = 0
    error_message: str = ""


class AutoRetryEndEvent(NovaBaseModel):
    type: Literal["auto_retry_end"] = AUTO_RETRY_END
    success: bool = False
    attempt: int = 0
    final_error: Optional[str] = None


class QueueUpdateEvent(NovaBaseModel):
    type: Literal["queue_update"] = QUEUE_UPDATE
    steering: List[str] = Field(default_factory=list)
    follow_up: List[str] = Field(default_factory=list)


class SessionInfoChangedEvent(NovaBaseModel):
    """会话信息变更直写通知（payload = 三个字段的当前全量值，非增量——

    前端快照无脑直写，省一趟 pull；None 即真值（未命名/无 override），
    不存在"字段缺席 vs 清除"的歧义）。
    """

    type: Literal["session_info_changed"] = SESSION_INFO_CHANGED
    name: Optional[str] = None
    agent: Optional[str] = None
    persona_override: Optional[str] = None


class SessionReloadedEvent(NovaBaseModel):
    """资源/扩展重载完成通知（/reload、/trust 后的自动重载）。

    前端据此刷新包 UI 贡献（slots 整体重载）——此前重载只发生在后端，
    前端包渲染器/对话框/slot 命令要到下次启动才刷新。
    """

    type: Literal["session_reloaded"] = SESSION_RELOADED
    reason: str = "reload"


class SessionReplacedEvent(NovaBaseModel):
    """会话内容整体替换通知（Bus 2）。

    发射点覆盖全部替换路径：runtime 重建（new/resume/fork）、
    AgentSession 原地切换（new/resume/fork/clone/import）与树导航
    （navigate）。前端据此全量重同步（快照 + 历史条目）——后端持有
    会话单一事实源，不通知则前端 transcript 永远停留在旧会话。
    """

    type: Literal["session_replaced"] = SESSION_REPLACED
    reason: str = ""


class ThinkingLevelChangedEvent(NovaBaseModel):
    type: Literal["thinking_level_changed"] = THINKING_LEVEL_CHANGED
    level: Optional[ModelThinkingLevel] = None


class ModelChangedEvent(NovaBaseModel):
    """模型变更通知（Bus 2，对齐 ThinkingLevelChangedEvent）。"""

    type: Literal["model_changed"] = MODEL_CHANGED
    model: Optional[object] = None
    previous_model: Optional[object] = None
    source: str = ""


class UserToolEvent(NovaBaseModel):
    """用户工具进度事件。

    用户工具的 ``on_event`` 通道经会话事件总线透出（RPC 事件桥以
    ``{type, data}`` 信封直通，本事件 type 为 ``user_tool``），前端按
    ``tool`` / ``event`` 自行特化渲染。
    """

    type: Literal["user_tool"] = USER_TOOL
    tool: str = ""
    event: str = ""
    data: Optional[dict] = None
    call_id: Optional[str] = None
