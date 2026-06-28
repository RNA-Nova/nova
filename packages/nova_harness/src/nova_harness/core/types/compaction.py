"""
压缩与分支摘要类型定义。

对应原 `nova_harness.compaction.types`，并作为 `CompactionDetails` 与
`CompactionSettings` 的唯一来源，消除与 `session`/`setting` 的重复定义。
"""

from typing import Any, Callable, Dict, List, Optional, Set

from nova_agent import AgentMessage
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

from nova_harness.core.types.session import SessionEntry


class FileOperations(NovaBaseModel):
    """Track file operations during agent execution."""

    read: Set[str] = Field(default_factory=set)
    written: Set[str] = Field(default_factory=set)
    edited: Set[str] = Field(default_factory=set)


class ContentBlock(NovaBaseModel):
    """Represents a content block in a message."""

    type: str
    text: str = ""
    thinking: str = ""
    name: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)


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


# ============================================================================
# Branch Summarization Types (from branch_summarization.py)
# ============================================================================


class BranchSummaryResult(NovaBaseModel):

    summary: Optional[str] = None
    read_files: Optional[List[str]] = None
    modified_files: Optional[List[str]] = None
    aborted: bool = False
    error: Optional[str] = None


class BranchSummaryDetails(NovaBaseModel):
    """Details stored in BranchSummaryEntry.details for file tracking."""

    read_files: List[str] = Field(default_factory=list)
    modified_files: List[str] = Field(default_factory=list)


class BranchPreparation(NovaBaseModel):
    """Messages extracted for summarization, in chronological order."""

    messages: List[AgentMessage] = Field(default_factory=list)
    # File operations extracted from tool calls
    file_ops: FileOperations = Field(default_factory=FileOperations)
    # Total estimated tokens in messages
    total_tokens: int = 0


class CollectEntriesResult(NovaBaseModel):
    """Entries to summarize, in chronological order."""

    entries: List[SessionEntry] = Field(default_factory=list)
    # Common ancestor between old and new position, if any
    common_ancestor_id: Optional[str] = None


class GenerateBranchSummaryOptions(NovaBaseModel):
    """Model to use for summarization."""

    model: Any  # Model type
    # API key for the model
    api_key: str
    # Abort signal for cancellation
    signal: Any
    # Optional custom instructions for summarization
    custom_instructions: Optional[str] = None
    # If true, custom_instructions replaces the default prompt instead of being appended
    replace_instructions: bool = False
    # Tokens reserved for prompt + LLM response (default 16384)
    reserve_tokens: int = 16384
    # Optional request headers for the model
    headers: Optional[Dict[str, str]] = None
    # Optional stream function (same shape as Agent.stream_fn / nova_ai.stream_simple).
    # If not provided, falls back to complete_simple.
    stream_fn: Optional[Callable[..., Any]] = Field(default=None, exclude=True)


__all__ = [
    "FileOperations",
    "ContentBlock",
    "CompactionDetails",
    "CompactionResult",
    "CompactionSettings",
    "ContextUsageEstimate",
    "CutPointResult",
    "CompactionPreparation",
    "BranchSummaryResult",
    "BranchSummaryDetails",
    "BranchPreparation",
    "CollectEntriesResult",
    "GenerateBranchSummaryOptions",
]
