"""
Context compaction for long sessions.

Pure functions for compaction logic. The session manager handles I/O,
and after compaction the session is reloaded.
"""

import asyncio
import inspect
import time
from typing import Any, Dict, List, Optional, Tuple

from nova_agent import AgentMessage, StreamFn
from nova_ai import (
    AssistantMessage,
    AssistantMessageEventStream,
    Context,
    Model,
    SimpleStreamOptions,
    ThinkingLevel,
    Usage,
    complete_simple,
)

from nova_harness.core.harness.compaction.utils import (
    SUMMARIZATION_SYSTEM_PROMPT,
    compute_file_lists,
    create_file_ops,
    extract_file_ops_from_message,
    format_file_operations,
    get_detail_value,
    serialize_conversation,
)
from nova_harness.core.harness.session import build_session_context
from nova_harness.core.types.compaction import (
    CompactionDetails,
    CompactionPreparation,
    CompactionResult,
    CompactionSettings,
    ContextUsageEstimate,
    CutPointResult,
    FileOperations,
)
from nova_harness.core.types.session import CompactionEntry, SessionEntry
from nova_harness.core.utils.messages import (
    convert_to_llm,
    create_branch_summary_message,
    create_compaction_summary_message,
    create_custom_message,
)

# ============================================================================
# File Operation Tracking
# ============================================================================


def _extract_file_operations(
    messages: List[AgentMessage],
    entries: List[SessionEntry],
    prev_compaction_index: int,
) -> FileOperations:
    """
    Extract file operations from messages and previous compaction entries.
    """
    file_ops = create_file_ops()

    # Collect from previous compaction's details (if pi-generated)
    if prev_compaction_index >= 0:
        prev_compaction = entries[prev_compaction_index]
        if isinstance(prev_compaction, CompactionEntry):
            if not prev_compaction.from_hook and prev_compaction.details:
                # from_hook field kept for session file compatibility
                details = prev_compaction.details
                read_files = get_detail_value(details, "read_files")
                if isinstance(read_files, list):
                    for f in read_files:
                        file_ops.read.add(f)
                modified_files = get_detail_value(details, "modified_files")
                if isinstance(modified_files, list):
                    for f in modified_files:
                        file_ops.edited.add(f)

    # Extract from tool calls in messages
    for msg in messages:
        extract_file_ops_from_message(msg, file_ops)

    return file_ops


# ============================================================================
# Message Extraction
# ============================================================================


def _get_message_from_entry(entry: SessionEntry) -> Optional[AgentMessage]:
    """
    Extract AgentMessage from an entry if it produces one.
    Returns None for entries that don't contribute to LLM context.
    """
    if entry.type == "message":
        return entry.message
    if entry.type == "custom_message":
        return create_custom_message(
            entry.custom_type,
            entry.content,
            entry.display,
            entry.details,
            entry.timestamp,
        )
    if entry.type == "branch_summary":
        return create_branch_summary_message(
            entry.summary,
            entry.from_id,
            entry.timestamp,
        )
    if entry.type == "compaction":
        return create_compaction_summary_message(
            entry.summary,
            entry.tokens_before,
            entry.timestamp,
        )
    return None


def _get_message_from_entry_for_compaction(
    entry: SessionEntry,
) -> Optional[AgentMessage]:
    """
    Extract AgentMessage for compaction summarization.
    Skips compaction entries themselves since they are already represented by previous_summary.
    """
    if entry.type == "compaction":
        return None
    return _get_message_from_entry(entry)


# ============================================================================
# Types
# ============================================================================

DEFAULT_COMPACTION_SETTINGS = CompactionSettings()


# ============================================================================
# Token calculation
# ============================================================================


def calculate_context_tokens(usage: Usage) -> int:
    """
    Calculate total context tokens from usage.
    Uses the native total_tokens field when available, falls back to computing from components.
    """
    return (
        usage.total_tokens
        or usage.input + usage.output + usage.cache_read + usage.cache_write
    )


def _get_assistant_usage(msg: AgentMessage) -> Optional[Usage]:
    """
    Get usage from an assistant message if available.
    Skips aborted and error messages as they don't have valid usage data.
    """
    if msg.role == "assistant" and hasattr(msg, "usage"):
        assistant_msg = msg  # type: AssistantMessage
        if (
            assistant_msg.stop_reason != "aborted"
            and assistant_msg.stop_reason != "error"
            and assistant_msg.usage
        ):
            return assistant_msg.usage
    return None


def get_last_assistant_usage(entries: List[SessionEntry]) -> Optional[Usage]:
    """
    Find the last non-aborted assistant message usage from session entries.
    """
    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        if entry.type == "message":
            usage = _get_assistant_usage(entry.message)
            if usage:
                return usage
    return None


def _get_last_assistant_usage_info(
    messages: List[AgentMessage],
) -> Optional[Tuple[Usage, int]]:
    for i in range(len(messages) - 1, -1, -1):
        usage = _get_assistant_usage(messages[i])
        if usage:
            return usage, i
    return None


def estimate_context_tokens(messages: List[AgentMessage]) -> ContextUsageEstimate:
    """
    Estimate context tokens from messages, using the last assistant usage when available.
    If there are messages after the last usage, estimate their tokens with estimate_tokens.
    """
    usage_info = _get_last_assistant_usage_info(messages)

    if not usage_info:
        estimated = 0
        for message in messages:
            estimated += estimate_tokens(message)
        return ContextUsageEstimate(
            tokens=estimated,
            usage_tokens=0,
            trailing_tokens=estimated,
            last_usage_index=None,
        )

    usage, index = usage_info
    usage_tokens = calculate_context_tokens(usage)
    trailing_tokens = 0
    for i in range(index + 1, len(messages)):
        trailing_tokens += estimate_tokens(messages[i])

    return ContextUsageEstimate(
        tokens=usage_tokens + trailing_tokens,
        usage_tokens=usage_tokens,
        trailing_tokens=trailing_tokens,
        last_usage_index=index,
    )


def should_compact(
    context_tokens: int, context_window: int, settings: CompactionSettings
) -> bool:
    """
    Check if compaction should trigger based on context usage.
    """
    if not settings.enabled:
        return False
    return context_tokens > context_window - settings.reserve_tokens


# ============================================================================
# Cut point detection
# ============================================================================


def estimate_tokens(message: AgentMessage) -> int:
    """
    Estimate token count for a message using chars/4 heuristic.
    This is conservative (overestimates tokens).
    """
    chars = 0

    if message.role == "user":
        content = message.content
        if isinstance(content, str):
            chars = len(content)
        elif isinstance(content, list):
            for block in content:
                if block.type == "text" and block.text:
                    chars += len(block.text)
                elif block.type == "image":
                    chars += 4800
        return (chars + 3) // 4

    elif message.role == "assistant":
        for block in message.content:
            block_type = block.type
            if block_type == "text":
                chars += len(block.text)
            elif block_type == "thinking":
                chars += len(block.thinking)
            elif block_type == "toolCall":
                chars += len(block.name)
                args = block.arguments
                chars += len(str(args))
        return (chars + 3) // 4

    elif message.role in ("custom", "interAgent", "frontend", "toolResult"):
        if isinstance(message.content, str):
            chars = len(message.content)
        else:
            for block in message.content:
                if block.type == "text" and block.text:
                    chars += len(block.text)
                if block.type == "image":
                    chars += 4800  # Estimate images as 4000 chars, or 1200 tokens
        return (chars + 3) // 4

    elif message.role == "bashExecution":
        chars = len(message.command) + len(message.output)
        return (chars + 3) // 4

    elif message.role in ("branchSummary", "compactionSummary"):
        chars = len(message.summary)
        return (chars + 3) // 4

    return 0


def _find_valid_cut_points(
    entries: List[SessionEntry], start_index: int, end_index: int
) -> List[int]:
    """
    Find valid cut points: indices of user, assistant, custom, or bashExecution messages.
    Never cut at tool results (they must follow their tool call).
    When we cut at an assistant message with tool calls, its tool results follow it
    and will be kept.
    BashExecutionMessage is treated like a user message (user-initiated context).
    """
    cut_points: List[int] = []
    for i in range(start_index, end_index):
        entry = entries[i]
        if entry.type == "message":
            role = entry.message.role
            if role in (
                "bashExecution",
                "custom",
                "branchSummary",
                "compactionSummary",
                "user",
                "assistant",
            ):
                cut_points.append(i)
        # branch_summary and custom_message are user-role messages, valid cut points
        if entry.type in (
            "branch_summary",
            "custom_message",
        ):
            cut_points.append(i)
    return cut_points


def find_turn_start_index(
    entries: List[SessionEntry], entry_index: int, start_index: int
) -> int:
    """
    Find the user message (or bashExecution) that starts the turn containing the given entry index.
    Returns -1 if no turn start found before the index.
    BashExecutionMessage is treated like a user message for turn boundaries.
    """
    for i in range(entry_index, start_index - 1, -1):
        entry = entries[i]
        # branch_summary and custom_message are user-role messages, can start a turn
        if entry.type in (
            "branch_summary",
            "custom_message",
        ):
            return i
        if entry.type == "message":
            role = entry.message.role
            if role in ("user", "bashExecution"):
                return i
    return -1


def find_cut_point(
    entries: List[SessionEntry],
    start_index: int,
    end_index: int,
    keep_recent_tokens: int,
) -> CutPointResult:
    """
    Find the cut point in session entries that keeps approximately `keep_recent_tokens`.

    Algorithm: Walk backwards from newest, accumulating estimated message sizes.
    Stop when we've accumulated >= keep_recent_tokens. Cut at that point.

    Can cut at user OR assistant messages (never tool results). When cutting at an
    assistant message with tool calls, its tool results come after and will be kept.

    Returns CutPointResult with:
    - first_kept_entry_index: the entry index to start keeping from
    - turn_start_index: if cutting mid-turn, the user message that started that turn
    - is_split_turn: whether we're cutting in the middle of a turn

    Only considers entries between `start_index` and `end_index` (exclusive).
    """
    cut_points = _find_valid_cut_points(entries, start_index, end_index)
    if not cut_points:
        return CutPointResult(
            first_kept_entry_index=start_index,
            turn_start_index=-1,
            is_split_turn=False,
        )

    # Walk backwards from newest, accumulating estimated message sizes
    accumulated_tokens = 0
    cut_index = cut_points[0]  # Default: keep from first message (not header)

    for i in range(end_index - 1, start_index - 1, -1):
        entry = entries[i]
        if entry.type != "message":
            continue

        # Estimate this message's size
        message_tokens = estimate_tokens(entry.message)
        accumulated_tokens += message_tokens
        # Check if we've exceeded the budget
        if accumulated_tokens >= keep_recent_tokens:
            # Find the closest valid cut point at or after this entry
            for c in range(len(cut_points)):
                cut_index = cut_points[c]
                if cut_points[c] >= i:
                    break
            break
    # Scan backwards from cut_index to include any non-message entries (bash, settings, etc.)
    while cut_index > start_index:
        prev_entry = entries[cut_index - 1]
        # Stop at session header or compaction boundaries
        if prev_entry.type == "compaction":
            break
        if prev_entry.type == "message":
            # Stop if we hit any message
            break
        # Include this non-message entry (bash, settings change, etc.)
        cut_index -= 1

    # Determine if this is a split turn
    cut_entry = entries[cut_index]
    is_user_message = cut_entry.type == "message" and cut_entry.message.role == "user"
    turn_start_index = (
        -1
        if is_user_message
        else find_turn_start_index(entries, cut_index, start_index)
    )

    return CutPointResult(
        first_kept_entry_index=cut_index,
        turn_start_index=turn_start_index,
        is_split_turn=not is_user_message and turn_start_index != -1,
    )


# ============================================================================
# Summarization
# ============================================================================

_SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [✓] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

_UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation messages to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use this EXACT format:

## Goal
[Preserve existing goals, add new ones if the task expanded]

## Constraints & Preferences
- [Preserve existing, add new ones discovered]

## Progress
### Done
- [✓] [Include previously done items AND newly completed items]

### In Progress
- [ ] [Current work - update based on progress]

### Blocked
- [Current blockers - remove if resolved]

## Key Decisions
- **[Decision]**: [Brief rationale] (preserve all previous, add new)

## Next Steps
1. [Update based on current state]

## Critical Context
- [Preserve important context, add new if needed]

Keep each section concise. Preserve exact file paths, function names, and error messages."""


async def _complete_summarization(
    model: Model,
    context: Context,
    options: SimpleStreamOptions,
    stream_fn: Optional[StreamFn] = None,
) -> AssistantMessage:
    """
    完成摘要生成请求。
    如果提供了 stream_fn（与 Agent.stream_fn 同形），则使用它来获取流并等待结果；
    否则回退到 complete_simple。
    """
    if stream_fn is None:
        return await complete_simple(model, context, options)

    stream_result = stream_fn(model, context, options)
    if inspect.isawaitable(stream_result):
        stream = await stream_result
    else:
        stream = stream_result

    if not isinstance(stream, AssistantMessageEventStream):
        raise TypeError(
            f"stream_fn must return an AssistantMessageEventStream, got {type(stream)}"
        )

    return await stream.result()


async def generate_summary(
    current_messages: List[AgentMessage],
    model: Model,
    reserve_tokens: int,
    api_key: str,
    signal: Optional[Any] = None,
    custom_instructions: Optional[str] = None,
    previous_summary: Optional[str] = None,
    thinking_level: Optional[ThinkingLevel] = None,
    headers: Optional[Dict[str, str]] = None,
    stream_fn: Optional[StreamFn] = None,
) -> str:
    """
    Generate a summary of the conversation using the LLM.
    If previous_summary is provided, uses the update prompt to merge.
    """
    max_tokens = int(0.8 * reserve_tokens)
    if model.max_tokens and model.max_tokens > 0:
        max_tokens = min(max_tokens, model.max_tokens)

    # Use update prompt if we have a previous summary, otherwise initial prompt
    base_prompt = (
        _UPDATE_SUMMARIZATION_PROMPT if previous_summary else _SUMMARIZATION_PROMPT
    )
    if custom_instructions:
        base_prompt = f"{base_prompt}\n\nAdditional focus: {custom_instructions}"

    # Serialize conversation to text so model doesn't try to continue it
    # Convert to LLM messages first (handles custom types like bashExecution, custom, etc.)
    llm_messages = await convert_to_llm(current_messages)
    conversation_text = serialize_conversation(llm_messages)

    # Build the prompt with conversation wrapped in tags
    prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n"
    if previous_summary:
        prompt_text += (
            f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
        )
    prompt_text += base_prompt

    summarization_messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": prompt_text}],
            "timestamp": int(time.time() * 1000),  # Date.now()
        }
    ]

    stream_options: Dict[str, Any] = {
        "max_tokens": max_tokens,
        "signal": signal,
        "api_key": api_key,
    }
    if headers:
        stream_options["headers"] = headers
    if model.reasoning and thinking_level is not None:
        stream_options["reasoning"] = thinking_level

    response = await _complete_summarization(
        model,
        Context.model_validate(
            {
                "system_prompt": SUMMARIZATION_SYSTEM_PROMPT,
                "messages": summarization_messages,
            }
        ),
        SimpleStreamOptions.model_validate(stream_options),
        stream_fn,
    )

    if response.stop_reason == "error":
        raise Exception(
            f"Summarization failed: {response.error_message or 'Unknown error'}"
        )

    text_content = "\n".join([c.text for c in response.content if c.type == "text"])

    return text_content


# ============================================================================
# Compaction Preparation (for extensions)
# ============================================================================


def prepare_compaction(
    path_entries: List[SessionEntry],
    settings: CompactionSettings,
) -> Optional[CompactionPreparation]:
    if not path_entries:
        return None
    if path_entries[-1].type == "compaction":
        return None

    prev_compaction_index = -1
    previous_summary: Optional[str] = None
    for i in range(len(path_entries) - 1, -1, -1):
        if path_entries[i].type == "compaction":
            prev_compaction_index = i
            break

    boundary_start = 0
    if prev_compaction_index >= 0:
        prev_compaction = path_entries[prev_compaction_index]
        if isinstance(prev_compaction, CompactionEntry):
            previous_summary = prev_compaction.summary
            first_kept_index = next(
                (
                    i
                    for i, entry in enumerate(path_entries)
                    if entry.id == prev_compaction.first_kept_entry_id
                ),
                -1,
            )
            boundary_start = (
                first_kept_index if first_kept_index >= 0 else prev_compaction_index + 1
            )
    boundary_end = len(path_entries)

    # Estimate current full LLM context size, matching TS buildSessionContext().messages
    tokens_before = estimate_context_tokens(
        build_session_context(path_entries).messages
    ).tokens

    cut_point = find_cut_point(
        path_entries, boundary_start, boundary_end, settings.keep_recent_tokens
    )
    # Get UUID of first kept entry
    first_kept_entry = path_entries[cut_point.first_kept_entry_index]
    if not first_kept_entry or not hasattr(first_kept_entry, "id"):
        return None  # Session needs migration
    first_kept_entry_id = first_kept_entry.id

    history_end = (
        cut_point.turn_start_index
        if cut_point.is_split_turn
        else cut_point.first_kept_entry_index
    )

    # Messages to summarize (will be discarded after summary)
    messages_to_summarize: List[AgentMessage] = []
    for i in range(boundary_start, history_end):
        msg = _get_message_from_entry_for_compaction(path_entries[i])
        if msg:
            messages_to_summarize.append(msg)

    # Messages for turn prefix summary (if splitting a turn)
    turn_prefix_messages: List[AgentMessage] = []
    if cut_point.is_split_turn:
        for i in range(cut_point.turn_start_index, cut_point.first_kept_entry_index):
            msg = _get_message_from_entry_for_compaction(path_entries[i])
            if msg:
                turn_prefix_messages.append(msg)

    # Extract file operations from messages and previous compaction
    file_ops = _extract_file_operations(
        messages_to_summarize, path_entries, prev_compaction_index
    )

    # Also extract file ops from turn prefix if splitting
    if cut_point.is_split_turn:
        for msg in turn_prefix_messages:
            extract_file_ops_from_message(msg, file_ops)

    return CompactionPreparation(
        first_kept_entry_id=first_kept_entry_id,
        messages_to_summarize=messages_to_summarize,
        turn_prefix_messages=turn_prefix_messages,
        is_split_turn=cut_point.is_split_turn,
        tokens_before=tokens_before,
        previous_summary=previous_summary,
        file_ops=file_ops,
        settings=settings,
    )


# ============================================================================
# Main compaction function
# ============================================================================

_TURN_PREFIX_SUMMARIZATION_PROMPT = """This is the PREFIX of a turn that was too large to keep. The SUFFIX (recent work) is retained.

Summarize the prefix to provide context for the retained suffix:

## Original Request
[What did the user ask for in this turn?]

## Early Progress
- [Key decisions and work done in the prefix]

## Context for Suffix
- [Information needed to understand the retained recent work]

Be concise. Focus on what's needed to understand the kept suffix."""


async def compact(
    preparation: CompactionPreparation,
    model: Model,
    api_key: str,
    custom_instructions: Optional[str] = None,
    signal: Optional[Any] = None,
    thinking_level: Optional[ThinkingLevel] = None,
    headers: Optional[Dict[str, str]] = None,
    stream_fn: Optional[StreamFn] = None,
) -> CompactionResult:
    """
    Generate summaries for compaction using prepared data.
    Returns CompactionResult - SessionManager adds uuid/parent_uuid when saving.

    Args:
        preparation: Pre-calculated preparation from prepare_compaction()
        custom_instructions: Optional custom focus for the summary
    """
    first_kept_entry_id = preparation.first_kept_entry_id
    messages_to_summarize = preparation.messages_to_summarize
    turn_prefix_messages = preparation.turn_prefix_messages
    is_split_turn = preparation.is_split_turn
    tokens_before = preparation.tokens_before
    previous_summary = preparation.previous_summary
    file_ops = preparation.file_ops
    settings = preparation.settings

    # Generate summaries (can be parallel if both needed) and merge into one
    summary: str

    if is_split_turn and turn_prefix_messages:
        # Generate both summaries in parallel
        if messages_to_summarize:
            history_promise = generate_summary(
                messages_to_summarize,
                model,
                settings.reserve_tokens,
                api_key,
                signal,
                custom_instructions,
                previous_summary,
                thinking_level,
                headers,
                stream_fn,
            )
        else:
            history_promise = "No prior history."

        turn_prefix_promise = _generate_turn_prefix_summary(
            turn_prefix_messages,
            model,
            settings.reserve_tokens,
            api_key,
            signal,
            thinking_level,
            headers,
            stream_fn,
        )

        if isinstance(history_promise, str):
            history_result = history_promise
            turn_prefix_result = await turn_prefix_promise
        else:
            history_result, turn_prefix_result = await asyncio.gather(
                history_promise, turn_prefix_promise
            )

        # Merge into single summary
        summary = f"{history_result}\n\n---\n\n**Turn Context (split turn):**\n\n{turn_prefix_result}"
    else:
        # Just generate history summary
        summary = await generate_summary(
            messages_to_summarize,
            model,
            settings.reserve_tokens,
            api_key,
            signal,
            custom_instructions,
            previous_summary,
            thinking_level,
            headers,
            stream_fn,
        )

    # Compute file lists and append to summary
    read_files, modified_files = compute_file_lists(file_ops)
    summary += format_file_operations(read_files, modified_files)

    if not first_kept_entry_id:
        raise Exception("First kept entry has no UUID - session may need migration")

    return CompactionResult(
        summary=summary,
        first_kept_entry_id=first_kept_entry_id,
        tokens_before=tokens_before,
        details=CompactionDetails(
            read_files=list(read_files), modified_files=(modified_files)
        ),
    )


async def _generate_turn_prefix_summary(
    messages: List[AgentMessage],
    model: Model,
    reserve_tokens: int,
    api_key: str,
    signal: Optional[Any] = None,
    thinking_level: Optional[ThinkingLevel] = None,
    headers: Optional[Dict[str, str]] = None,
    stream_fn: Optional[StreamFn] = None,
) -> str:
    """
    Generate a summary for a turn prefix (when splitting a turn).
    """
    max_tokens = int(0.5 * reserve_tokens)
    if model.max_tokens and model.max_tokens > 0:
        max_tokens = min(max_tokens, model.max_tokens)

    llm_messages = await convert_to_llm(messages)
    conversation_text = serialize_conversation(llm_messages)
    prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n{_TURN_PREFIX_SUMMARIZATION_PROMPT}"

    summarization_messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": prompt_text}],
            "timestamp": int(time.time() * 1000),
        }
    ]

    stream_options: Dict[str, Any] = {
        "max_tokens": max_tokens,
        "signal": signal,
        "api_key": api_key,
    }
    if headers:
        stream_options["headers"] = headers
    if model.reasoning and thinking_level is not None:
        stream_options["reasoning"] = thinking_level

    response = await _complete_summarization(
        model,
        Context.model_validate(
            {
                "system_prompt": SUMMARIZATION_SYSTEM_PROMPT,
                "messages": summarization_messages,
            }
        ),
        SimpleStreamOptions.model_validate(stream_options),
        stream_fn,
    )

    if response.stop_reason == "error":
        raise Exception(
            f"Turn prefix summarization failed: {response.error_message or 'Unknown error'}"
        )

    return "\n".join([c.text for c in response.content if c.type == "text"])
