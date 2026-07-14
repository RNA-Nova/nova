"""
会话条目类型定义。

对应原 `nova_harness.session.types`，仅保留纯数据类型。
"""

from typing import Any, Dict, List, Literal, Optional, Union

from nova_agent import AgentMessage
from nova_ai import ImageContent, Message, TextContent, ThinkingLevel
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field, model_validator

from nova_harness.core.types.messages import (
    BashExecutionMessage,
    CustomMessage,
    FileContent,
)
from nova_harness.core.types.session.constants import CURRENT_SESSION_VERSION


class SessionHeader(NovaBaseModel):
    """会话头部"""

    type: Literal["session"] = "session"
    version: int = CURRENT_SESSION_VERSION
    id: str = ""
    timestamp: str = ""
    cwd: str = ""
    parent_session: Optional[str] = None


class SessionEntryBase(NovaBaseModel):
    """会话条目基类 - 不包含 type，由子类各自定义"""

    id: str = ""
    parent_id: Optional[str] = None
    timestamp: str = ""


class SessionMessageEntry(SessionEntryBase):
    """消息条目"""

    type: Literal["message"] = "message"
    message: Optional[Union["Message", "CustomMessage", "BashExecutionMessage"]] = None

    @model_validator(mode="after")
    def check_message(self):
        if self.message is None:
            raise ValueError("message cannot be None")
        return self


class ThinkingLevelChangeEntry(SessionEntryBase):
    """思考级别变更条目"""

    type: Literal["thinking_level_change"] = "thinking_level_change"
    thinking_level: Optional[ThinkingLevel] = None


class ModelChangeEntry(SessionEntryBase):
    """模型变更条目"""

    type: Literal["model_change"] = "model_change"
    provider: str = ""
    model_id: str = ""


class ActiveToolsChangeEntry(SessionEntryBase):
    """激活工具变更条目"""

    type: Literal["active_tools_change"] = "active_tools_change"
    active_tool_names: List[str] = Field(default_factory=list)


class CompactionEntry(SessionEntryBase):
    """压缩条目。"""

    type: Literal["compaction"] = "compaction"
    summary: str = ""
    first_kept_entry_id: str = ""
    tokens_before: int = 0
    details: Optional[Any] = None
    from_hook: Optional[bool] = None


class BranchSummaryEntry(SessionEntryBase):
    """分支摘要条目。"""

    type: Literal["branch_summary"] = "branch_summary"
    from_id: str = ""
    summary: str = ""
    details: Optional[Any] = None
    from_hook: Optional[bool] = None


class LeafEntry(SessionEntryBase):
    """叶子指针条目，用于持久化当前 leaf 位置。"""

    type: Literal["leaf"] = "leaf"
    target_id: Optional[str] = None


class CustomEntry(SessionEntryBase):
    """自定义条目（不参与LLM上下文）"""

    type: Literal["custom"] = "custom"
    custom_type: str = ""
    data: Optional[Any] = None


class LabelEntry(SessionEntryBase):
    """标签条目"""

    type: Literal["label"] = "label"
    target_id: str = ""
    label: Optional[str] = None


class SessionInfoEntry(SessionEntryBase):
    """会话信息条目"""

    type: Literal["session_info"] = "session_info"
    name: Optional[str] = None


def _serialize_content(value):
    if isinstance(value, str):
        return value
    # 只要继承了 NovaBaseModel，就一定有 model_dump() 方法
    return [item.model_dump() for item in value]


def _deserialize_content(value):
    if isinstance(value, str):
        return value
    res = []
    for item in value:
        # 如果已经是内容对象，直接保留
        if isinstance(item, (TextContent, ImageContent, FileContent)):
            res.append(item)
            continue
        if item.get("type") == "text":
            res.append(TextContent.model_validate(item))
        elif item.get("type") == "image":
            res.append(ImageContent.model_validate(item))
        else:
            res.append(FileContent.model_validate(item))
    return res


class CustomMessageEntry(SessionEntryBase):
    """自定义消息条目（参与LLM上下文）"""

    type: Literal["custom_message"] = "custom_message"
    custom_type: str = ""
    content: Union[str, List[Union["TextContent", "ImageContent"]]] = Field(
        default_factory=list
    )
    details: Optional[Any] = None
    display: bool = True

    @model_validator(mode="before")
    @classmethod
    def _deserialize_content(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = value.copy()
            if "content" in value:
                value["content"] = _deserialize_content(value["content"])
        return value

    def model_dump(self, **kwargs: Any) -> Dict[str, Any]:
        data = super().model_dump(**kwargs)
        data["content"] = _serialize_content(self.content)
        return data


# 会话条目联合类型
SessionEntry = Union[
    SessionMessageEntry,
    ThinkingLevelChangeEntry,
    ModelChangeEntry,
    ActiveToolsChangeEntry,
    CompactionEntry,
    BranchSummaryEntry,
    LeafEntry,
    CustomEntry,
    CustomMessageEntry,
    LabelEntry,
    SessionInfoEntry,
]

FileEntry = Union[SessionHeader, SessionEntry]


__all__ = [
    "SessionHeader",
    "SessionEntryBase",
    "SessionMessageEntry",
    "ThinkingLevelChangeEntry",
    "ModelChangeEntry",
    "ActiveToolsChangeEntry",
    "CompactionEntry",
    "BranchSummaryEntry",
    "LeafEntry",
    "CustomEntry",
    "CustomMessageEntry",
    "LabelEntry",
    "SessionInfoEntry",
    "SessionEntry",
    "FileEntry",
    "_deserialize_content",
    "_serialize_content",
]
