"""上下文压缩相关类型。"""

from typing import Any, List, Optional

from nova_agent import AgentMessage
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

from nova_harness.core.types.compaction.base import FileOperations


class CompactionDetails(NovaBaseModel):
    """Details stored in CompactionEntry.details for file tracking."""

    read_files: List[str] = Field(default_factory=list)
    modified_files: List[str] = Field(default_factory=list)


class CompactionResult(NovaBaseModel):
    """Result from compact() - SessionManager adds uuid/parent_uuid when saving."""

    summary: str
    first_kept_entry_id: str
    tokens_before: int
    # Extension-specific data (e.g., ArtifactIndex, version markers for structured compaction)
    details: Optional[Any] = None


class CompactionSettings(NovaBaseModel):
    """Compaction configuration settings."""

    enabled: bool = True
    reserve_tokens: int = 16384
    keep_recent_tokens: int = 20000


class ContextUsageEstimate(NovaBaseModel):

    tokens: int
    usage_tokens: int
    trailing_tokens: int
    last_usage_index: Optional[int] = None


class CutPointResult(NovaBaseModel):
    """Index of first entry to keep."""

    first_kept_entry_index: int
    # Index of user message that starts the turn being split, or -1 if not splitting
    turn_start_index: int
    # Whether this cut splits a turn (cut point is not a user message)
    is_split_turn: bool


class CompactionPreparation(NovaBaseModel):
    """UUID of first entry to keep."""

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
    file_ops: FileOperations = Field(default_factory=FileOperations)
    # Compaction settings from settings.jsonl
    settings: CompactionSettings = Field(default_factory=CompactionSettings)


__all__ = [
    "CompactionDetails",
    "CompactionResult",
    "CompactionSettings",
    "ContextUsageEstimate",
    "CutPointResult",
    "CompactionPreparation",
]
