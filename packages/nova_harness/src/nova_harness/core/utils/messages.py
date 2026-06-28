"""
消息转换与构造工具。

将自定义消息类型转为 LLM 兼容的 UserMessage，并提供构造摘要/自定义消息的工厂函数。
"""

from datetime import datetime
from typing import Any, List, Optional, Union

from nova_agent import AgentMessage
from nova_ai import ImageContent, TextContent, UserMessage

from nova_harness.core.types.messages import (
    BRANCH_SUMMARY_PREFIX,
    BRANCH_SUMMARY_SUFFIX,
    COMPACTION_SUMMARY_PREFIX,
    COMPACTION_SUMMARY_SUFFIX,
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    FileContent,
)


def extract_text_from_content(
    content: Union[str, List[Union[TextContent, ImageContent]]],
) -> str:
    """从文本内容块列表中提取纯文本。"""
    if isinstance(content, str):
        return content
    return "".join(
        getattr(block, "text", "") or ""
        for block in content
        if getattr(block, "type", None) == "text"
    )


def bash_execution_to_text(msg: BashExecutionMessage) -> str:
    """Convert a BashExecutionMessage to user message text for LLM context."""
    text = f"Ran `{msg.command}`\n"
    if msg.output:
        text += f"```\n{msg.output}\n```"
    else:
        text += "(no output)"

    if msg.cancelled:
        text += "\n\n(command cancelled)"
    elif msg.exit_code is not None and msg.exit_code != 0:
        text += f"\n\nCommand exited with code {msg.exit_code}"

    if msg.truncated and msg.full_output_path:
        text += f"\n\n[Output truncated. Full output: {msg.full_output_path}]"

    return text


def convert_content(
    content: Union[str, List[Union[TextContent, ImageContent, FileContent]]],
) -> Union[str, List[Union[TextContent, ImageContent, FileContent]]]:
    """将混合内容转换为纯文本表示（用于LLM）。

    文本和图片内容直接保留，文件转换为描述性文本。
    """
    if isinstance(content, str):
        return content

    parts = []
    for item in content:
        if item.type == "file":
            parts.append(
                TextContent(
                    type="text",
                    text=f"[File: {item.filename} ({item.mime_type}, {item.size or 'unknown'} bytes) at {item.path}]",
                )
            )
        else:
            parts.append(item)

    return parts


def create_branch_summary_message(
    summary: str,
    from_id: str,
    timestamp: str,
) -> BranchSummaryMessage:
    """Create a branch summary message."""
    return BranchSummaryMessage(
        summary=summary,
        from_id=from_id,
        timestamp=_parse_timestamp(timestamp),
    )


def create_compaction_summary_message(
    summary: str,
    tokens_before: int,
    timestamp: str,
) -> CompactionSummaryMessage:
    """Create a compaction summary message."""
    return CompactionSummaryMessage(
        summary=summary,
        tokens_before=tokens_before,
        timestamp=_parse_timestamp(timestamp),
    )


def create_custom_message(
    custom_type: str,
    content: Union[str, List[Union[TextContent, ImageContent]]],
    display: bool,
    details: Optional[Any],
    timestamp: str,
) -> CustomMessage:
    """Convert CustomMessageEntry to AgentMessage format."""
    return CustomMessage(
        custom_type=custom_type,
        content=content,
        display=display,
        details=details,
        timestamp=_parse_timestamp(timestamp),
    )


def _parse_timestamp(timestamp: str) -> int:
    """Parse timestamp string to milliseconds since epoch."""
    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


async def convert_to_llm(messages: List[AgentMessage]) -> List[UserMessage]:
    """
    Transform AgentMessages (including custom types) to LLM-compatible Messages.

    This is used by:
    - Agent's transform_to_llm option (for prompt calls and queued messages)
    - Compaction's generate_summary (for summarization)
    - Custom extensions and tools
    """
    result = []

    for m in messages:
        msg = None

        if m.role == "bashExecution":
            # Skip messages excluded from context (!! prefix)
            if getattr(m, "exclude_from_context", False):
                continue

            msg = UserMessage(
                role="user",
                content=[TextContent(type="text", text=bash_execution_to_text(m))],
                timestamp=m.timestamp,
            )

        elif m.role == "custom":
            content = m.content
            if isinstance(content, str):
                content = [TextContent(type="text", text=content)]

            msg = UserMessage(
                role="user",
                content=content,
                timestamp=m.timestamp,
            )

        elif m.role == "branchSummary":
            msg = UserMessage(
                role="user",
                content=[
                    TextContent(
                        type="text",
                        text=BRANCH_SUMMARY_PREFIX + m.summary + BRANCH_SUMMARY_SUFFIX,
                    )
                ],
                timestamp=m.timestamp,
            )

        elif m.role == "compactionSummary":
            msg = UserMessage(
                role="user",
                content=[
                    TextContent(
                        type="text",
                        text=COMPACTION_SUMMARY_PREFIX
                        + m.summary
                        + COMPACTION_SUMMARY_SUFFIX,
                    )
                ],
                timestamp=m.timestamp,
            )

        elif m.role in ("user", "assistant", "toolResult"):
            msg = m

        else:
            # Unknown message type, skip it
            continue

        if msg is not None:
            result.append(msg)

    return result


__all__ = [
    "convert_to_llm",
    "convert_content",
    "bash_execution_to_text",
    "extract_text_from_content",
    "create_branch_summary_message",
    "create_compaction_summary_message",
    "create_custom_message",
]
