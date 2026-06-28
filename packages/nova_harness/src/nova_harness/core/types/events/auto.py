"""AgentSession 自动触发的内部事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional

from nova_ai import ThinkingLevel

from nova_harness.core.types.compaction import CompactionResult

from .constants import (
    AUTO_COMPACTION_END,
    AUTO_COMPACTION_START,
    AUTO_RETRY_END,
    AUTO_RETRY_START,
    QUEUE_UPDATE,
    SESSION_INFO_CHANGED,
    THINKING_LEVEL_CHANGED,
)


class AutoCompactionReason(Enum):
    """自动压缩触发原因"""

    THRESHOLD = auto()
    OVERFLOW = auto()


@dataclass
class AutoCompactionStartEvent:
    event_type: str = AUTO_COMPACTION_START
    reason: AutoCompactionReason = field(
        default_factory=lambda: AutoCompactionReason.THRESHOLD
    )


@dataclass
class AutoCompactionEndEvent:
    result: Optional[CompactionResult] = None
    aborted: bool = False
    will_retry: bool = False
    error_message: Optional[str] = None
    event_type: str = AUTO_COMPACTION_END


@dataclass
class AutoRetryStartEvent:
    attempt: int = 0
    max_attempts: int = 0
    delay_ms: int = 0
    error_message: str = ""
    event_type: str = AUTO_RETRY_START


@dataclass
class AutoRetryEndEvent:
    success: bool = False
    attempt: int = 0
    final_error: Optional[str] = None
    event_type: str = AUTO_RETRY_END


@dataclass
class QueueUpdateEvent:
    event_type: str = QUEUE_UPDATE
    steering: List[str] = field(default_factory=list)
    follow_up: List[str] = field(default_factory=list)


@dataclass
class SessionInfoChangedEvent:
    event_type: str = SESSION_INFO_CHANGED
    name: Optional[str] = None


@dataclass
class ThinkingLevelChangedEvent:
    event_type: str = THINKING_LEVEL_CHANGED
    level: Optional[ThinkingLevel] = None
