"""Agent / turn / message / tool / 其他事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

from nova_agent import AgentMessage
from nova_ai import AssistantMessage, ImageContent, TextContent, ThinkingLevel

from .constants import (
    AFTER_PROVIDER_RESPONSE,
    AGENT_END,
    AGENT_START,
    BEFORE_AGENT_START,
    BEFORE_PROVIDER_REQUEST,
    CONTEXT,
    EXTENSION_ERROR,
    INPUT,
    MESSAGE_END,
    MESSAGE_START,
    MESSAGE_UPDATE,
    MODEL_SELECT,
    PREPARE_NEXT_TURN,
    RESOURCES_DISCOVER,
    SHOULD_STOP_AFTER_TURN,
    THINKING_LEVEL_SELECT,
    TOOL_CALL,
    TOOL_EXECUTION_END,
    TOOL_EXECUTION_START,
    TOOL_EXECUTION_UPDATE,
    TOOL_RESULT,
    TURN_END,
    TURN_START,
    USER_BASH,
)


@dataclass
class ContextEvent:
    type: Literal["context"] = CONTEXT
    messages: List[AgentMessage] = field(default_factory=list)
    signal: Any = None


@dataclass
class BeforeProviderRequestEvent:
    type: Literal["before_provider_request"] = BEFORE_PROVIDER_REQUEST
    payload: Any = None


@dataclass
class AfterProviderResponseEvent:
    type: Literal["after_provider_response"] = AFTER_PROVIDER_RESPONSE
    status: int = 0
    headers: Dict[str, str] = field(default_factory=dict)
    model: Any = None


@dataclass
class BeforeAgentStartEvent:
    type: Literal["before_agent_start"] = BEFORE_AGENT_START
    prompt: str = ""
    images: List[ImageContent] = field(default_factory=list)
    system_prompt: Optional[str] = None
    system_prompt_options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentStartEvent:
    type: Literal["agent_start"] = AGENT_START


@dataclass
class AgentEndEvent:
    type: Literal["agent_end"] = AGENT_END
    messages: List[AgentMessage] = field(default_factory=list)
    will_retry: bool = False


@dataclass
class TurnStartEvent:
    type: Literal["turn_start"] = TURN_START
    turn_index: int = 0
    timestamp: int = 0


@dataclass
class TurnEndEvent:
    type: Literal["turn_end"] = TURN_END
    turn_index: int = 0
    message: Optional[AssistantMessage] = None
    tool_results: List[AgentMessage] = field(default_factory=list)


@dataclass
class PrepareNextTurnEvent:
    type: Literal["prepare_next_turn"] = PREPARE_NEXT_TURN
    message: Optional[AssistantMessage] = None
    tool_results: List[AgentMessage] = field(default_factory=list)
    context: Any = None
    new_messages: List[AgentMessage] = field(default_factory=list)


@dataclass
class ShouldStopAfterTurnEvent:
    type: Literal["should_stop_after_turn"] = SHOULD_STOP_AFTER_TURN
    message: Optional[AssistantMessage] = None
    tool_results: List[AgentMessage] = field(default_factory=list)
    context: Any = None
    new_messages: List[AgentMessage] = field(default_factory=list)


@dataclass
class MessageStartEvent:
    type: Literal["message_start"] = MESSAGE_START
    message: AgentMessage = field(default_factory=lambda: AgentMessage())


@dataclass
class MessageUpdateEvent:
    type: Literal["message_update"] = MESSAGE_UPDATE
    message: AgentMessage = field(default_factory=lambda: AgentMessage())
    assistant_message_event: Any = None


@dataclass
class MessageEndEvent:
    type: Literal["message_end"] = MESSAGE_END
    message: AgentMessage = field(default_factory=lambda: AgentMessage())


@dataclass
class ToolExecutionStartEvent:
    type: Literal["tool_execution_start"] = TOOL_EXECUTION_START
    tool_call_id: str = ""
    tool_name: str = ""
    args: Any = None


@dataclass
class ToolExecutionUpdateEvent:
    type: Literal["tool_execution_update"] = TOOL_EXECUTION_UPDATE
    tool_call_id: str = ""
    tool_name: str = ""
    args: Any = None
    partial_result: Any = None


@dataclass
class ToolExecutionEndEvent:
    type: Literal["tool_execution_end"] = TOOL_EXECUTION_END
    tool_call_id: str = ""
    tool_name: str = ""
    result: Any = None
    is_error: bool = False


@dataclass
class ToolCallEvent:
    type: Literal["tool_call"] = TOOL_CALL
    tool_call_id: str = ""
    tool_name: str = ""
    args: Any = None
    assistant_message: Optional[AssistantMessage] = None


@dataclass
class ToolResultEvent:
    type: Literal["tool_result"] = TOOL_RESULT
    tool_call_id: str = ""
    tool_name: str = ""
    args: Any = None
    content: List[Union[TextContent, ImageContent]] = field(default_factory=list)
    details: Any = None
    is_error: bool = False


@dataclass
class UserBashEvent:
    type: Literal["user_bash"] = USER_BASH
    command: str = ""
    exclude_from_context: bool = False
    cwd: str = ""


@dataclass
class InputEvent:
    type: Literal["input"] = INPUT
    text: str = ""
    images: List[ImageContent] = field(default_factory=list)
    source: Literal["interactive", "rpc", "extension"] = "interactive"
    streaming_behavior: Optional[str] = None


@dataclass
class ModelSelectEvent:
    type: Literal["model_select"] = MODEL_SELECT
    model: Any = None
    previous_model: Any = None
    source: str = ""


@dataclass
class ThinkingLevelSelectEvent:
    type: Literal["thinking_level_select"] = THINKING_LEVEL_SELECT
    level: Optional[ThinkingLevel] = None
    previous_level: Optional[ThinkingLevel] = None


@dataclass
class ResourcesDiscoverEvent:
    type: Literal["resources_discover"] = RESOURCES_DISCOVER
    cwd: str = ""
    reason: Literal["startup", "reload"] = "startup"


@dataclass
class ExtensionErrorEvent:
    type: Literal["extension_error"] = EXTENSION_ERROR
    extension_path: str = ""
    event: str = ""
    error: str = ""
    stack: Optional[str] = None
