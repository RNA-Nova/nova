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


# ============================================================================
# 前后端通信专用消息类型（新增）
# ============================================================================

@dataclass
class FrontendToAgentMessage(DataClassJSONMixin):
    """前端发送给 Agent 的消息类型。
    
    用于处理来自前端的输入，支持文本、图片和文件内容的混合列表。
    类似于 CustomMessage，但专门用于前端到 Agent 的通信。
    """
    
    content: Union[str, List[Union[TextContent, ImageContent, FileContent]]]  # 支持字符串或混合内容列表
    display: bool = True                     # 是否在前端显示
    timestamp: Optional[int] = None
    role: Literal["frontend_to_agent"] = "frontend_to_agent"


@dataclass
class AgentToFrontendMessage(DataClassJSONMixin):
    """Agent 发送给前端的消息类型。
    
    用于向前端发送响应，支持文本、图片和文件内容的混合列表。
    可以包含需要在前端展示或下载的文件。
    """
    
    content: Union[str, List[Union[TextContent, ImageContent, FileContent]]]  # 支持字符串或混合内容列表
    display: bool = True                     # 是否在前端显示
    timestamp: Optional[int] = None
    role: Literal["agent_to_frontend"] = "agent_to_frontend"


# ============================================================================
# 原有消息类型
# ============================================================================

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


# ============================================================================
# 前后端消息创建函数（新增）
# ============================================================================

# def create_frontend_to_agent_message(
#     content: Union[str, List[ContentItem]],
#     display: bool = True,
#     metadata: Optional[dict] = None,
#     timestamp: Optional[str] = None,
# ) -> FrontendToAgentMessage:
#     """创建前端发送给 Agent 的消息。
    
#     Args:
#         content: 消息内容，可以是字符串或包含 TextContent/ImageContent/FileContent 的列表
#         display: 是否在前端显示此消息
#         metadata: 可选的元数据字典
#         timestamp: ISO 格式时间戳字符串，默认为当前时间
    
#     Returns:
#         FrontendToAgentMessage 实例
#     """
#     ts = _parse_timestamp(timestamp) if timestamp else int(datetime.now().timestamp() * 1000)
#     return FrontendToAgentMessage(
#         content=content,
#         display=display,
#         metadata=metadata,
#         timestamp=ts,
#     )


# def create_agent_to_frontend_message(
#     content: Union[str, List[ContentItem]],
#     display: bool = True,
#     require_ack: bool = False,
#     metadata: Optional[dict] = None,
#     timestamp: Optional[str] = None,
# ) -> AgentToFrontendMessage:
#     """创建 Agent 发送给前端的消息。
    
#     Args:
#         content: 消息内容，可以是字符串或包含 TextContent/ImageContent/FileContent 的列表
#         display: 是否在前端显示此消息
#         require_ack: 是否需要前端确认接收（用于重要文件传输）
#         metadata: 可选的元数据字典（如文件下载链接、操作指令等）
#         timestamp: ISO 格式时间戳字符串，默认为当前时间
    
#     Returns:
#         AgentToFrontendMessage 实例
#     """
#     ts = _parse_timestamp(timestamp) if timestamp else int(datetime.now().timestamp() * 1000)
#     return AgentToFrontendMessage(
#         content=content,
#         display=display,
#         require_ack=require_ack,
#         metadata=metadata,
#         timestamp=ts,
#     )


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
        
        elif m.role == "frontend_to_agent":
            # 前端发来的消息转换为LLM可理解的格式
            content = convert_content(m.content)
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