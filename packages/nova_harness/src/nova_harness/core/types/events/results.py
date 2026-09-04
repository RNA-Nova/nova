"""事件结果类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Union

from nova_agent import AgentMessage
from nova_ai import ImageContent, TextContent
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

from nova_harness.core.types.compaction import CompactionResult


class ContextEventResult(NovaBaseModel):
    messages: Optional[List[AgentMessage]] = None


class ToolCallEventResult(NovaBaseModel):
    block: bool = False
    reason: Optional[str] = None


class ToolResultEventResult(NovaBaseModel):
    content: Optional[List[Union[TextContent, ImageContent]]] = None
    details: Any = None
    is_error: Optional[bool] = None


class MessageEndEventResult(NovaBaseModel):
    message: Optional[AgentMessage] = None


class BeforeProviderRequestEventResult(NovaBaseModel):
    payload: Any = None


class BeforeAgentStartEventResult(NovaBaseModel):
    message: Optional[AgentMessage] = None
    system_prompt: Optional[str] = None


class SessionBeforeSwitchResult(NovaBaseModel):
    cancel: bool = False


class SessionBeforeForkResult(NovaBaseModel):
    cancel: bool = False
    skip_conversation_restore: bool = False


class SessionBeforeCompactResult(NovaBaseModel):
    cancel: bool = False
    compaction: Optional[CompactionResult] = None


class SessionBeforeTreeResult(NovaBaseModel):
    cancel: bool = False
    summary: Optional[str] = None
    details: Any = None
    custom_instructions: Optional[str] = None
    replace_instructions: bool = False
    label: Optional[str] = None


@dataclass(frozen=True)
class UserBashEventResult:
    """``user_bash`` 拦截结果（hook 结果，不跨序列化边界）。

    ``operations`` 是扩展注入的活执行引擎（服务实例），按数据建模规则 4
    不进 Pydantic——本类型因此是 frozen dataclass 而非事件契约模型。
    """

    operations: Any = None
    result: Any = None


class InputEventResult(NovaBaseModel):
    action: Literal["continue", "transform", "handled"] = "continue"
    text: Optional[str] = None
    images: Optional[List[ImageContent]] = None


class ResourcesDiscoverEventResult(NovaBaseModel):
    skill_paths: List[str] = Field(default_factory=list)
    prompt_paths: List[str] = Field(default_factory=list)
    persona_paths: List[str] = Field(default_factory=list)


class PrepareNextTurnEventResult(NovaBaseModel):
    context: Any = None
    model: Any = None
    thinking_level: Any = None


class ShouldStopAfterTurnEventResult(NovaBaseModel):
    stop: bool = False


class AfterProviderResponseEventResult(NovaBaseModel):
    payload: Any = None


class ModelSelectEventResult(NovaBaseModel):
    model: Any = None


class ThinkingLevelSelectEventResult(NovaBaseModel):
    level: Any = None
