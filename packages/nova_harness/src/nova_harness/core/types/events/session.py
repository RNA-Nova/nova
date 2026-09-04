"""会话生命周期事件。"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

from nova_harness.core.types.compaction import CompactionPreparation
from nova_harness.core.types.session.entries import SessionEntry

from .constants import (
    COMPACTION_END,
    COMPACTION_START,
    SESSION_BEFORE_COMPACT,
    SESSION_BEFORE_FORK,
    SESSION_BEFORE_SWITCH,
    SESSION_BEFORE_TREE,
    SESSION_COMPACT,
    SESSION_SHUTDOWN,
    SESSION_TREE,
)


class SessionStartEvent(NovaBaseModel):
    """会话启动事件。

    定义在本模块（而非独立文件），统一会话生命周期事件的归属。
    """

    type: Literal["session_start"] = "session_start"
    reason: Literal[
        "startup",
        "reload",
        "new",
        "resume",
        "fork",
        "clone",
        "import",
        # 角色切换触发的重放——恢复 handler 据此跳过 agent 条目恢复
        # （切换本身是来源，分支旧角色条目不能回切）
        "agent_change",
    ] = "startup"
    previous_session_file: Optional[str] = None


class SessionShutdownEvent(NovaBaseModel):
    type: Literal["session_shutdown"] = SESSION_SHUTDOWN
    reason: Literal["quit", "reload", "new", "resume", "fork"] = "quit"
    target_session_file: Optional[str] = None


class SessionBeforeSwitchEvent(NovaBaseModel):
    type: Literal["session_before_switch"] = SESSION_BEFORE_SWITCH
    reason: str = ""
    target_session_file: Optional[str] = None


class SessionBeforeForkEvent(NovaBaseModel):
    type: Literal["session_before_fork"] = SESSION_BEFORE_FORK
    entry_id: str = ""
    position: Optional[str] = None


class SessionBeforeCompactEvent(NovaBaseModel):
    preparation: CompactionPreparation
    type: Literal["session_before_compact"] = SESSION_BEFORE_COMPACT
    branch_entries: List[SessionEntry] = Field(default_factory=list)
    custom_instructions: Optional[str] = None


class SessionCompactEvent(NovaBaseModel):
    type: Literal["session_compact"] = SESSION_COMPACT
    compaction_entry: Any = None
    from_extension: bool = False


class CompactionStartEvent(NovaBaseModel):
    type: Literal["compaction_start"] = COMPACTION_START
    reason: Literal["manual", "threshold", "overflow"] = "manual"
    custom_instructions: Optional[str] = None


class CompactionEndEvent(NovaBaseModel):
    type: Literal["compaction_end"] = COMPACTION_END
    reason: Literal["manual", "threshold", "overflow"] = "manual"
    result: Any = None
    aborted: bool = False
    will_retry: bool = False
    error_message: Optional[str] = None


class TreePreparation(NovaBaseModel):
    target_id: str = ""
    old_leaf_id: Optional[str] = None
    common_ancestor_id: Optional[str] = None
    entries_to_summarize: List[SessionEntry] = Field(default_factory=list)
    user_wants_summary: bool = False
    custom_instructions: Optional[str] = None
    replace_instructions: bool = False
    label: Optional[str] = None


class SessionBeforeTreeEvent(NovaBaseModel):
    type: Literal["session_before_tree"] = SESSION_BEFORE_TREE
    preparation: TreePreparation = Field(default_factory=TreePreparation)


class SessionTreeEvent(NovaBaseModel):
    type: Literal["session_tree"] = SESSION_TREE
    new_leaf_id: Optional[str] = None
    old_leaf_id: Optional[str] = None
    summary_entry: Any = None
    from_extension: bool = False


class EntryAppendedEvent(NovaBaseModel):
    """自定义条目实时追加事件（扩展 append_entry 的 transcript 通道）。

    对齐 pi：仅 custom 条目发射——消息/压缩/标签等条目各有专属事件通道，
    前端只消费 custom（扩展自定义内容实时进 transcript，不必等会话重开）。
    """

    entry: Optional[Any] = None
    type: Literal["entry_appended"] = "entry_appended"


class CacheMissEvent(NovaBaseModel):
    """prompt 缓存显著 miss 提醒（pi addCacheMissNotice 对位）。

    message_end 时经 ``detect_cache_miss`` 检测发射（先持久化前检测——
    entries 尚未包含当前消息）。呈现层即时产物，不入会话历史；
    settings ``show_cache_miss_notices`` 门控（默认关闭）。
    """

    missed_tokens: int = 0
    missed_cost: float = 0.0
    idle_ms: float = 0.0
    model_changed: bool = False
    type: Literal["cache_miss"] = "cache_miss"
