"""
Custom message types and transformers for the coding agent.

Extends the base AgentMessage type with coding-agent specific message types,
and provides a transformer to convert them to LLM-compatible messages.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional, Union, Literal
from datetime import datetime
from pi_agent import AgentMessage
from nova_ai import ImageContent, UserMessage, TextContent
from mashumaro.mixins.json import DataClassJSONMixin

COMPACTION_SUMMARY_PREFIX = """The conversation history before this point was compacted into the following summary:

<summary>
"""

COMPACTION_SUMMARY_SUFFIX = """
</summary>"""

BRANCH_SUMMARY_PREFIX = """The following is a summary of a branch that this conversation came back from:

<summary>
"""

BRANCH_SUMMARY_SUFFIX = """</summary>"""


# ============================================================================
# 基础内容类型
# ============================================================================

@dataclass
class FileContent(DataClassJSONMixin):
    """文件内容（用于消息中的文件引用）"""
    type: Literal["file"] = "file"
    filename: str = ""
    path: str = ""           # 文件路径或标识符
    mime_type: str = ""      # 文件的 MIME 类型
    size: Optional[int] = None      # 文件大小（字节），可选


@dataclass
class BashExecutionMessage(DataClassJSONMixin):
    """Message type for bash executions via the ! command."""
    
    command: str
    output: str
    exit_code: Optional[int]
    cancelled: bool
    truncated: bool
    full_output_path: Optional[str] = None
    timestamp: Optional[int] = None
    exclude_from_context: bool = False
    role: Literal["bashExecution"] = "bashExecution"


@dataclass
class CustomMessage(DataClassJSONMixin):
    """Message type for extension-injected messages via send_message().
    These are custom messages that extensions can inject into the conversation.
    """
    
    custom_type: str
    content: Union[str, List[Union[TextContent, ImageContent]]]
    display: bool
    details: Optional[Any] = None
    timestamp: Optional[int] = None
    role: Literal["custom"] = "custom"


@dataclass
class InterAgentMessage(DataClassJSONMixin):
    """Message type for inter-agent communication."""
    
    sender_id: str
    sender_name: str
    content: Union[str, List[Union[TextContent, ImageContent, FileContent]]]
    display: bool = True
    timestamp: Optional[int] = None
    role: Literal["interAgent"] = "interAgent"


@dataclass
class FrontendMessage(DataClassJSONMixin):
    """Message type for frontend-initiated messages."""
    
    content: Union[str, List[Union[TextContent, ImageContent, FileContent]]]
    display: bool = True
    timestamp: Optional[int] = None
    role: Literal["frontend"] = "frontend"


@dataclass
class BranchSummaryMessage(DataClassJSONMixin):
    """Branch summary message for conversation branching."""
    
    summary: str
    from_id: str
    timestamp: Optional[int] = None
    role: Literal["branchSummary"] = "branchSummary"


@dataclass
class CompactionSummaryMessage(DataClassJSONMixin):
    """Compaction summary message for context compression."""
    
    summary: str
    tokens_before: int
    timestamp: Optional[int] = None
    role: Literal["compactionSummary"] = "compactionSummary"


# ============================================================================
# 转换函数
# ============================================================================

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


def convert_content(content: Union[str, List[Union[TextContent, ImageContent, FileContent]]]) -> str:
    """将混合内容转换为纯文本表示（用于LLM）。
    
    文本和图片内容直接保留，文件转换为描述性文本。
    """
    if isinstance(content, str):
        return content
    
    parts = []
    for item in content:
        if item.type == 'file':
            parts.append(
                TextContent(
                    text=f"[File: {item.filename} ({item.mime_type}, {item.size or 'unknown'} bytes) at {item.path}]"
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


def create_inter_agent_message(
    sender_id: str,
    sender_name: str,
    content: Union[str, List[Union[TextContent, ImageContent]]],
    display: bool,
    timestamp: str,
) -> InterAgentMessage:
    """Convert InterAgentMessageEntry to AgentMessage format."""
    return InterAgentMessage(
        sender_id=sender_id,
        sender_name=sender_name,
        content=content,
        display=display,
        timestamp=_parse_timestamp(timestamp),
    )


def create_frontend_message(
    content: Union[str, List[Union[TextContent, ImageContent]]],
    display: bool,
    timestamp: str,
) -> FrontendMessage:
    """Convert FrontendMessageEntry to AgentMessage format."""
    return FrontendMessage(
        content=content,
        display=display,
        timestamp=_parse_timestamp(timestamp),
    )


def _parse_timestamp(timestamp: str) -> int:
    """Parse timestamp string to milliseconds since epoch."""
    # Assumes timestamp is in ISO format or similar that datetime can parse
    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    return int(dt.timestamp() * 1000)


# ============================================================================
# 主转换函数（已更新支持新消息类型）
# ============================================================================

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
            if getattr(m, 'exclude_from_context', False):
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
        
        elif m.role == "interAgent":
            content = m.content
            if isinstance(content, str):
                content = [TextContent(type="text", text=content)]
            else: 
                content = convert_content(content)
            
            msg = UserMessage(
                role="user",
                content=content,
                timestamp=m.timestamp,
            )
        
        elif m.role == "frontend":
            content = m.content
            if isinstance(content, str):
                content = [TextContent(type="text", text=content)]
            else: 
                content = convert_content(content)
            
            msg = UserMessage(
                role="user",
                content=content,
                timestamp=m.timestamp,
            )
            
        elif m.role == "branchSummary":
            msg = UserMessage(
                role="user",
                content=[TextContent(
                    type="text",
                    text=BRANCH_SUMMARY_PREFIX + m.summary + BRANCH_SUMMARY_SUFFIX
                )],
                timestamp=m.timestamp,
            )
        
        elif m.role == "compactionSummary":
            msg = UserMessage(
                role="user",
                content=[TextContent(
                    type="text",
                    text=COMPACTION_SUMMARY_PREFIX + m.summary + COMPACTION_SUMMARY_SUFFIX
                )],
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