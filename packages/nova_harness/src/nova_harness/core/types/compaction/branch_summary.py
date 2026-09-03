"""分支摘要相关类型。"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from nova_agent import AgentMessage
from nova_ai import AbortSignal, Model
from nova_ai.types.base_model import NovaBaseModel
from nova_harness.core.types.compaction.file_ops import FileOperations
from nova_harness.core.types.session.entries import SessionEntry
from pydantic import Field


@dataclass(frozen=True)
class BranchSummaryResult:
    """分支摘要的运行时结果（不进入会话 JSONL）。"""

    summary: Optional[str] = None
    read_files: Optional[List[str]] = None
    modified_files: Optional[List[str]] = None
    aborted: bool = False
    error: Optional[str] = None


class BranchSummaryDetails(NovaBaseModel):
    """BranchSummaryEntry.details 中用于文件追踪的详情。"""

    read_files: List[str] = Field(default_factory=list)
    modified_files: List[str] = Field(default_factory=list)


@dataclass(frozen=True)
class BranchPreparation:
    """Messages extracted for summarization, in chronological order."""

    messages: List[AgentMessage] = field(default_factory=list)
    # File operations extracted from tool calls
    file_ops: FileOperations = field(default_factory=FileOperations)
    # Total estimated tokens in messages
    total_tokens: int = 0


@dataclass(frozen=True)
class CollectEntriesResult:
    """Entries to summarize, in chronological order."""

    entries: List[SessionEntry] = field(default_factory=list)
    # Common ancestor between old and new position, if any
    common_ancestor_id: Optional[str] = None


@dataclass(frozen=True)
class GenerateBranchSummaryOptions:
    """生成分支摘要的运行时选项。

    纯代码构造的传参对象（持 Model 实例、AbortSignal、stream_fn），
    不跨 JSON 边界，因此用 dataclass 而非 Pydantic。
    """

    # Model to use for summarization
    model: Model
    # Abort signal for cancellation（必填，对齐 TS）
    signal: AbortSignal
    # API key for the model
    api_key: Optional[str] = None
    # Optional request headers for the model
    headers: Optional[Dict[str, str]] = None
    # Provider-scoped environment values for the model
    env: Optional[Dict[str, str]] = None
    # Optional custom instructions for summarization
    custom_instructions: Optional[str] = None
    # If true, custom_instructions replaces the default prompt instead of being appended
    replace_instructions: bool = False
    # Tokens reserved for prompt + LLM response (default 16384)
    reserve_tokens: int = 16384
    # Optional stream function (same shape as Agent.stream_fn / nova_ai.stream_simple).
    # If not provided, falls back to complete_simple.
    stream_fn: Optional[Callable] = None


__all__ = [
    "BranchSummaryResult",
    "BranchSummaryDetails",
    "BranchPreparation",
    "CollectEntriesResult",
    "GenerateBranchSummaryOptions",
]
