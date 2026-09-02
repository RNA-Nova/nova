"""
Agent 状态类型定义。

AgentState 是运行时可变状态容器，不是 JSON 边界类型，因此不使用 Pydantic，
而是用普通 class + property setter 实现 TS 侧 MutableAgentState 的拷贝语义。
"""

from typing import List, Optional, Set

from nova_ai import Model, ModelThinkingLevel

from .base import AgentMessage
from .tool import AgentTool

_PLACEHOLDER_MODEL_ID = "unknown"
"""占位模型的 id，用于识别"未显式配置模型"的状态。"""


def _default_placeholder_model() -> Model:
    """未配置模型时的占位模型，与 TS 的 DEFAULT_MODEL 对齐。

    该模型不能用于实际推理，仅保证 ``AgentState.model`` 永远不为 ``None``。
    真正调用 ``Agent.prompt`` 前必须通过 ``set_model`` 或 ``initial_state`` 指定有效模型。
    """
    return Model(
        id=_PLACEHOLDER_MODEL_ID,
        name="unknown",
        api="unknown",
        provider="unknown",
        base_url="",
        reasoning=False,
        input_types=["text"],
        cost={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        context_window=0,
        max_tokens=0,
    )


class AgentState:
    """Agent 运行时状态容器。

    设计对标 TS 的 ``MutableAgentState``：
    - ``tools`` / ``messages`` 通过 property setter 赋值时拷贝顶层数组，避免外部引用污染内部状态。
    - 其他字段直接暴露，允许运行时自由读写。
    """

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        model: Optional[Model] = None,
        thinking_level: Optional[ModelThinkingLevel] = None,
        tools: Optional[List[AgentTool]] = None,
        messages: Optional[List[AgentMessage]] = None,
        is_streaming: bool = False,
        streaming_message: Optional[AgentMessage] = None,
        pending_tool_calls: Optional[Set[str]] = None,
        error_message: Optional[str] = None,
    ):
        self.system_prompt: str = system_prompt or ""
        self.model: Model = model or _default_placeholder_model()
        self.thinking_level: ModelThinkingLevel = (
            thinking_level or ModelThinkingLevel.OFF
        )
        self._tools: List[AgentTool] = list(tools) if tools is not None else []
        self._messages: List[AgentMessage] = (
            list(messages) if messages is not None else []
        )
        self.is_streaming: bool = is_streaming
        self.streaming_message: Optional[AgentMessage] = streaming_message
        self.pending_tool_calls: Set[str] = (
            set(pending_tool_calls) if pending_tool_calls is not None else set()
        )
        self.error_message: Optional[str] = error_message

    @property
    def tools(self) -> List[AgentTool]:
        """可用工具列表。赋值时会拷贝顶层数组。"""
        return self._tools

    @tools.setter
    def tools(self, value: List[AgentTool]) -> None:
        self._tools = list(value)

    @property
    def messages(self) -> List[AgentMessage]:
        """对话消息列表。赋值时会拷贝顶层数组。"""
        return self._messages

    @messages.setter
    def messages(self, value: List[AgentMessage]) -> None:
        self._messages = list(value)

    def has_configured_model(self) -> bool:
        """是否已显式配置可用模型。

        未配置时 ``model`` 为占位模型（对齐 TS DEFAULT_MODEL），返回 False；
        ``Agent.prompt`` 据此做 fail-fast 检查。
        """
        return self.model.id != _PLACEHOLDER_MODEL_ID

    def __repr__(self) -> str:
        return (
            f"AgentState(system_prompt={self.system_prompt!r}, "
            f"model={self.model.id!r}, thinking_level={self.thinking_level!r}, "
            f"tools={len(self._tools)}, messages={len(self._messages)}, "
            f"is_streaming={self.is_streaming})"
        )


__all__ = ["AgentState"]
