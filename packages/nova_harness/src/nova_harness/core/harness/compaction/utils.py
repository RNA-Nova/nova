"""
Shared utilities for compaction and branch summarization.
"""

from typing import Any, List, Optional, Tuple

from nova_agent import AgentMessage
from nova_ai import Message

from nova_harness.core.types.compaction import FileOperations
from nova_harness.core.types.session import SessionEntry
from nova_harness.core.utils.messages import (
    create_branch_summary_message,
    create_compaction_summary_message,
    create_custom_message,
)

# ============================================================================
# Message Extraction
# ============================================================================


def get_message_from_entry(
    entry: SessionEntry,
    *,
    skip_compaction: bool = False,
    skip_tool_results: bool = False,
) -> Optional[AgentMessage]:
    """
    从会话条目中提取 AgentMessage。

    Args:
        entry: 会话条目。
        skip_compaction: 为 True 时跳过 compaction 条目（用于压缩自身遍历）。
        skip_tool_results: 为 True 时跳过 role 为 toolResult 的消息（用于分支摘要）。
    """
    if entry.type == "message":
        if skip_tool_results and entry.message.role == "toolResult":
            return None
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
        if skip_compaction:
            return None
        return create_compaction_summary_message(
            entry.summary,
            entry.tokens_before,
            entry.timestamp,
        )
    return None


# ============================================================================
# File Operation Tracking
# ============================================================================


def create_file_ops() -> FileOperations:
    """Create a new FileOperations instance."""
    return FileOperations()


def get_detail_value(details: Any, key: str) -> Any:
    """从 details（Pydantic 模型或 dict）中取值。"""
    if details is None:
        return None
    if isinstance(details, dict):
        return details.get(key)
    return getattr(details, key, None)


def extract_file_ops_from_message(
    message: AgentMessage, file_ops: FileOperations
) -> None:
    """
    Extract file operations from tool calls in an assistant message.
    """
    if message.role != "assistant":
        return

    if not hasattr(message, "content") or not isinstance(message.content, list):
        return

    for block in message.content:
        if block is None:
            continue

        if block.type != "toolCall":
            continue

        if not hasattr(block, "arguments") or not hasattr(block, "name"):
            continue

        args = block.arguments
        if not isinstance(args, dict):
            continue

        path = args.get("path")
        if not isinstance(path, str):
            continue

        tool_name = block.name
        if tool_name == "read":
            file_ops.read.add(path)
        elif tool_name == "write":
            file_ops.written.add(path)
        elif tool_name == "edit":
            file_ops.edited.add(path)


def compute_file_lists(file_ops: FileOperations) -> Tuple[List[str], List[str]]:
    """
    Compute final file lists from file operations.
    Returns read_files (files only read, not modified) and modified_files.
    """
    modified = set(file_ops.edited) | set(file_ops.written)
    read_only = sorted([f for f in file_ops.read if f not in modified])
    modified_files = sorted(list(modified))
    return read_only, modified_files


def format_file_operations(read_files: List[str], modified_files: List[str]) -> str:
    """
    Format file operations as XML tags for summary.
    """
    sections: List[str] = []

    if read_files:
        content = "\n".join(read_files)
        sections.append(f"<read-files>\n{content}\n</read-files>")

    if modified_files:
        content = "\n".join(modified_files)
        sections.append(f"<modified-files>\n{content}\n</modified-files>")

    if not sections:
        return ""

    return "\n\n" + "\n\n".join(sections)


# ============================================================================
# Message Serialization
# ============================================================================

# Maximum characters for a tool result in serialized summaries.
_TOOL_RESULT_MAX_CHARS = 2000


def _truncate_for_summary(text: str, max_chars: int = _TOOL_RESULT_MAX_CHARS) -> str:
    """Truncate text to a maximum character length for summarization."""
    if len(text) <= max_chars:
        return text
    truncated_chars = len(text) - max_chars
    return f"{text[:max_chars]}\n\n[... {truncated_chars} more characters truncated]"


def serialize_conversation(messages: List[Message]) -> str:
    """
    Serialize LLM messages to text for summarization.
    This prevents the model from treating it as a conversation to continue.
    Call convert_to_llm() first to handle custom message types.
    """
    parts: List[str] = []

    for msg in messages:
        if msg.role == "user":
            if isinstance(msg.content, str):
                content = msg.content
            else:
                # Assume content is a list of content blocks
                user_text_parts = []
                for c in msg.content:
                    if c.type == "text":
                        user_text_parts.append(c.text)
                content = "".join(user_text_parts)

            if content:
                parts.append(f"[User]: {content}")

        elif msg.role == "assistant":
            text_parts: List[str] = []
            thinking_parts: List[str] = []
            tool_calls: List[str] = []

            for block in msg.content:

                block_type = block.type

                if block_type == "text":
                    text_parts.append(block.text)
                elif block_type == "thinking":
                    thinking_parts.append(block.thinking)
                elif block_type == "toolCall":
                    args = block.arguments
                    args_str = ", ".join([f"{k}={repr(v)}" for k, v in args.items()])
                    tool_calls.append(f"{block.name}({args_str})")
            if thinking_parts:
                content = "\n".join(thinking_parts)
                parts.append(f"[Assistant thinking]: {content}")

            if text_parts:
                content = "\n".join(text_parts)
                parts.append(f"[Assistant]: {content}")

            if tool_calls:
                parts.append(f"[Assistant tool calls]: {'; '.join(tool_calls)}")

        elif msg.role == "toolResult":
            if isinstance(msg.content, list):
                text_parts = []
                for c in msg.content:
                    if c.type == "text":
                        text_parts.append(c.text)
                content = "".join(text_parts)
            else:
                content = str(msg.content)

            if content:
                parts.append(f"[Tool result]: {_truncate_for_summary(content)}")

    return "\n\n".join(parts)


# ============================================================================
# Summarization System Prompt
# ============================================================================

SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI coding assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""
