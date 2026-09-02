"""
Branch summarization for tree navigation.

When navigating to a different point in the session tree, this generates
a summary of the branch being left so context isn't lost.
"""

import time
from typing import List, Optional

from nova_agent import AgentMessage
from nova_ai import Context, SimpleStreamOptions
from nova_harness.core.harness.compaction.compaction import (
    complete_summarization,
    estimate_tokens,
)
from nova_harness.core.harness.compaction.utils import (
    SUMMARIZATION_SYSTEM_PROMPT,
    compute_file_lists,
    create_file_ops,
    extract_file_ops_from_message,
    format_file_operations,
    get_message_from_entry,
    serialize_conversation,
)
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.types.compaction import (
    BranchPreparation,
    BranchSummaryResult,
    CollectEntriesResult,
    GenerateBranchSummaryOptions,
)
from nova_harness.core.types.session.entries import SessionEntry
from nova_harness.core.utils.messages import convert_to_llm

# ============================================================================
# Entry Collection
# ============================================================================


def collect_entries_for_branch_summary(
    session: SessionManager,
    old_leaf_id: Optional[str],
    target_id: str,
) -> CollectEntriesResult:
    """
    Collect entries that should be summarized when navigating from one position to another.

    Walks from old_leaf_id back to the common ancestor with target_id, collecting entries
    along the way. Does NOT stop at compaction boundaries - those are included and their
    summaries become context.

    Args:
        session: Session manager (read-only access)
        old_leaf_id: Current position (where we're navigating from)
        target_id: Target position (where we're navigating to)

    Returns:
        Entries to summarize and the common ancestor
    """
    # If no old position, nothing to summarize
    if not old_leaf_id:
        return CollectEntriesResult(entries=[], common_ancestor_id=None)

    # Find common ancestor (deepest node that's on both paths)
    old_path = set(e.id for e in session.get_branch(old_leaf_id))
    target_path = session.get_branch(target_id)

    # target_path is root-first, so iterate backwards to find deepest common ancestor
    common_ancestor_id: Optional[str] = None
    for i in range(len(target_path) - 1, -1, -1):
        if target_path[i].id in old_path:
            common_ancestor_id = target_path[i].id
            break

    # Collect entries from old leaf back to common ancestor
    entries: List[SessionEntry] = []
    current: Optional[str] = old_leaf_id

    while current and current != common_ancestor_id:
        entry = session.get_entry(current)
        if not entry:
            break
        entries.append(entry)
        current = entry.parent_id

    # Reverse to get chronological order
    entries.reverse()

    return CollectEntriesResult(entries=entries, common_ancestor_id=common_ancestor_id)


# ============================================================================
# Entry Collection
# ============================================================================


def prepare_branch_entries(
    entries: List[SessionEntry],
    token_budget: int = 0,
) -> BranchPreparation:
    """
    Prepare entries for summarization with token budget.

    Walks entries from NEWEST to OLDEST, adding messages until we hit the token budget.
    This ensures we keep the most recent context when the branch is too long.

    Also collects file operations from:
    - Tool calls in assistant messages
    - Existing branch_summary entries' details (for cumulative tracking)

    Args:
        entries: Entries in chronological order
        token_budget: Maximum tokens to include (0 = no limit)

    Returns:
        Branch preparation with messages, file operations, and total tokens
    """
    messages: List[AgentMessage] = []
    file_ops = create_file_ops()
    total_tokens = 0

    # First pass: collect file ops from ALL entries (even if they don't fit in token budget)
    # This ensures we capture cumulative file tracking from nested branch summaries
    # Only extract from pi-generated summaries (from_hook is not true), not extension-generated ones
    # details 在写入关口已归一化为 dict（见 SessionManager._normalize_details）
    for entry in entries:
        if entry.type == "branch_summary" and not entry.from_hook:
            details = entry.details
            if not isinstance(details, dict):
                continue
            read_files = details.get("read_files")
            if isinstance(read_files, list):
                for f in read_files:
                    file_ops.read.add(f)
            modified_files = details.get("modified_files")
            if isinstance(modified_files, list):
                # Modified files go into both edited and written for proper deduplication
                for f in modified_files:
                    file_ops.edited.add(f)

    # Second pass: walk from newest to oldest, adding messages until token budget
    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        message = get_message_from_entry(entry, skip_tool_results=True)
        if not message:
            continue

        # Extract file ops from assistant messages (tool calls)
        extract_file_ops_from_message(message, file_ops)

        tokens = estimate_tokens(message)

        # Check budget before adding
        if token_budget > 0 and total_tokens + tokens > token_budget:
            # If this is a summary entry, try to fit it anyway as it's important context
            if entry.type in ("compaction", "branch_summary"):
                if total_tokens < token_budget * 0.9:
                    messages.insert(0, message)
                    total_tokens += tokens
            # Stop - we've hit the budget
            break

        messages.insert(0, message)
        total_tokens += tokens

    return BranchPreparation(
        messages=messages, file_ops=file_ops, total_tokens=total_tokens
    )


# ============================================================================
# Summary Generation
# ============================================================================

_BRANCH_SUMMARY_PREAMBLE = """The user explored a different conversation branch before returning here.
Summary of that exploration:

"""

_BRANCH_SUMMARY_PROMPT = """Create a structured summary of this conversation branch for context when returning later.

Use this EXACT format:

## Goal
[What was the user trying to accomplish in this branch?]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [Work that was started but not finished]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [What should happen next to continue this work]

Keep each section concise. Preserve exact file paths, function names, and error messages."""


async def generate_branch_summary(
    entries: List[SessionEntry],
    options: GenerateBranchSummaryOptions,
) -> BranchSummaryResult:
    """
    Generate a summary of abandoned branch entries.

    Args:
        entries: Session entries to summarize (chronological order)
        options: Generation options

    Returns:
        Branch summary result
    """
    model = options.model
    api_key = options.api_key
    headers = options.headers
    env = options.env
    signal = options.signal
    custom_instructions = options.custom_instructions
    replace_instructions = options.replace_instructions
    reserve_tokens = options.reserve_tokens
    stream_fn = options.stream_fn

    # Token budget = context window minus reserved space for prompt + response
    context_window = model.context_window or 128000
    token_budget = context_window - reserve_tokens

    preparation = prepare_branch_entries(entries, token_budget)
    messages = preparation.messages
    file_ops = preparation.file_ops

    if not messages:
        return BranchSummaryResult(summary="No content to summarize")

    # Transform to LLM-compatible messages, then serialize to text
    # Serialization prevents the model from treating it as a conversation to continue
    llm_messages = convert_to_llm(messages)
    conversation_text = serialize_conversation(llm_messages)
    # Build prompt
    if replace_instructions and custom_instructions:
        instructions = custom_instructions
    elif custom_instructions:
        instructions = (
            f"{_BRANCH_SUMMARY_PROMPT}\n\nAdditional focus: {custom_instructions}"
        )
    else:
        instructions = _BRANCH_SUMMARY_PROMPT

    prompt_text = (
        f"<conversation>\n{conversation_text}\n</conversation>\n\n{instructions}"
    )

    summarization_messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": prompt_text}],
            "timestamp": int(time.time() * 1000),  # Date.now()
        }
    ]

    # Call LLM for summarization
    response = await complete_summarization(
        model,
        Context.model_validate(
            {
                "system_prompt": SUMMARIZATION_SYSTEM_PROMPT,
                "messages": summarization_messages,
            }
        ),
        SimpleStreamOptions(
            api_key=api_key,
            headers=headers,
            env=env,
            signal=signal,
            max_tokens=2048,
        ),
        stream_fn,
    )

    # Check if aborted or errored
    if response.stop_reason == "aborted":
        return BranchSummaryResult(aborted=True)

    if response.stop_reason == "error":
        return BranchSummaryResult(
            error=response.error_message or "Summarization failed"
        )

    summary = "\n".join([c.text for c in response.content if c.type == "text"])

    # Prepend preamble to provide context about the branch summary
    summary = _BRANCH_SUMMARY_PREAMBLE + summary

    # Compute file lists and append to summary
    read_files, modified_files = compute_file_lists(file_ops)
    summary += format_file_operations(read_files, modified_files)

    return BranchSummaryResult(
        summary=summary or "No summary generated",
        read_files=read_files,
        modified_files=modified_files,
    )
