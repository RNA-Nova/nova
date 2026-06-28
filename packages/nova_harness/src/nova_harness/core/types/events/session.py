"""会话生命周期事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional

from nova_harness.core.types.compaction import CompactionPreparation
from nova_harness.core.types.session import SessionEntry

from .constants import (
    COMPACTION_END,
    COMPACTION_START,
    SESSION_BEFORE_COMPACT,
    SESSION_BEFORE_FORK,
    SESSION_BEFORE_SWITCH,
    SESSION_BEFORE_TREE,
    SESSION_COMPACT,
    SESSION_SHUTDOWN,
    SESSION_START,
    SESSION_TREE,
)


@dataclass
class SessionStartEvent:
    type: Literal["session_start"] = SESSION_START
    reason: Literal["new", "reload", "switch", "fork"] = "new"
    previous_session_file: Optional[str] = None


@dataclass
class SessionShutdownEvent:
    type: Literal["session_shutdown"] = SESSION_SHUTDOWN
    reason: Literal["dispose", "reload", "switch"] = "dispose"
    target_session_file: Optional[str] = None


@dataclass
class SessionBeforeSwitchEvent:
    type: Literal["session_before_switch"] = SESSION_BEFORE_SWITCH
    reason: str = ""
    target_session_file: Optional[str] = None


@dataclass
class SessionBeforeForkEvent:
    type: Literal["session_before_fork"] = SESSION_BEFORE_FORK
    entry_id: str = ""
    position: Optional[str] = None


@dataclass
class SessionBeforeCompactEvent:
    type: Literal["session_before_compact"] = SESSION_BEFORE_COMPACT
    preparation: CompactionPreparation = field(
        default_factory=lambda: CompactionPreparation(
            first_kept_entry_id="",
            messages_to_summarize=[],
            turn_prefix_messages=[],
            is_split_turn=False,
            tokens_before=0,
        )
    )
    branch_entries: List[SessionEntry] = field(default_factory=list)
    custom_instructions: Optional[str] = None
    signal: Any = None


@dataclass
class SessionCompactEvent:
    type: Literal["session_compact"] = SESSION_COMPACT
    compaction_entry: Any = None
    from_extension: bool = False


@dataclass
class CompactionStartEvent:
    type: Literal["compaction_start"] = COMPACTION_START
    reason: Literal["manual", "threshold", "overflow"] = "manual"
    custom_instructions: Optional[str] = None


@dataclass
class CompactionEndEvent:
    type: Literal["compaction_end"] = COMPACTION_END
    reason: Literal["manual", "threshold", "overflow"] = "manual"
    result: Any = None
    aborted: bool = False
    will_retry: bool = False
    error_message: Optional[str] = None


@dataclass
class TreePreparation:
    target_id: str = ""
    old_leaf_id: Optional[str] = None
    common_ancestor_id: Optional[str] = None
    entries_to_summarize: List[SessionEntry] = field(default_factory=list)
    user_wants_summary: bool = False
    custom_instructions: Optional[str] = None
    replace_instructions: bool = False
    label: Optional[str] = None


@dataclass
class SessionBeforeTreeEvent:
    type: Literal["session_before_tree"] = SESSION_BEFORE_TREE
    preparation: TreePreparation = field(default_factory=TreePreparation)
    signal: Any = None


@dataclass
class SessionTreeEvent:
    type: Literal["session_tree"] = SESSION_TREE
    new_leaf_id: Optional[str] = None
    old_leaf_id: Optional[str] = None
    summary_entry: Any = None
    from_extension: bool = False
