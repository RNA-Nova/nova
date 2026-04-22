# ============================================================================
# 事件类型定义
# ============================================================================
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional, Union, Awaitable


from pi_agent import AgentEvent

from ..compaction import (
    CompactionResult
)
class AutoCompactionReason(Enum):
    """自动压缩触发原因"""
    THRESHOLD = auto()
    OVERFLOW = auto()


@dataclass(frozen=True)
class AutoCompactionStartEvent:
    event_type: str = field(default="auto_compaction_start", init=False)
    reason: AutoCompactionReason


@dataclass(frozen=True)
class AutoCompactionEndEvent:
    event_type: str = field(default="auto_compaction_end", init=False)
    result: Optional['CompactionResult']
    aborted: bool
    will_retry: bool
    error_message: Optional[str] = None


@dataclass(frozen=True)
class AutoRetryStartEvent:
    event_type: str = field(default="auto_retry_start", init=False)
    attempt: int
    max_attempts: int
    delay_ms: int
    error_message: str


@dataclass(frozen=True)
class AutoRetryEndEvent:
    event_type: str = field(default="auto_retry_end", init=False)
    success: bool
    attempt: int
    final_error: Optional[str] = None


AgentSessionEvent = Union[
    AgentEvent,
    AutoCompactionStartEvent,
    AutoCompactionEndEvent,
    AutoRetryStartEvent,
    AutoRetryEndEvent,
]

AgentSessionEventListener = Union[
    Callable[[AgentSessionEvent], None],
    Callable[[AgentSessionEvent], Awaitable[None]],
]