"""AgentSession 生命周期操作选项。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, List, Literal, Optional

from nova_ai import ImageContent
from nova_ai.types.base_model import NovaBaseModel


@dataclass
class PromptOptions:
    """prompt 调用选项。"""

    expand_prompt_templates: bool = True
    images: List["ImageContent"] = field(default_factory=list)
    streaming_behavior: Optional[str] = None
    source: Literal["interactive", "rpc", "extension"] = "interactive"
    preflight_result: Optional[Callable[[bool], None]] = field(default=None, repr=False)


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
    project_trust_context_factory: Optional[Callable[[str], Any]] = None


@dataclass
class ForkOptions:
    """Fork 会话的选项。"""

    position: Literal["before", "at"] = "before"
    with_session: Optional[Callable[[Any], Awaitable[None]]] = None


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
    replace_instructions: bool = False
    reserve_tokens: Optional[int] = None


__all__ = [
    "PromptOptions",
    "NewSessionOptions",
    "SwitchSessionOptions",
    "ForkOptions",
    "NavigateOptions",
]
