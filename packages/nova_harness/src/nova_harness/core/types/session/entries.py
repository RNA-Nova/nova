"""
会话条目类型定义。

对应原 `nova_harness.session.types`，仅保留纯数据类型。
"""

from typing import Annotated, Any, List, Literal, Optional, Union

from nova_agent import CustomAgentMessage
from nova_ai import Message, ModelThinkingLevel
from nova_ai.types.base_model import NovaBaseModel
from nova_harness.core.types.messages import (
    CustomMessage,
    CustomMessageContent,
)
from nova_harness.core.types.session.constants import CURRENT_SESSION_VERSION
from pydantic import BeforeValidator, Field, SerializeAsAny, TypeAdapter


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


# 裸 dict 形态消息的严格校验器：只接受标准消息与扩展 custom 消息。
_MESSAGE_DICT_ADAPTER = TypeAdapter(Union[Message, CustomMessage])


def _validate_message_dict(value: Any) -> Any:
    """dict 形态的消息必须能验证为标准/扩展消息，否则抛错。

    包级用户工具消息（bashExecution 等）由解析层经注册表构造实例后进入
    本模型，不应以裸 dict 到达。没有这个守卫时，union 里无字段的
    ``CustomAgentMessage`` 基类会把任意 dict 静默吞成空消息
    （extra ignored）——畸形数据凭空消失。
    """
    if isinstance(value, dict):
        _MESSAGE_DICT_ADAPTER.validate_python(value)
    return value


class SessionMessageEntry(SessionEntryBase):
    """消息条目。

    message 的静态 union 只覆盖标准消息与扩展 custom 消息；包级用户工具的
    消息类（如 bashExecution）由解析层经 ``session/message_types`` 注册表
    复原后以**实例**形态进入。``SerializeAsAny`` 保证子类实例按自身
    schema 序列化（pydantic 默认按注解类型序列化会丢掉子类字段）。
    """

    type: Literal["message"] = "message"
    message: Annotated[
        Union["Message", "CustomMessage", SerializeAsAny[CustomAgentMessage]],
        BeforeValidator(_validate_message_dict),
    ]


class ThinkingLevelChangeEntry(SessionEntryBase):
    """思考级别变更条目"""

    type: Literal["thinking_level_change"] = "thinking_level_change"
    thinking_level: Optional[ModelThinkingLevel] = None


class ModelChangeEntry(SessionEntryBase):
    """模型变更条目"""

    type: Literal["model_change"] = "model_change"
    provider: str = ""
    model_id: str = ""


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


class CustomMessageEntry(SessionEntryBase):
    """自定义消息条目（参与LLM上下文）"""

    type: Literal["custom_message"] = "custom_message"
    custom_type: str = ""
    content: Union[str, List["CustomMessageContent"]] = Field(default_factory=list)
    details: Optional[Any] = None
    display: bool = True


# 会话条目联合类型
SessionEntry = Union[
    SessionMessageEntry,
    ThinkingLevelChangeEntry,
    ModelChangeEntry,
    CompactionEntry,
    BranchSummaryEntry,
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
    "CompactionEntry",
    "BranchSummaryEntry",
    "CustomEntry",
    "CustomMessageEntry",
    "LabelEntry",
    "SessionInfoEntry",
    "SessionEntry",
    "FileEntry",
]
