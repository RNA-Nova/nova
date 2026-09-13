"""Agent 循环事件与 agent 消息词汇（批次 ②：原 ``nova_agent/types`` 的事件侧）。

收录理由：agent 事件经 RPC ``agent/event`` 上线、随会话落盘回放——跨组件
序列化词汇。运行时容器（``AgentContext``/``AgentState``）与行为契约
（``AgentTool``/hook 上下文）仍住 ``nova_agent``。

开放集（规则 6 + 第六节）：框架消息走 ``role`` 判别联合，包级自定义消息以
``CustomAgentMessage`` 兜底吸收（``extra="allow"`` 保数据不丢，包缺席的旧会话
降级为不透明消息）；包级消息类经 ``MESSAGE_TYPES`` 注册表回载为具体子类
（注册表住 nova_harness，枢纽只收基座）。

事件是不可变值对象：构造后只读，统一经 ``_AgentEventBase`` 锁定 frozen。
"""

from typing import Annotated, Any, Generic, List, Literal, Optional, TypeVar, Union

from pydantic import ConfigDict, Field, field_serializer

from .base_model import NovaBaseModel
from .content import ImageContent, TextContent
from .events import AssistantMessageEvent
from .messages import Message, ToolResultMessage


class CustomAgentMessage(NovaBaseModel):
    """包级自定义消息基座（开放集兜底成员）。

    子类约定：以包专属的 ``role`` 字面量覆盖默认值（如 ``"bashExecution"``），
    并声明自己的载荷字段。基座 ``extra="allow"``：回载时若包缺席，
    未知字段原样保留（降级为不透明消息，数据不丢）。

    ``role`` 恒存在（基座保证）——消费方可以直接 ``message.role``，
    无需 getattr 防御。
    """

    model_config = ConfigDict(extra="allow")

    role: str = "custom"


# AgentMessage 可以是标准框架消息或任意自定义消息。
# ``left_to_right``：框架判别联合（``role`` 判别）优先匹配；未知/畸形变体
# 落兜底成员吸收——前向兼容是契约义务（旧代码读新数据不炸、数据不丢）。
AgentMessage = Annotated[
    Union[Message, CustomAgentMessage],
    Field(union_mode="left_to_right"),
]
"""Agent 循环流动的消息类型（框架消息 ∪ 包级自定义消息）。"""


TDetails = TypeVar("TDetails")


class AgentToolResult(NovaBaseModel, Generic[TDetails]):
    """Result of a tool execution.

    工具结果是第三方产出（工具作者），故用 Pydantic 在边界尽早校验
    （规则 3）；内容随工具事件上线、经转换进 ToolResultMessage 落盘。

    - ``details``：工具自定义的呈现/日志载荷（UI 按工具解释）；形状归工具；
    - ``added_tool_names``：本次调用新注册的工具名（Kimi deferred tools）；
      ``None`` = 无新增；
    - ``terminate``：提示 agent 在本批工具调用后停止——仅当批内全部终态
      结果都置 True 才提前终止；``None`` = 不终止。
    """

    content: List[
        Annotated[Union[TextContent, ImageContent], Field(discriminator="type")]
    ]
    """Content blocks supporting text and images."""
    details: TDetails
    """Details to be displayed in a UI or logged."""
    added_tool_names: Optional[List[str]] = None
    """Names of tools introduced by this result and available from this transcript point onward."""
    terminate: Optional[bool] = None
    """
    Hint that the agent should stop after the current tool batch.
    Early termination only happens when every finalized tool result in the batch sets this to true.
    """
    is_error: bool = False
    """
    结果级错误标记（pi 对齐）：工具对**预期内失败**（非零退出、文件不存在、
    参数非法等）返回结果时置 True——驱动 toolResult.is_error 与 UI 错误卡片。
    异常路径（未捕获异常）由执行框架另行标记，不走此字段。
    """


def dump_agent_message(value: Any) -> Any:
    """按**运行时类型**序列化一条 agent 消息（纯转换——``to_*`` 类平凡行为）。

    ``AgentMessage = Union[Message, CustomAgentMessage]``——pydantic 按声明
    联合序列化时，兜底基座会把 ``BashExecutionMessage`` 等子类剥成基座字段
    （线上 custom 消息全灭：扩展注入消息、bash 用户工具终态卡都到不了前端）。
    任何序列化 ``AgentMessage`` 字段的地方都必须经本函数。
    """
    dump_wire = getattr(value, "dump_wire", None)
    if callable(dump_wire):
        return dump_wire()
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value


class _AgentEventBase(NovaBaseModel):
    """Agent 事件公共基座：事件是不可变值对象。"""

    model_config = ConfigDict(frozen=True)

    @field_serializer("message", "messages", check_fields=False)
    def _ser_messages(self, value: Any) -> Any:
        if isinstance(value, list):
            return [dump_agent_message(item) for item in value]
        return dump_agent_message(value)


class AgentStartEvent(_AgentEventBase):
    """一轮 agent 运行开始。"""

    type: Literal["agent_start"] = "agent_start"


class AgentEndEvent(_AgentEventBase):
    """一轮 agent 运行结束（``messages`` 为本轮新增消息）。"""

    type: Literal["agent_end"] = "agent_end"
    messages: List[AgentMessage]


class TurnStartEvent(_AgentEventBase):
    """一个 turn（一次模型请求 + 其工具执行）开始。"""

    type: Literal["turn_start"] = "turn_start"


class TurnEndEvent(_AgentEventBase):
    """一个 turn 结束（``message`` 为助手消息，``tool_results`` 为本轮工具结果）。"""

    type: Literal["turn_end"] = "turn_end"
    message: AgentMessage
    tool_results: List[ToolResultMessage]


class MessageStartEvent(_AgentEventBase):
    """一条消息开始进入上下文。"""

    type: Literal["message_start"] = "message_start"
    message: AgentMessage


class MessageUpdateEvent(_AgentEventBase):
    """助手消息流式更新（``assistant_message_event`` 携带底层增量事件）。"""

    type: Literal["message_update"] = "message_update"
    message: AgentMessage
    assistant_message_event: AssistantMessageEvent


class MessageEndEvent(_AgentEventBase):
    """一条消息终态落入上下文。"""

    type: Literal["message_end"] = "message_end"
    message: AgentMessage


class ToolExecutionStartEvent(_AgentEventBase):
    """工具调用开始执行（``args`` 为校验后的参数）。"""

    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str
    tool_name: str
    args: Any


class ToolExecutionUpdateEvent(_AgentEventBase):
    """工具执行中的进度更新（``partial_result`` 为工具自定义的半成品）。"""

    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str
    tool_name: str
    args: Any
    partial_result: Any


class ToolExecutionEndEvent(_AgentEventBase):
    """工具调用执行结束（``result`` 为 ``AgentToolResult`` 线上形态）。"""

    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool


# 全部 agent 事件的联合（判别键 ``type``——规则 6：随 RPC/会话回放走
# model_validate，显式判别）
AgentEvent = Annotated[
    Union[
        AgentStartEvent,
        AgentEndEvent,
        TurnStartEvent,
        TurnEndEvent,
        MessageStartEvent,
        MessageUpdateEvent,
        MessageEndEvent,
        ToolExecutionStartEvent,
        ToolExecutionUpdateEvent,
        ToolExecutionEndEvent,
    ],
    Field(discriminator="type"),
]
"""Agent 循环事件联合。"""


__all__ = [
    "AgentEndEvent",
    "AgentEvent",
    "AgentMessage",
    "AgentStartEvent",
    "AgentToolResult",
    "CustomAgentMessage",
    "dump_agent_message",
    "MessageEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "TDetails",
    "ToolExecutionEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "TurnEndEvent",
    "TurnStartEvent",
]
