"""事件结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional, Union

from nova_agent import AgentMessage
from nova_ai import ImageContent, TextContent

from nova_harness.core.types.compaction import CompactionResult


@dataclass
class ContextEventResult:
    messages: Optional[List[AgentMessage]] = None


@dataclass
class ToolCallEventResult:
    block: bool = False
    reason: Optional[str] = None


@dataclass
class ToolResultEventResult:
    content: Optional[List[Union[TextContent, ImageContent]]] = None
    details: Any = None
    is_error: Optional[bool] = None


@dataclass
class MessageEndEventResult:
    message: Optional[AgentMessage] = None


@dataclass
class BeforeProviderRequestEventResult:
    payload: Any = None


@dataclass
class BeforeAgentStartEventResult:
    message: Optional[AgentMessage] = None
    system_prompt: Optional[str] = None


@dataclass
class SessionBeforeSwitchResult:
    cancel: bool = False


@dataclass
class SessionBeforeForkResult:
    cancel: bool = False
    skip_conversation_restore: bool = False


@dataclass
class SessionBeforeCompactResult:
    cancel: bool = False
    compaction: Optional[CompactionResult] = None


@dataclass
class SessionBeforeTreeResult:
    cancel: bool = False
    summary: Optional[str] = None
    details: Any = None
    custom_instructions: Optional[str] = None
    replace_instructions: bool = False
    label: Optional[str] = None


@dataclass
class UserBashEventResult:
    operations: Any = None
    result: Any = None


@dataclass
class InputEventResult:
    action: Literal["continue", "transform", "handled"] = "continue"
    text: Optional[str] = None
    images: Optional[List[ImageContent]] = None


@dataclass
class ResourcesDiscoverEventResult:
    skill_paths: List[str] = field(default_factory=list)
    prompt_paths: List[str] = field(default_factory=list)
    theme_paths: List[str] = field(default_factory=list)


@dataclass
class PrepareNextTurnEventResult:
    context: Any = None
    model: Any = None
    thinking_level: Any = None


@dataclass
class ShouldStopAfterTurnEventResult:
    stop: bool = False


@dataclass
class AfterProviderResponseEventResult:
    payload: Any = None


@dataclass
class ModelSelectEventResult:
    model: Any = None


@dataclass
class ThinkingLevelSelectEventResult:
    level: Any = None


@dataclass
class SessionShutdownEventResult:
    cancelled: bool = False
