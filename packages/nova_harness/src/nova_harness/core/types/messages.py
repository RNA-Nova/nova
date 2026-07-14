"""自定义消息类型与相关常量。

对应原 `nova_harness.messages.types`。
"""

from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional, Union

from nova_agent import CustomAgentMessage
from nova_ai import ImageContent, TextContent
from nova_ai.types.base_model import NovaBaseModel

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

COMPACTION_SUMMARY_PREFIX = """The conversation history before this point was compacted into the following summary:

<summary>
"""

COMPACTION_SUMMARY_SUFFIX = """
</summary>"""

BRANCH_SUMMARY_PREFIX = """The following is a summary of a branch that this conversation came back from:

<summary>
"""

BRANCH_SUMMARY_SUFFIX = """</summary>"""


# ---------------------------------------------------------------------------
# 类型
# ---------------------------------------------------------------------------


@dataclass
class ContentBlock:
    """表示消息中的一个内容块。"""

    type: str
    text: str = ""
    thinking: str = ""
    name: str = ""
    arguments: Any = field(default_factory=dict)


class FileContent(NovaBaseModel):
    """文件内容（用于消息中的文件引用）"""

    type: Literal["file"] = "file"
    filename: str = ""
    path: str = ""  # 文件路径或标识符
    mime_type: str = ""  # 文件的 MIME 类型
    size: Optional[int] = None  # 文件大小（字节），可选


class BashExecutionMessage(CustomAgentMessage):
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


class CustomMessage(CustomAgentMessage):
    """Message type for extension-injected messages via send_message().
    These are custom messages that extensions can inject into the conversation.
    """

    custom_type: str
    content: Union[str, List[Union[TextContent, ImageContent]]]
    display: bool
    details: Optional[Any] = None
    timestamp: Optional[int] = None
    role: Literal["custom"] = "custom"


class BranchSummaryMessage(CustomAgentMessage):
    """Branch summary message for conversation branching."""

    summary: str
    from_id: str
    timestamp: Optional[int] = None
    role: Literal["branchSummary"] = "branchSummary"


class CompactionSummaryMessage(CustomAgentMessage):
    """Compaction summary message for context compression."""

    summary: str
    tokens_before: int
    timestamp: Optional[int] = None
    role: Literal["compactionSummary"] = "compactionSummary"


__all__ = [
    # constants
    "COMPACTION_SUMMARY_PREFIX",
    "COMPACTION_SUMMARY_SUFFIX",
    "BRANCH_SUMMARY_PREFIX",
    "BRANCH_SUMMARY_SUFFIX",
    # types
    "ContentBlock",
    "FileContent",
    "BashExecutionMessage",
    "CustomMessage",
    "BranchSummaryMessage",
    "CompactionSummaryMessage",
]
