"""
Type definitions for session management
"""

from typing import (
    Any, Dict, Optional, List, Union, Tuple, Generic, TypeVar, Literal
)
from dataclasses import dataclass, field
from datetime import datetime

from mashumaro import field_options

from .constants import CURRENT_SESSION_VERSION

# 类型导入替换
from pi_agent import AgentMessage
from nova_ai import ImageContent, Message, TextContent, ThinkingLevel
from ..messages import (
    BashExecutionMessage, CustomMessage, FileContent,
    InterAgentMessage, FrontendMessage
)
from mashumaro.mixins.json import DataClassJSONMixin

@dataclass
class SessionHeader(DataClassJSONMixin):
    """会话头部"""
    type: Literal["session"] = "session"
    version: int = CURRENT_SESSION_VERSION
    id: str = ""
    timestamp: str = ""
    cwd: str = ""
    parent_session: Optional[str] = None


@dataclass
class SessionEntryBase(DataClassJSONMixin):
    """会话条目基类 - 不包含 type，由子类各自定义"""
    id: str = ""
    parent_id: Optional[str] = None
    timestamp: str = ""


@dataclass
class SessionMessageEntry(SessionEntryBase):
    """消息条目"""
    type: Literal["message"] = "message"
    message: Union['Message', 'CustomMessage', 'BashExecutionMessage'] = None

    def __post_init__(self):
        if self.message is None:
            raise ValueError("message cannot be None")


@dataclass
class ThinkingLevelChangeEntry(SessionEntryBase):
    """思考级别变更条目"""
    type: Literal["thinking_level_change"] = "thinking_level_change"
    thinking_level: Optional[ThinkingLevel] = None


@dataclass
class ModelChangeEntry(SessionEntryBase):
    """模型变更条目"""
    type: Literal["model_change"] = "model_change"
    provider: str = ""
    model_id: str = ""

@dataclass
class CompactionDetails(DataClassJSONMixin):
    """Details stored in CompactionEntry.details for file tracking."""
    read_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)

@dataclass
class CompactionEntry(SessionEntryBase):
    """压缩条目"""
    type: Literal["compaction"] = "compaction"
    summary: str = ""
    first_kept_entry_id: str = ""
    tokens_before: int = 0
    details: Optional[CompactionDetails] = None
    from_hook: bool = False


@dataclass
class BranchSummaryEntry(SessionEntryBase):
    """分支摘要条目"""
    type: Literal["branch_summary"] = "branch_summary"
    from_id: str = ""
    summary: str = ""
    details: Optional[CompactionDetails] = None
    from_hook: bool = False


@dataclass
class CustomEntry(SessionEntryBase):
    """自定义条目（不参与LLM上下文）"""
    type: Literal["custom"] = "custom"
    custom_type: str = ""
    data: Optional[Dict[str,Any]] = None


@dataclass
class LabelEntry(SessionEntryBase):
    """标签条目"""
    type: Literal["label"] = "label"
    target_id: str = ""
    label: Optional[str] = None


@dataclass
class SessionInfoEntry(SessionEntryBase):
    """会话信息条目"""
    type: Literal["session_info"] = "session_info"
    name: Optional[str] = None

# 自定义序列化函数
def serialize_content(value):
    if isinstance(value, str):
        return value
    # 只要继承了 Mixin，就一定有 to_dict() 方法
    return[item.to_dict() for item in value]

# 自定义反序列化函数 (如果你需要 from_json 功能的话)
def deserialize_content(value):
    if isinstance(value, str):
        return value
    res =[]
    for item in value:
        if item.get("type") == "text":
            res.append(TextContent.from_dict(item))
        elif item.get("type") == "image":
            res.append(ImageContent.from_dict(item))
        else:
            res.append(FileContent.from_dict(item))
    return res


@dataclass
class SendToFrontendEntry(SessionEntryBase):
    """发送到前端的消息条目（不参与LLM上下文）"""
    type: Literal["send_to_frontend"] = "send_to_frontend"
    content: Union[str, List[Union['TextContent', 'ImageContent']]] = field(
        default_factory=list,
        metadata=field_options(
            serialize=serialize_content,
            deserialize=deserialize_content
        )
    )
    display: bool = True


@dataclass
class SendToAgentEntry(SessionEntryBase):
    """发送到Agent的消息条目（不参与LLM上下文）"""
    type: Literal["send_to_agent_message"] = "send_to_agent_message"
    receiver_id: str = ""
    receiver_name: str = ""
    content: Union[str, List[Union['TextContent', 'ImageContent']]] = field(
        default_factory=list,
        metadata=field_options(
            serialize=serialize_content,
            deserialize=deserialize_content
        )
    )
    display: bool = True


@dataclass
class CustomMessageEntry(SessionEntryBase):
    """自定义消息条目（参与LLM上下文）"""
    type: Literal["custom_message"] = "custom_message"
    custom_type: str = ""
    content: Union[str, List[Union['TextContent', 'ImageContent']]] = field(
        default_factory=list,
        metadata=field_options(
            serialize=serialize_content,
            deserialize=deserialize_content
        )
    )
    details: Optional[CompactionDetails] = None
    display: bool = True


@dataclass
class InterAgentMessageEntry(SessionEntryBase):
    """Inter-agent 消息条目（参与LLM上下文）"""
    type: Literal["inter_agent_message"] = "inter_agent_message"
    sender_id: str = ""
    sender_name: str = ""
    content: Union[str, List[Union['TextContent', 'ImageContent']]] = field(
        default_factory=list,
        metadata=field_options(
            serialize=serialize_content,
            deserialize=deserialize_content
        )
    )
    display: bool = True


@dataclass
class FrontendMessageEntry(SessionEntryBase):
    """Frontend 消息条目（参与LLM上下文）"""
    type: Literal["frontend_message"] = "frontend_message"
    content: Union[str, List[Union['TextContent', 'ImageContent']]] = field(
        default_factory=list,
        metadata=field_options(
            serialize=serialize_content,
            deserialize=deserialize_content
        )
    )
    display: bool = True


# 会话条目联合类型
SessionEntry = Union[
    SessionMessageEntry,
    ThinkingLevelChangeEntry,
    ModelChangeEntry,
    CompactionEntry,
    BranchSummaryEntry,
    CustomEntry,
    SendToFrontendEntry,
    SendToAgentEntry,
    CustomMessageEntry,
    InterAgentMessageEntry,
    FrontendMessageEntry,
    LabelEntry,
    SessionInfoEntry
]

FileEntry = Union[SessionHeader, SessionEntry]


@dataclass
class SessionTreeNode(DataClassJSONMixin):
    """会话树节点"""
    entry: SessionEntry
    children: List['SessionTreeNode'] = field(default_factory=list)
    label: Optional[str] = None


@dataclass
class SessionContext(DataClassJSONMixin):
    """会话上下文"""
    messages: List['AgentMessage'] = field(default_factory=list)
    thinking_level: Optional[ThinkingLevel] = None
    model: Optional[Tuple[str, str]] = None  # (provider, model_id)


@dataclass
class SessionInfo(DataClassJSONMixin):
    """会话信息"""
    path: str = ""
    id: str = ""
    cwd: str = ""
    name: Optional[str] = None
    parent_session_path: Optional[str] = None
    created: datetime = None
    modified: datetime = None
    message_count: int = 0
    first_message: str = ""
    all_messages_text: str = ""

    def __post_init__(self):
        if self.created is None:
            self.created = datetime.now()
        if self.modified is None:
            self.modified = datetime.now()

