"""分支摘要相关类型。"""

from typing import Any, Callable, Dict, List, Optional

from nova_agent import AgentMessage
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

from nova_harness.core.types.compaction.base import FileOperations
from nova_harness.core.types.session.entries import SessionEntry


class BranchSummaryResult(NovaBaseModel):

    summary: Optional[str] = None
    read_files: Optional[List[str]] = None
    modified_files: Optional[List[str]] = None
    aborted: bool = False
    error: Optional[str] = None


class BranchSummaryDetails(NovaBaseModel):
    """BranchSummaryEntry.details 中用于文件追踪的详情。"""

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
    "BranchSummaryResult",
    "BranchSummaryDetails",
    "BranchPreparation",
    "CollectEntriesResult",
    "GenerateBranchSummaryOptions",
]
