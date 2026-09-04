"""
消息转换与构造工具。

将自定义消息类型转为 LLM 兼容的 UserMessage，并提供构造摘要/自定义消息的工厂函数。
"""

from datetime import datetime
from typing import Any, List, Optional, Union

from nova_agent import AgentMessage
from nova_ai import ImageContent, Message, TextContent, UserMessage

from nova_harness.core.types.messages import (
    BRANCH_SUMMARY_PREFIX,
    BRANCH_SUMMARY_SUFFIX,
    COMPACTION_SUMMARY_PREFIX,
    COMPACTION_SUMMARY_SUFFIX,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    ContextInjectable,
    CustomMessage,
)


def extract_text_from_content(
    content: Union[str, List[Union[TextContent, ImageContent]]],
) -> str:
    """从内容（str 或内容块列表）提取纯文本（空格拼接，对齐 TS extractTextContent）。"""
    if isinstance(content, str):
        return content
    return " ".join(block.text for block in content if isinstance(block, TextContent))


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
    """Parse timestamp string to milliseconds since epoch.

    非法字符串回退 0（对齐 TS ``new Date(s).getTime()`` 得 NaN 的静默语义——
    timestamp 不参与逻辑，仅为展示元数据；Python int 无法表达 NaN，取 0）。
    """
    try:
        return int(datetime.fromisoformat(timestamp).timestamp() * 1000)
    except ValueError:
        return 0


def convert_to_llm(messages: List[AgentMessage]) -> List[Message]:
    """
    Transform AgentMessages (including custom types) to LLM-compatible Messages.

    This is used by:
    - Agent's transform_to_llm option (for prompt calls and queued messages)
    - Compaction's generate_summary (for summarization)
    - Custom extensions and tools
    """
    result: List[Message] = []

    for m in messages:
        msg = None

        if isinstance(m, ContextInjectable):
            # 可注入上下文的自定义消息（bash 执行、用户工具产出等）：
            # 排除标记优先，翻译走消息自身的多态方法
            if m.exclude_from_context:
                continue

            msg = UserMessage(
                role="user",
                content=[TextContent(type="text", text=m.to_context_text())],
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
    "extract_text_from_content",
    "create_branch_summary_message",
    "create_compaction_summary_message",
    "create_custom_message",
]
