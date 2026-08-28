"""
Shared utilities for compaction and branch summarization.
"""

import json
from typing import List, Optional, Tuple

from nova_agent import AgentMessage
from nova_ai import Message

from nova_harness.core.harness.session.utils import session_entry_to_context_messages
from nova_harness.core.types.compaction import FileOperations
from nova_harness.core.types.session import SessionEntry

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
    从会话条目中提取 AgentMessage（委托 session 层的统一投影逻辑）。

    Args:
        entry: 会话条目。
        skip_compaction: 为 True 时跳过 compaction 条目（用于压缩自身遍历）。
        skip_tool_results: 为 True 时跳过 role 为 toolResult 的消息（用于分支摘要）。
    """
    if skip_compaction and entry.type == "compaction":
        return None
    messages = session_entry_to_context_messages(entry)
    if not messages:
        return None
    message = messages[0]
    if skip_tool_results and message.role == "toolResult":
        return None
    return message


# ============================================================================
# File Operation Tracking
# ============================================================================


def create_file_ops() -> FileOperations:
    """Create a new FileOperations instance."""
    return FileOperations()


def extract_file_ops_from_message(
    message: AgentMessage, file_ops: FileOperations
) -> None:
    """
    Extract file operations from tool calls in an assistant message.
    """
    if message.role != "assistant":
        return

    for block in message.content:
        if block.type != "toolCall":
            continue

        args = block.arguments
        path = args.get("path")
        if not isinstance(path, str):
            continue

        if block.name == "read":
            file_ops.read.add(path)
        elif block.name == "write":
            file_ops.written.add(path)
        elif block.name == "edit":
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
                    # 对齐 TS：参数值用 JSON.stringify 风格序列化，不用 repr
                    args_str = ", ".join(
                        [
                            f"{k}={json.dumps(v, ensure_ascii=False)}"
                            for k, v in args.items()
                        ]
                    )
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
            text_parts = []
            for c in msg.content:
                if c.type == "text":
                    text_parts.append(c.text)
            content = "".join(text_parts)

            if content:
                parts.append(f"[Tool result]: {_truncate_for_summary(content)}")

    return "\n\n".join(parts)


# ============================================================================
# Summarization System Prompt
# ============================================================================

SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""
