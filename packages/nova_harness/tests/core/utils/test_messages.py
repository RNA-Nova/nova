"""
消息转换工具单元测试。
"""

from datetime import datetime, timezone
from typing import Literal

import pytest
from nova_agent import CustomAgentMessage
from nova_ai import AssistantMessage, ImageContent, TextContent, UserMessage
from nova_harness.core.types.messages import (
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
)
from nova_harness.core.utils.messages import (
    convert_to_llm,
    create_branch_summary_message,
    create_compaction_summary_message,
    create_custom_message,
)


class FakeInjectableMessage(CustomAgentMessage):
    """测试用 ContextInjectable 消息（包级用户工具消息的最小替身）。"""

    text: str = ""
    timestamp: int = 0
    exclude_from_context: bool = False
    role: Literal["fakeInjectable"] = "fakeInjectable"

    def to_context_text(self) -> str:
        return self.text


def test_create_branch_summary_message():
    ts = "2024-01-02T03:04:05.678Z"
    msg = create_branch_summary_message("summary", "from-1", ts)
    assert isinstance(msg, BranchSummaryMessage)
    assert msg.summary == "summary"
    assert msg.from_id == "from-1"
    expected_ms = int(
        datetime(2024, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc).timestamp() * 1000
    )
    assert msg.timestamp == expected_ms


def test_create_compaction_summary_message():
    ts = "2024-01-02T03:04:05+00:00"
    msg = create_compaction_summary_message("compact", 1000, ts)
    assert isinstance(msg, CompactionSummaryMessage)
    assert msg.tokens_before == 1000


def test_create_custom_message():
    ts = "2024-01-02T03:04:05Z"
    msg = create_custom_message("type-a", "hello", True, {"k": "v"}, ts)
    assert isinstance(msg, CustomMessage)
    assert msg.custom_type == "type-a"
    assert msg.content == "hello"
    assert msg.display is True
    assert msg.details == {"k": "v"}
    assert msg.timestamp is not None


def test_convert_to_llm_injectable():
    msg = FakeInjectableMessage(text="注入文本", timestamp=123)
    result = convert_to_llm([msg])
    assert len(result) == 1
    assert isinstance(result[0], UserMessage)
    assert result[0].role == "user"
    assert result[0].timestamp == 123
    assert result[0].content[0].text == "注入文本"


def test_convert_to_llm_injectable_excluded():
    msg = FakeInjectableMessage(
        text="不应出现",
        exclude_from_context=True,
        timestamp=1700000000000,
    )
    result = convert_to_llm([msg])
    assert len(result) == 0


def test_convert_to_llm_custom_context_injectable():
    """实现 ContextInjectable 协议的未知消息类型也能多态注入上下文。"""

    class SearchResultMessage:
        role = "searchResult"
        timestamp = 111
        exclude_from_context = False

        def to_context_text(self) -> str:
            return "Searched `nova`: 3 results"

    result = convert_to_llm([SearchResultMessage()])
    assert len(result) == 1
    assert isinstance(result[0], UserMessage)
    assert result[0].content == [
        TextContent(type="text", text="Searched `nova`: 3 results")
    ]
    assert result[0].timestamp == 111


def test_convert_to_llm_custom_context_injectable_excluded():
    class SearchResultMessage:
        role = "searchResult"
        timestamp = 111
        exclude_from_context = True

        def to_context_text(self) -> str:
            return "should not appear"

    result = convert_to_llm([SearchResultMessage()])
    assert len(result) == 0


def test_convert_to_llm_custom_string():
    msg = CustomMessage(
        custom_type="note", content="hello", display=True, timestamp=100
    )
    result = convert_to_llm([msg])
    assert len(result) == 1
    assert result[0].content == [TextContent(type="text", text="hello")]
    assert result[0].timestamp == 100


def test_convert_to_llm_custom_list():
    msg = CustomMessage(
        custom_type="note",
        content=[TextContent(type="text", text="hello")],
        display=False,
        timestamp=200,
    )
    result = convert_to_llm([msg])
    assert len(result) == 1
    assert result[0].content == [TextContent(type="text", text="hello")]
    assert result[0].timestamp == 200


def test_convert_to_llm_branch_summary():
    msg = BranchSummaryMessage(summary="s", from_id="f1", timestamp=456)
    result = convert_to_llm([msg])
    assert len(result) == 1
    assert result[0].role == "user"
    text = result[0].content[0].text
    assert "s" in text
    assert "summary of a branch" in text


def test_convert_to_llm_compaction_summary():
    msg = CompactionSummaryMessage(summary="cs", tokens_before=100, timestamp=789)
    result = convert_to_llm([msg])
    assert len(result) == 1
    text = result[0].content[0].text
    assert "cs" in text
    assert "compacted" in text


def test_convert_to_llm_standard_roles():
    user = UserMessage(role="user", content="hi", timestamp=10)
    assistant = AssistantMessage(
        role="assistant", content=[], provider="p", model="m", timestamp=20
    )
    tool = type("ToolResultMsg", (), {"role": "toolResult", "timestamp": 30})()
    result = convert_to_llm([user, assistant, tool])
    assert len(result) == 3
    assert result[0] is user
    assert result[1] is assistant
    assert result[2] is tool


def test_convert_to_llm_unknown_role_skipped():
    class UnknownMsg:
        role = "unknown"

    result = convert_to_llm([UnknownMsg()])
    assert len(result) == 0
