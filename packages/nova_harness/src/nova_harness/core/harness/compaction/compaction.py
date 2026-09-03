"""
Context compaction for long sessions.

Pure functions for compaction logic. The session manager handles I/O,
and after compaction the session is reloaded.
"""

import inspect
import json
import time
from typing import Dict, List, Optional, Tuple

from nova_agent import AgentMessage, StreamFn
from nova_ai import (
    AbortSignal,
    AssistantMessage,
    Context,
    Model,
    ModelThinkingLevel,
    SimpleStreamOptions,
    Usage,
    builtin_models,
    to_thinking_level,
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
from nova_harness.core.types.messages import ContextInjectable
from nova_harness.core.types.session.entries import CompactionEntry, SessionEntry
from nova_harness.core.utils.messages import convert_to_llm

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
            # 只从 pi 生成的摘要（from_hook 不为 True）提取文件操作，
            # 避免重复读取 extension-generated 摘要里的已操作文件。
            # details 在写入关口已归一化为 dict（见 SessionManager._normalize_details）。
            details = prev_compaction.details
            if not prev_compaction.from_hook and isinstance(details, dict):
                read_files = details.get("read_files")
                if isinstance(read_files, list):
                    for f in read_files:
                        file_ops.read.add(f)
                modified_files = details.get("modified_files")
                if isinstance(modified_files, list):
                    for f in modified_files:
                        file_ops.edited.add(f)

    # Extract from tool calls in messages
    for msg in messages:
        extract_file_ops_from_message(msg, file_ops)

    return file_ops


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
    Skips aborted, error, and all-zero usage messages as they don't have valid usage data.
    """
    if msg.role != "assistant":
        return None
    if (
        msg.stop_reason != "aborted"
        and msg.stop_reason != "error"
        and msg.usage
        and calculate_context_tokens(msg.usage) > 0
    ):
        return msg.usage
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
                # 对齐 TS JSON.stringify：紧凑 JSON 序列化，不用 str(dict)
                chars += len(
                    json.dumps(
                        block.arguments, separators=(",", ":"), ensure_ascii=False
                    )
                )
        return (chars + 3) // 4

    elif message.role in ("custom", "toolResult"):
        if isinstance(message.content, str):
            chars = len(message.content)
        else:
            for block in message.content:
                if block.type == "text" and block.text:
                    chars += len(block.text)
                if block.type == "image":
                    chars += 4800  # 对齐 TS ESTIMATED_IMAGE_CHARS
        return (chars + 3) // 4

    elif message.role == "bashExecution":
        chars = len(message.command) + len(message.output)
        return (chars + 3) // 4

    elif message.role in ("branchSummary", "compactionSummary"):
        chars = len(message.summary)
        return (chars + 3) // 4

    return 0


def estimate_messages_tokens(messages: List[AgentMessage]) -> int:
    """对一组消息估算总 token 数（对齐 TS estimateMessagesTokens）。"""
    total = 0
    for message in messages:
        total += estimate_tokens(message)
    return total


def _find_valid_cut_points(
    entries: List[SessionEntry], start_index: int, end_index: int
) -> List[int]:
    """
    Find valid cut points: indices of context-visible user-like or assistant messages.
    Never cut at tool results (they must follow their tool call).
    When we cut at an assistant message with tool calls, its tool results follow it
    and will be kept.
    """
    cut_points: List[int] = []
    for i in range(start_index, end_index):
        entry = entries[i]
        if entry.type == "compaction":
            continue
        message = get_message_from_entry(entry)
        if message is not None and _is_cut_point_message(message):
            cut_points.append(i)
    return cut_points


def _is_cut_point_message(message: AgentMessage) -> bool:
    """该角色处可以切断（toolResult 必须跟随其 tool call，不可切）。"""
    return message.role in (
        "user",
        "assistant",
        "bashExecution",
        "custom",
        "branchSummary",
        "compactionSummary",
    )


def _is_turn_start_message(message: AgentMessage) -> bool:
    """该角色可以作为一轮的开始（对齐 TS isTurnStartMessage）。

    包级用户工具消息（bashExecution 等）经 ``ContextInjectable`` 多态
    判定——不硬编码具体 role（框架不内置用户工具）。opaque 降级形态
    同样满足协议形状，保证包缺席时 turn 边界与包在场一致。
    """
    if message.role in ("user", "custom", "branchSummary", "compactionSummary"):
        return True
    return isinstance(message, ContextInjectable)


def _is_turn_start_entry(entry: SessionEntry) -> bool:
    """该条目是否可以作为一轮的开始（对齐 TS isTurnStartEntry）。"""
    if entry.type == "compaction":
        return False
    message = get_message_from_entry(entry)
    return message is not None and _is_turn_start_message(message)


def find_turn_start_index(
    entries: List[SessionEntry], entry_index: int, start_index: int
) -> int:
    """
    Find the context-visible user-role message that starts the turn containing
    the given entry index. Returns -1 if no turn start found before the index.
    """
    for i in range(entry_index, start_index - 1, -1):
        if _is_turn_start_entry(entries[i]):
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
        message = get_message_from_entry(entry)
        message_tokens = estimate_tokens(message) if message is not None else 0
        if message_tokens == 0:
            continue
        accumulated_tokens += message_tokens
        # Check if we've exceeded the budget
        if accumulated_tokens >= keep_recent_tokens:
            # Find the closest valid cut point at or after this entry
            for c in range(len(cut_points)):
                if cut_points[c] >= i:
                    cut_index = cut_points[c]
                    break
            break
    # Scan backwards from cut_index to include adjacent metadata entries
    # that do not affect context.
    while cut_index > start_index:
        prev_entry = entries[cut_index - 1]
        # Stop at compaction boundaries or context-visible entries.
        if (
            prev_entry.type == "compaction"
            or get_message_from_entry(prev_entry) is not None
        ):
            break
        cut_index -= 1

    # Determine if this is a split turn
    cut_entry = entries[cut_index]
    starts_turn = _is_turn_start_entry(cut_entry)
    turn_start_index = (
        -1 if starts_turn else find_turn_start_index(entries, cut_index, start_index)
    )

    return CutPointResult(
        first_kept_entry_index=cut_index,
        turn_start_index=turn_start_index,
        is_split_turn=not starts_turn and turn_start_index != -1,
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
- [x] [Completed tasks/changes]

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
- [x] [Include previously done items AND newly completed items]

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


def _create_summarization_options(
    model: Model,
    max_tokens: int,
    api_key: Optional[str],
    headers: Optional[Dict[str, str]],
    env: Optional[Dict[str, str]],
    signal: Optional[AbortSignal],
    thinking_level: Optional[ModelThinkingLevel],
) -> SimpleStreamOptions:
    """构建摘要请求的流选项（对齐 TS createSummarizationOptions）。"""
    options = SimpleStreamOptions(
        max_tokens=max_tokens,
        signal=signal,
        api_key=api_key,
        headers=headers,
        env=env,
    )
    request_level = to_thinking_level(thinking_level)
    if model.reasoning and request_level is not None:
        options.reasoning = request_level
    return options


async def complete_summarization(
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
        return await builtin_models().complete_simple(model, context, options)

    stream_result = stream_fn(model, context, options)
    if inspect.isawaitable(stream_result):
        stream = await stream_result
    else:
        stream = stream_result

    return await stream.result()


async def generate_summary(
    current_messages: List[AgentMessage],
    model: Model,
    reserve_tokens: int,
    api_key: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    signal: Optional[AbortSignal] = None,
    custom_instructions: Optional[str] = None,
    previous_summary: Optional[str] = None,
    thinking_level: Optional[ModelThinkingLevel] = None,
    stream_fn: Optional[StreamFn] = None,
    env: Optional[Dict[str, str]] = None,
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
    llm_messages = convert_to_llm(current_messages)
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

    response = await complete_summarization(
        model,
        Context.model_validate(
            {
                "system_prompt": SUMMARIZATION_SYSTEM_PROMPT,
                "messages": summarization_messages,
            }
        ),
        _create_summarization_options(
            model, max_tokens, api_key, headers, env, signal, thinking_level
        ),
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

    # 估算当前完整 LLM 上下文大小
    tokens_before = estimate_context_tokens(
        build_session_context(path_entries).messages
    ).tokens

    cut_point = find_cut_point(
        path_entries, boundary_start, boundary_end, settings.keep_recent_tokens
    )
    # Get UUID of first kept entry
    first_kept_entry = path_entries[cut_point.first_kept_entry_index]
    if not first_kept_entry.id:
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
        msg = get_message_from_entry(path_entries[i], skip_compaction=True)
        if msg:
            messages_to_summarize.append(msg)

    # Messages for turn prefix summary (if splitting a turn)
    turn_prefix_messages: List[AgentMessage] = []
    if cut_point.is_split_turn:
        for i in range(cut_point.turn_start_index, cut_point.first_kept_entry_index):
            msg = get_message_from_entry(path_entries[i], skip_compaction=True)
            if msg:
                turn_prefix_messages.append(msg)

    # 两路消息都空时不发摘要请求（对齐 TS prepareCompaction 的空摘要短路，
    # 位置在 fileOps 提取之前，与 TS 一致）
    if not messages_to_summarize and not turn_prefix_messages:
        return None

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
    api_key: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    custom_instructions: Optional[str] = None,
    signal: Optional[AbortSignal] = None,
    thinking_level: Optional[ModelThinkingLevel] = None,
    stream_fn: Optional[StreamFn] = None,
    env: Optional[Dict[str, str]] = None,
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

    # Generate summaries and merge into one
    summary: str

    if is_split_turn and turn_prefix_messages:
        if messages_to_summarize:
            history_result = await generate_summary(
                messages_to_summarize,
                model,
                settings.reserve_tokens,
                api_key,
                headers,
                signal,
                custom_instructions,
                previous_summary,
                thinking_level,
                stream_fn,
                env,
            )
        else:
            history_result = "No prior history."

        turn_prefix_result = await _generate_turn_prefix_summary(
            turn_prefix_messages,
            model,
            settings.reserve_tokens,
            api_key,
            headers,
            env,
            signal,
            thinking_level,
            stream_fn,
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
            headers,
            signal,
            custom_instructions,
            previous_summary,
            thinking_level,
            stream_fn,
            env,
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
        details=CompactionDetails(read_files=read_files, modified_files=modified_files),
    )


async def _generate_turn_prefix_summary(
    messages: List[AgentMessage],
    model: Model,
    reserve_tokens: int,
    api_key: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    env: Optional[Dict[str, str]] = None,
    signal: Optional[AbortSignal] = None,
    thinking_level: Optional[ModelThinkingLevel] = None,
    stream_fn: Optional[StreamFn] = None,
) -> str:
    """
    Generate a summary for a turn prefix (when splitting a turn).
    """
    max_tokens = int(0.5 * reserve_tokens)
    if model.max_tokens and model.max_tokens > 0:
        max_tokens = min(max_tokens, model.max_tokens)

    llm_messages = convert_to_llm(messages)
    conversation_text = serialize_conversation(llm_messages)
    prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n{_TURN_PREFIX_SUMMARIZATION_PROMPT}"

    summarization_messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": prompt_text}],
            "timestamp": int(time.time() * 1000),
        }
    ]

    response = await complete_summarization(
        model,
        Context.model_validate(
            {
                "system_prompt": SUMMARIZATION_SYSTEM_PROMPT,
                "messages": summarization_messages,
            }
        ),
        _create_summarization_options(
            model, max_tokens, api_key, headers, env, signal, thinking_level
        ),
        stream_fn,
    )

    if response.stop_reason == "error":
        raise Exception(
            f"Turn prefix summarization failed: {response.error_message or 'Unknown error'}"
        )

    return "\n".join([c.text for c in response.content if c.type == "text"])
