"""
StatsCollector 单元测试。

验证上下文用量与会话统计计算。
"""

from types import SimpleNamespace

import pytest
from nova_ai import AssistantMessage, Cost, TextContent, ToolCall, Usage, UserMessage


@pytest.fixture
def stats_session(make_agent_session):
    """构造一个用于测试 StatsCollector 的 session。"""
    return make_agent_session()


def test_get_context_usage_no_model(stats_session):
    """没有模型时返回 None。"""
    stats_session.agent.state.model = None
    assert stats_session._stats.get_context_usage() is None


def test_get_context_usage_no_context_window(stats_session):
    """context_window 无效时返回 None。"""
    stats_session.agent.state.model.context_window = 0
    assert stats_session._stats.get_context_usage() is None


def test_get_context_usage_returns_percent(stats_session):
    """应返回 token 数、窗口大小与百分比。"""
    stats_session.agent.state.model.context_window = 100
    stats_session.agent.state.messages = [
        UserMessage(role="user", content=[TextContent(type="text", text="abcd")])
    ]
    usage = stats_session._stats.get_context_usage()
    assert usage["context_window"] == 100
    assert usage["tokens"] == 1
    assert usage["percent"] == 1.0


def test_get_session_stats_counts_messages(stats_session):
    """应正确统计各类消息数量。"""
    stats_session.agent.state.messages = [
        UserMessage(role="user", content=[TextContent(text="hi")]),
        AssistantMessage(
            role="assistant",
            content=[
                TextContent(text="ok"),
                ToolCall(type="toolCall", id="1", name="bash", arguments={}),
            ],
            usage=Usage(
                input=10, output=5, cache_read=2, cache_write=1, cost=Cost(total=0.5)
            ),
        ),
        SimpleNamespace(role="toolResult", content=[]),
    ]
    stats = stats_session._stats.get_session_stats()
    assert stats.user_messages == 1
    assert stats.assistant_messages == 1
    assert stats.tool_calls == 1
    assert stats.tool_results == 1
    assert stats.total_messages == 4
    assert stats.tokens.input_tokens == 10
    assert stats.tokens.output_tokens == 5
    assert stats.tokens.cache_read == 2
    assert stats.tokens.cache_write == 1
    assert stats.tokens.total == 18
    assert stats.cost == 0.5


def test_get_session_stats_no_usage(stats_session):
    """没有 usage 时统计字段为 0。"""
    stats_session.agent.state.messages = [
        AssistantMessage(role="assistant", content=[TextContent(text="ok")])
    ]
    stats = stats_session._stats.get_session_stats()
    assert stats.tokens.total == 0
    assert stats.cost == 0.0


def test_get_session_stats_context_usage_included(stats_session):
    """session stats 应包含 context_usage。"""
    stats_session.agent.state.model.context_window = 1000
    stats_session.agent.state.messages = [
        UserMessage(role="user", content=[TextContent(text="x" * 100)])
    ]
    stats = stats_session._stats.get_session_stats()
    assert stats.context_usage is not None
    assert stats.context_usage["tokens"] == 25
