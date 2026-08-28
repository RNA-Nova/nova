"""自定义消息类型与相关常量。

对应原 `nova_harness.messages.types`。
"""

from typing import (
    Annotated,
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

from nova_agent import CustomAgentMessage
from nova_ai import ImageContent, TextContent
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

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


# 自定义消息的内容块：按 type 字段判别反序列化（对齐 TS TextContent | ImageContent）
CustomMessageContent = Annotated[
    Union[TextContent, ImageContent], Field(discriminator="type")
]


@runtime_checkable
class ContextInjectable(Protocol):
    """可注入 LLM 上下文的自定义消息契约。

    用户工具（user tool）产出的消息实现本协议后，`convert_to_llm`
    无需认识具体类型即可完成上下文注入。
    """

    timestamp: int
    exclude_from_context: bool

    def to_context_text(self) -> str:
        """翻译为注入 LLM 上下文的文本。"""
        ...


class OpaqueUserToolMessage(CustomAgentMessage):
    """包级用户工具消息在包缺席时的降级形态。

    会话 JSONL 中 ``role`` 属于用户工具消息、但对应包未安装（未注册）时，
    解析层把原始 message dict 全量收进 ``payload`` 降级为本类型：
    数据不丢、默认不进 LLM 上下文（``exclude_from_context=True``）。
    """

    original_role: str
    payload: Dict[str, Any]
    timestamp: int
    exclude_from_context: bool = True
    role: Literal["opaqueUserTool"] = "opaqueUserTool"

    def to_context_text(self) -> str:
        # 永不注入（exclude_from_context 恒为 True），仅为满足协议
        return ""


class CustomMessage(CustomAgentMessage):
    """Message type for extension-injected messages via send_message().
    These are custom messages that extensions can inject into the conversation.
    """

    custom_type: str
    content: Union[str, List[CustomMessageContent]]
    display: bool
    details: Optional[Any] = None
    timestamp: int
    role: Literal["custom"] = "custom"


class BranchSummaryMessage(CustomAgentMessage):
    """Branch summary message for conversation branching."""

    summary: str
    from_id: str
    timestamp: int
    role: Literal["branchSummary"] = "branchSummary"


class CompactionSummaryMessage(CustomAgentMessage):
    """Compaction summary message for context compression."""

    summary: str
    tokens_before: int
    timestamp: int
    role: Literal["compactionSummary"] = "compactionSummary"


__all__ = [
    # constants
    "COMPACTION_SUMMARY_PREFIX",
    "COMPACTION_SUMMARY_SUFFIX",
    "BRANCH_SUMMARY_PREFIX",
    "BRANCH_SUMMARY_SUFFIX",
    # types
    "ContextInjectable",
    "CustomMessageContent",
    "OpaqueUserToolMessage",
    "CustomMessage",
    "BranchSummaryMessage",
    "CompactionSummaryMessage",
]
