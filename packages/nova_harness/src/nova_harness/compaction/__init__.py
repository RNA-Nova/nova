"""
Context compaction and branch summarization utilities.

This package provides tools for:
- Session context compaction for long conversations
- Branch summarization for tree navigation
- File operation tracking and serialization
"""

from .types import (
    # Utils exports
    FileOperations,
    ContentBlock,
    # Compaction exports
    CompactionDetails,
    CompactionResult,
    CompactionSettings,
    ContextUsageEstimate,
    CutPointResult,
    CompactionPreparation,
    # Branch summarization exports
    BranchSummaryResult,
    BranchSummaryDetails,
    BranchPreparation,
    CollectEntriesResult,
    GenerateBranchSummaryOptions,
)

from .utils import (
    create_file_ops,
    extract_file_ops_from_message,
    compute_file_lists,
    format_file_operations,
    serialize_conversation,
    SUMMARIZATION_SYSTEM_PROMPT,
)

from .compaction import (
    calculate_context_tokens,
    estimate_context_tokens,
    should_compact,
    estimate_tokens,
    find_cut_point,
    find_turn_start_index,
    generate_summary,
    prepare_compaction,
    compact,
    get_last_assistant_usage,
)

from .branch_summarization import (
    collect_entries_for_branch_summary,
    prepare_branch_entries,
    generate_branch_summary,
)

__all__ = [
    # Utils exports
    "FileOperations",
    "create_file_ops",
    "extract_file_ops_from_message",
    "compute_file_lists",
    "format_file_operations",
    "serialize_conversation",
    "SUMMARIZATION_SYSTEM_PROMPT",
    "ContentBlock",
    
    # Compaction exports
    "CompactionResult",
    "CompactionDetails",
    "CompactionSettings",
    "CompactionPreparation",
    "CutPointResult",
    "ContextUsageEstimate",
    "calculate_context_tokens",
    "estimate_context_tokens",
    "should_compact",
    "estimate_tokens",
    "find_cut_point",
    "find_turn_start_index",
    "generate_summary",
    "prepare_compaction",
    "compact",
    "get_last_assistant_usage",
    
    # Branch summarization exports
    "BranchSummaryResult",
    "BranchSummaryDetails",
    "BranchPreparation",
    "CollectEntriesResult",
    "GenerateBranchSummaryOptions",
    "collect_entries_for_branch_summary",
    "prepare_branch_entries",
    "generate_branch_summary",
]