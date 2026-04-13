"""
Shared utilities for compaction and branch summarization.
"""

from typing import Set, List, Dict, Any, Tuple, Optional

from pi_agent import AgentMessage
from nova_ai import Message

from .types import FileOperations, ContentBlock

# ============================================================================
# File Operation Tracking
# ============================================================================

def create_file_ops() -> FileOperations:
    """Create a new FileOperations instance."""
    return FileOperations()


def extract_file_ops_from_message(message: AgentMessage, file_ops: FileOperations) -> None:
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
        content = '\n'.join(read_files)
        sections.append(f"<read-files>\n{content}\n</read-files>")

    if modified_files:
        content = '\n'.join(modified_files)
        sections.append(f"<modified-files>\n{content}\n</modified-files>")

    if not sections:
        return ""

    return "\n\n" + "\n\n".join(sections)


# ============================================================================
# Message Serialization
# ============================================================================

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
                text_parts = []
                for c in msg.content:
                    if c.type == "text":
                        text_parts.append(c.text)
                content = "".join(text_parts)

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
                content = '\n'.join(thinking_parts)
                parts.append(f"[Assistant thinking]: {content}")

            if text_parts:
                content = '\n'.join(text_parts)
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
                parts.append(f"[Tool result]: {content}")

    return "\n\n".join(parts)


# ============================================================================
# Summarization System Prompt
# ============================================================================

SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI coding assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""