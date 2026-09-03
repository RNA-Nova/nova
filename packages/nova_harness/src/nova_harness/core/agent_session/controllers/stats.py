"""会话统计与上下文用量。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from nova_harness.core.harness.compaction.compaction import estimate_context_tokens
from nova_harness.core.harness.session.cache_stats import compute_cache_waste
from nova_harness.core.types.protocols import AgentSessionProtocol
from nova_harness.core.types.session.stats import (
    CacheWasteTotals,
    ModelPriceSource,
    SessionStats,
    SessionTokens,
)


class StatsCollector:
    """封装 AgentSession 的统计信息计算。"""

    def __init__(self, session: AgentSessionProtocol) -> None:
        self._session = session

    def get_cache_waste(
        self, models: Optional[ModelPriceSource] = None
    ) -> CacheWasteTotals:
        """返回会话级缓存浪费汇总（基于 session entries 扫描）。

        *models* 为定价查询源（通常是 ``ModelRuntime``）；缺省时
        missed_cost 以 0 计（定价未知）。
        """
        entries = self._session.session_manager.get_entries()
        return compute_cache_waste(entries, models)

    def get_context_usage(self) -> Optional[Dict[str, Any]]:
        """返回当前上下文 token 估算。"""
        model = self._session.model
        if model is None:
            return None
        context_window = getattr(model, "context_window", None) or 0
        if context_window <= 0:
            return None

        estimate = estimate_context_tokens(self._session.messages)
        percent = (estimate.tokens / context_window) * 100 if context_window else 0
        return {
            "tokens": estimate.tokens,
            # 线上 camel（RPC 透出形状——内部变量保持 snake）
            "contextWindow": context_window,
            "percent": percent,
        }

    def get_session_stats(self) -> SessionStats:
        """返回当前会话统计信息。"""
        messages = self._session.messages
        user_messages = sum(1 for m in messages if getattr(m, "role", None) == "user")
        assistant_messages = sum(
            1 for m in messages if getattr(m, "role", None) == "assistant"
        )
        tool_results = sum(
            1 for m in messages if getattr(m, "role", None) == "toolResult"
        )

        tool_calls = 0
        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_write = 0
        total_cost = 0.0

        for msg in messages:
            if getattr(msg, "role", None) == "assistant":
                content = getattr(msg, "content", []) or []
                tool_calls += sum(
                    1 for c in content if getattr(c, "type", None) == "toolCall"
                )
                usage = getattr(msg, "usage", None)
                if usage is not None:
                    total_input += getattr(usage, "input", 0) or 0
                    total_output += getattr(usage, "output", 0) or 0
                    total_cache_read += getattr(usage, "cache_read", 0) or 0
                    total_cache_write += getattr(usage, "cache_write", 0) or 0
                    cost = getattr(usage, "cost", None)
                    if cost is not None:
                        total_cost += getattr(cost, "total", 0.0) or 0.0

        return SessionStats(
            session_id=self._session.session_id,
            session_file=self._session.session_file,
            user_messages=user_messages,
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
            total_messages=len(messages),
            tokens=SessionTokens(
                input_tokens=total_input,
                output_tokens=total_output,
                cache_read=total_cache_read,
                cache_write=total_cache_write,
                total=total_input + total_output + total_cache_read + total_cache_write,
            ),
            cost=total_cost,
            context_usage=self.get_context_usage(),
        )
