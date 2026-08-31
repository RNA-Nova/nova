"""上下文压缩相关类型。"""

from dataclasses import dataclass, field
from typing import Any, List, Optional

from nova_agent import AgentMessage
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

from nova_harness.core.types.compaction.file_ops import FileOperations


class CompactionDetails(NovaBaseModel):
    """Details stored in CompactionEntry.details for file tracking."""

    read_files: List[str] = Field(default_factory=list)
    modified_files: List[str] = Field(default_factory=list)


class CompactionResult(NovaBaseModel):
    """Result from compact() - SessionManager adds uuid/parent_uuid when saving."""

    summary: str
    first_kept_entry_id: str
    tokens_before: int
    # 压缩完成后新上下文的估算 token 数（对齐 TS estimatedTokensAfter）
    estimated_tokens_after: Optional[int] = None
    # Extension-specific data (e.g., ArtifactIndex, version markers for structured compaction)
    details: Optional[Any] = None


class CompactionSettings(NovaBaseModel):
    """Compaction configuration settings."""

    enabled: bool = True
    reserve_tokens: int = 16384
    keep_recent_tokens: int = 20000


@dataclass(frozen=True)
class ContextUsageEstimate:
    """上下文用量估算（运行时计算中间结果，不跨 JSON 边界）。"""

    tokens: int
    usage_tokens: int
    trailing_tokens: int
    last_usage_index: Optional[int] = None


@dataclass(frozen=True)
class CutPointResult:
    """Index of first entry to keep."""

    first_kept_entry_index: int
    # Index of user message that starts the turn being split, or -1 if not splitting
    turn_start_index: int
    # Whether this cut splits a turn (cut point is not a user message)
    is_split_turn: bool


@dataclass(frozen=True)
class CompactionPreparation:
    """UUID of first entry to keep（压缩准备的运行时中间态）。"""

    first_kept_entry_id: str
    # Messages that will be summarized and discarded
    messages_to_summarize: List[AgentMessage]
    # Messages that will be turned into turn prefix summary (if splitting)
    turn_prefix_messages: List[AgentMessage]
    # Whether this is a split turn (cut point in middle of turn)
    is_split_turn: bool
    tokens_before: int
    # Summary from previous compaction, for iterative update
    previous_summary: Optional[str] = None
    # File operations extracted from messages_to_summarize
    file_ops: FileOperations = field(default_factory=FileOperations)
    # Compaction settings from settings.jsonl
    settings: CompactionSettings = field(default_factory=CompactionSettings)


__all__ = [
    "CompactionDetails",
    "CompactionResult",
    "CompactionSettings",
    "ContextUsageEstimate",
    "CutPointResult",
    "CompactionPreparation",
]
