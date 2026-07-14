"""AgentSession 统计与状态类型。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field, model_validator


class SessionTokens(NovaBaseModel):
    """会话 token 统计。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0


class SessionStats(NovaBaseModel):
    """会话统计信息。"""

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


__all__ = ["SessionTokens", "SessionStats"]
