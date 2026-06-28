"""
Agent 状态类型定义。
"""

from typing import Any, List, Optional, Set

from pydantic import Field

from nova_ai import Model, ThinkingLevel
from nova_ai.types.base_model import NovaBaseModel

from .base import AgentMessage


class AgentState(NovaBaseModel):
    """Agent state containing all configuration and conversation data."""

    system_prompt: Optional[str] = ""
    model: Optional[Model] = None
    thinking_level: Optional[ThinkingLevel] = None
    tools: List[Any] = Field(default_factory=list)
    messages: List[AgentMessage] = Field(default_factory=list)
    is_streaming: bool = False
    streaming_message: Optional[AgentMessage] = None
    pending_tool_calls: Set[str] = Field(default_factory=set)
    error_message: Optional[str] = None


__all__ = ["AgentState"]
