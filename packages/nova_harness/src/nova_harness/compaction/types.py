"""
Type definitions for context compaction and branch summarization.
"""

from typing import Set, List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, field

from mashumaro.mixins.json import DataClassJSONMixin
from pi_agent import AgentMessage
from ..session import SessionEntry, CompactionDetails
# ============================================================================
# File Operation Tracking (from utils.py)
# ============================================================================

@dataclass
class FileOperations(DataClassJSONMixin):
    """Track file operations during agent execution."""
    read: Set[str] = field(default_factory=set)
    written: Set[str] = field(default_factory=set)
    edited: Set[str] = field(default_factory=set)


@dataclass
class ContentBlock:
    """Represents a content block in a message."""
    type: str
    text: str = ""
    thinking: str = ""
    name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Compaction Types (from compaction.py)
# ============================================================================

@dataclass
class CompactionResult(DataClassJSONMixin):
    """Result from compact() - SessionManager adds uuid/parent_uuid when saving."""
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    # Extension-specific data (e.g., ArtifactIndex, version markers for structured compaction)
    details: Optional[Any] = None


@dataclass
class CompactionSettings(DataClassJSONMixin):
    enabled: bool = True
    reserve_tokens: int = 16384
    keep_recent_tokens: int = 20000


@dataclass
class ContextUsageEstimate(DataClassJSONMixin):
    tokens: int
    usage_tokens: int
    trailing_tokens: int
    last_usage_index: Optional[int] = None


@dataclass
class CutPointResult(DataClassJSONMixin):
    """Index of first entry to keep."""
    first_kept_entry_index: int
    # Index of user message that starts the turn being split, or -1 if not splitting
    turn_start_index: int
    # Whether this cut splits a turn (cut point is not a user message)
    is_split_turn: bool


@dataclass
class CompactionPreparation(DataClassJSONMixin):
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
    file_ops: FileOperations = field(default_factory=lambda: FileOperations())
    # Compaction settings from settings.jsonl
    settings: CompactionSettings = field(default_factory=CompactionSettings)


# ============================================================================
# Branch Summarization Types (from branch_summarization.py)
# ============================================================================

@dataclass
class BranchSummaryResult(DataClassJSONMixin):
    summary: Optional[str] = None
    read_files: Optional[List[str]] = None
    modified_files: Optional[List[str]] = None
    aborted: bool = False
    error: Optional[str] = None


@dataclass
class BranchSummaryDetails(DataClassJSONMixin):
    """Details stored in BranchSummaryEntry.details for file tracking."""
    read_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)


@dataclass
class BranchPreparation(DataClassJSONMixin):
    """Messages extracted for summarization, in chronological order."""
    messages: List[AgentMessage] = field(default_factory=list)
    # File operations extracted from tool calls
    file_ops: FileOperations = field(default_factory=lambda: FileOperations())
    # Total estimated tokens in messages
    total_tokens: int = 0


@dataclass
class CollectEntriesResult(DataClassJSONMixin):
    """Entries to summarize, in chronological order."""
    entries: List[SessionEntry] = field(default_factory=list)
    # Common ancestor between old and new position, if any
    common_ancestor_id: Optional[str] = None


@dataclass
class GenerateBranchSummaryOptions(DataClassJSONMixin):
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