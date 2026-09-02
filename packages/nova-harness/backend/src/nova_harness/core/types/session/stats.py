"""AgentSession 统计与状态类型。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

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


class CacheMiss(NovaBaseModel):
    """单条 assistant 消息上计及的一次缓存 miss。"""

    # 上一轮 prompt 中已出现、本轮未从缓存读取的 token 数
    missed_tokens: int = 0
    # 相比全部命中缓存多付的美元；定价未知时为 0
    missed_cost: float = 0.0
    # 距上一次请求（最后一次刷新缓存）的毫秒数
    idle_ms: int = 0
    # 本轮模型是否相对上一请求发生了变化
    model_changed: bool = False


class CacheWasteTotals(NovaBaseModel):
    """会话级缓存浪费汇总。"""

    missed_tokens: int = 0
    missed_cost: float = 0.0
    # 计及的 miss 次数（超过噪声地板的轮数）
    miss_count: int = 0


class ModelPriceSource(Protocol):
    """最小定价查询接口，由 ``ModelRuntime`` 满足（``find`` 方法）。

    返回的 Model 的 ``cost.cache_read`` 为 $/百万 tokens。
    """

    def find(self, provider: str, model_id: str): ...


__all__ = [
    "SessionTokens",
    "SessionStats",
    "CacheMiss",
    "CacheWasteTotals",
    "ModelPriceSource",
]
