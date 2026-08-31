"""Agent / turn / message / tool / 其他事件。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from nova_agent import AgentMessage
from nova_agent.types.events import _dump_agent_message
from nova_ai import AssistantMessage, ImageContent, ModelThinkingLevel, TextContent
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field, field_serializer

from .constants import (
    AFTER_PROVIDER_RESPONSE,
    AGENT_END,
    AGENT_SETTLED,
    AGENT_START,
    BEFORE_AGENT_START,
    BEFORE_PROVIDER_HEADERS,
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


class ContextEvent(NovaBaseModel):
    type: Literal["context"] = CONTEXT
    messages: List[AgentMessage] = Field(default_factory=list)
    signal: Any = None


class BeforeProviderRequestEvent(NovaBaseModel):
    type: Literal["before_provider_request"] = BEFORE_PROVIDER_REQUEST
    payload: Any = None


class BeforeProviderHeadersEvent(NovaBaseModel):
    """before_provider_headers 事件（provider 请求派发前，可改写请求头）。

    ``headers`` 契约（对齐 pi）：handler **原地修改** headers 字典（追加/改写/
    删除键），返回值被忽略；多个 handler 串行时后者在前者修改上继续；
    handler 异常 fail-open（转 extension_error 事件，请求继续）。
    """

    type: Literal["before_provider_headers"] = BEFORE_PROVIDER_HEADERS
    headers: Dict[str, str] = Field(default_factory=dict)


class AfterProviderResponseEvent(NovaBaseModel):
    type: Literal["after_provider_response"] = AFTER_PROVIDER_RESPONSE
    status: int = 0
    headers: Dict[str, str] = Field(default_factory=dict)
    model: Any = None


class BeforeAgentStartEvent(NovaBaseModel):
    type: Literal["before_agent_start"] = BEFORE_AGENT_START
    prompt: str = ""
    images: List[ImageContent] = Field(default_factory=list)
    system_prompt: Optional[str] = None
    system_prompt_options: Dict[str, Any] = Field(default_factory=dict)


class AgentStartEvent(NovaBaseModel):
    type: Literal["agent_start"] = AGENT_START
    # run 身份（作用域仲裁的归属键——挂起 UI 请求按 run:<run_id> 记账，
    # run 死即仲裁清扫；会话层在 agent_start 时生成）
    run_id: str = ""


class AgentEndEvent(NovaBaseModel):
    type: Literal["agent_end"] = AGENT_END
    messages: List[AgentMessage] = Field(default_factory=list)
    will_retry: bool = False
    # 终结的 run 身份（仲裁的清扫键——见 AgentStartEvent.run_id）
    run_id: str = ""


class AgentSettledEvent(NovaBaseModel):
    """agent_settled 事件：整个 run（含续话/队列 drain）结束后的"彻底安静"时刻。

    对齐 pi：``_run_agent_prompt`` 的 finally 中发射——正常结束、abort、
    异常路径均会发射（run 终结即 settled）；双发 Bus 2 与扩展面。
    """

    type: Literal["agent_settled"] = AGENT_SETTLED


class TurnStartEvent(NovaBaseModel):
    type: Literal["turn_start"] = TURN_START
    turn_index: int = 0
    timestamp: int = 0


class TurnEndEvent(NovaBaseModel):
    type: Literal["turn_end"] = TURN_END
    turn_index: int = 0
    message: Optional[AssistantMessage] = None
    tool_results: List[AgentMessage] = Field(default_factory=list)


class PrepareNextTurnEvent(NovaBaseModel):
    type: Literal["prepare_next_turn"] = PREPARE_NEXT_TURN
    message: Optional[AssistantMessage] = None
    tool_results: List[AgentMessage] = Field(default_factory=list)
    context: Any = None
    new_messages: List[AgentMessage] = Field(default_factory=list)


class ShouldStopAfterTurnEvent(NovaBaseModel):
    type: Literal["should_stop_after_turn"] = SHOULD_STOP_AFTER_TURN
    message: Optional[AssistantMessage] = None
    tool_results: List[AgentMessage] = Field(default_factory=list)
    context: Any = None
    new_messages: List[AgentMessage] = Field(default_factory=list)


class MessageStartEvent(NovaBaseModel):
    type: Literal["message_start"] = MESSAGE_START
    message: Optional[AgentMessage] = None

    @field_serializer("message")
    def _ser_message(self, value: Any) -> Any:
        return _dump_agent_message(value)


class MessageUpdateEvent(NovaBaseModel):
    type: Literal["message_update"] = MESSAGE_UPDATE
    message: Optional[AgentMessage] = None
    assistant_message_event: Any = None

    @field_serializer("message")
    def _ser_message(self, value: Any) -> Any:
        return _dump_agent_message(value)


class MessageEndEvent(NovaBaseModel):
    type: Literal["message_end"] = MESSAGE_END
    message: Optional[AgentMessage] = None

    @field_serializer("message")
    def _ser_message(self, value: Any) -> Any:
        return _dump_agent_message(value)


class ToolExecutionStartEvent(NovaBaseModel):
    type: Literal["tool_execution_start"] = TOOL_EXECUTION_START
    tool_call_id: str = ""
    tool_name: str = ""
    args: Any = None


class ToolExecutionUpdateEvent(NovaBaseModel):
    type: Literal["tool_execution_update"] = TOOL_EXECUTION_UPDATE
    tool_call_id: str = ""
    tool_name: str = ""
    args: Any = None
    partial_result: Any = None


class ToolExecutionEndEvent(NovaBaseModel):
    type: Literal["tool_execution_end"] = TOOL_EXECUTION_END
    tool_call_id: str = ""
    tool_name: str = ""
    result: Any = None
    is_error: bool = False


class ToolCallEvent(NovaBaseModel):
    """tool_call 事件（工具执行前，可拦截/改参）。

    ``args`` 契约（对齐 pi 的 input 原地改参）：handler 可**原地修改**
    ``args`` 字典，修改直送工具执行——事件对象与 loop 校验后的执行参数
    共享同一 dict（校验时已完成 deepcopy，原地改不会污染原始 tool_call）。
    多个 handler 串行时后者可见前者的修改；**修改后不再经二次 schema
    校验**——handler 是受信代码，自行保证参数合法。
    """

    type: Literal["tool_call"] = TOOL_CALL
    tool_call_id: str = ""
    tool_name: str = ""
    args: Any = None
    assistant_message: Optional[AssistantMessage] = None


class ToolResultEvent(NovaBaseModel):
    type: Literal["tool_result"] = TOOL_RESULT
    tool_call_id: str = ""
    tool_name: str = ""
    args: Any = None
    content: List[Union[TextContent, ImageContent]] = Field(default_factory=list)
    details: Any = None
    is_error: bool = False


class UserBashEvent(NovaBaseModel):
    type: Literal["user_bash"] = USER_BASH
    command: str = ""
    exclude_from_context: bool = False
    cwd: str = ""


class InputEvent(NovaBaseModel):
    type: Literal["input"] = INPUT
    text: str = ""
    images: List[ImageContent] = Field(default_factory=list)
    source: Literal["interactive", "rpc", "extension"] = "interactive"
    streaming_behavior: Optional[str] = None


class ModelSelectEvent(NovaBaseModel):
    type: Literal["model_select"] = MODEL_SELECT
    model: Any = None
    previous_model: Any = None
    source: str = ""


class ThinkingLevelSelectEvent(NovaBaseModel):
    type: Literal["thinking_level_select"] = THINKING_LEVEL_SELECT
    level: Optional[ModelThinkingLevel] = None
    previous_level: Optional[ModelThinkingLevel] = None


class ResourcesDiscoverEvent(NovaBaseModel):
    type: Literal["resources_discover"] = RESOURCES_DISCOVER
    cwd: str = ""
    reason: Literal["startup", "reload"] = "startup"


class ExtensionErrorEvent(NovaBaseModel):
    type: Literal["extension_error"] = EXTENSION_ERROR
    extension_path: str = ""
    event: str = ""
    error: str = ""
    stack: Optional[str] = None
