"""会话上下文类型。"""

from typing import List, Optional, Tuple

from nova_agent import AgentMessage
from nova_ai import ThinkingLevel
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field


class SessionContext(NovaBaseModel):
    """会话上下文"""

    messages: List[AgentMessage] = Field(default_factory=list)
    thinking_level: Optional[ThinkingLevel] = None
    model: Optional[Tuple[str, str]] = None  # (provider, model_id)
    active_tool_names: Optional[List[str]] = None


__all__ = ["SessionContext"]
