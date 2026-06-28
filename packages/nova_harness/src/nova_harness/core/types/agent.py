"""
AgentSession 配置与状态类型。

对应原 `nova_harness.agent.options` 中不依赖具体服务实例的纯数据类型。
`AgentSessionConfig` 因直接持有 `Agent`、`SessionManager` 等服务实例，
仍保留在 `agent/options.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from nova_ai import ImageContent, Model, ThinkingLevel
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field, model_validator


class ScopedModelConfig(NovaBaseModel):

    model: Model
    thinking_level: ThinkingLevel


class SessionTokens(NovaBaseModel):

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0


class PromptOptions(NovaBaseModel):

    expand_prompt_templates: bool = True
    images: List["ImageContent"] = Field(default_factory=list)
    streaming_behavior: Optional[str] = None
    source: Literal["interactive", "rpc", "extension"] = "interactive"
    preflight_result: Optional[Callable[[bool], None]] = Field(
        default=None, exclude=True
    )


@dataclass
class NewSessionOptions:
    """新建会话的选项。"""

    parent_session: Optional[str] = None
    setup: Optional[Callable[[Any], Awaitable[None]]] = None
    with_session: Optional[Callable[[Any], Awaitable[None]]] = None


@dataclass
class SwitchSessionOptions:
    """切换会话的选项。"""

    cwd_override: Optional[str] = None
    with_session: Optional[Callable[[Any], Awaitable[None]]] = None


@dataclass
class ForkOptions:
    """Fork 会话的选项。"""

    position: Literal["before", "at"] = "before"
    with_session: Optional[Callable[[Any], Awaitable[None]]] = None


class ModelCycleResult(NovaBaseModel):

    model: Model
    thinking_level: ThinkingLevel
    is_scoped: bool


class SessionStats(NovaBaseModel):

    session_id: str
    session_file: Optional[str] = None
    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    total_messages: int = 0
    tokens: SessionTokens = Field(default_factory=SessionTokens)
    cost: float = 0.0
    context_usage: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def compute_total_messages(self):
        # 使用 object.__setattr__ 避免 validate_assignment 触发递归
        object.__setattr__(
            self,
            "total_messages",
            self.user_messages
            + self.assistant_messages
            + self.tool_calls
            + self.tool_results,
        )
        return self


class NavigateOptions(NovaBaseModel):
    """
    会话树导航选项

    Attributes:
        summarize: 是否生成从当前位置到目标位置的分支摘要
        label: 为目标节点添加标签（仅在 summarize=False 时直接添加到目标节点）
        custom_instructions: 生成摘要时的自定义指令
        replace_instructions: 替换默认的摘要生成指令
        reserve_tokens: 为摘要预留的 token 数量（覆盖默认设置）
    """

    summarize: bool = False
    label: Optional[str] = None
    custom_instructions: Optional[str] = None
    replace_instructions: Optional[str] = None
    reserve_tokens: Optional[int] = None


__all__ = [
    "ScopedModelConfig",
    "SessionTokens",
    "PromptOptions",
    "NewSessionOptions",
    "SwitchSessionOptions",
    "ForkOptions",
    "ModelCycleResult",
    "SessionStats",
    "NavigateOptions",
]
