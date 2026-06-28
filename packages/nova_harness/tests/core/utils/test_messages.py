"""
消息转换工具单元测试。
"""

from datetime import datetime, timezone

import pytest
from nova_ai import AssistantMessage, ImageContent, TextContent, UserMessage

from nova_harness.core.types.messages import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    FileContent,
)
from nova_harness.core.utils.messages import (
    bash_execution_to_text,
    convert_content,
    convert_to_llm,
    create_branch_summary_message,
    create_compaction_summary_message,
    create_custom_message,
)


def test_bash_execution_to_text_with_output():
    msg = BashExecutionMessage(
        command="ls",
        output="a\nb",
        exit_code=0,
        cancelled=False,
        truncated=False,
    )
    text = bash_execution_to_text(msg)
    assert "Ran `ls`" in text
    assert "```\na\nb\n```" in text


def test_bash_execution_to_text_no_output():
    msg = BashExecutionMessage(
        command="ls",
        output="",
        exit_code=0,
        cancelled=False,
        truncated=False,
    )
    text = bash_execution_to_text(msg)
    assert "(no output)" in text


def test_bash_execution_to_text_cancelled():
    msg = BashExecutionMessage(
        command="sleep 10",
        output="",
        exit_code=None,
        cancelled=True,
        truncated=False,
    )
    text = bash_execution_to_text(msg)
    assert "(command cancelled)" in text


def test_bash_execution_to_text_nonzero_exit():
    msg = BashExecutionMessage(
        command="false",
        output="",
        exit_code=1,
        cancelled=False,
        truncated=False,
    )
    text = bash_execution_to_text(msg)
    assert "Command exited with code 1" in text


def test_bash_execution_to_text_truncated():
    msg = BashExecutionMessage(
        command="cat big.log",
        output="...",
        exit_code=0,
        cancelled=False,
        truncated=True,
        full_output_path="/tmp/big.log",
    )
    text = bash_execution_to_text(msg)
    assert "[Output truncated. Full output: /tmp/big.log]" in text


def test_convert_content_string():
    assert convert_content("hello") == "hello"


def test_convert_content_mixed_list():
    file_item = FileContent(
        filename="x.txt", path="/tmp/x.txt", mime_type="text/plain", size=42
    )
    text_item = TextContent(type="text", text="hello")
    image_item = ImageContent(type="image", url="http://example.com/img.png")

    result = convert_content([text_item, file_item, image_item])
    assert len(result) == 3
    assert result[0] == text_item
    assert result[2] == image_item
    assert result[1].type == "text"
    assert "x.txt" in result[1].text
    assert "42" in result[1].text


def test_convert_content_file_without_size():
    file_item = FileContent(filename="y.txt", path="/tmp/y.txt", mime_type="text/plain")
    result = convert_content([file_item])
    assert result[0].type == "text"
    assert "unknown" in result[0].text


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


@pytest.mark.asyncio
async def test_convert_to_llm_bash_execution():
    msg = BashExecutionMessage(
        command="ls",
        output="a",
        exit_code=0,
        cancelled=False,
        truncated=False,
        timestamp=123,
    )
    result = await convert_to_llm([msg])
    assert len(result) == 1
    assert isinstance(result[0], UserMessage)
    assert result[0].role == "user"
    assert result[0].timestamp == 123


@pytest.mark.asyncio
async def test_convert_to_llm_bash_execution_excluded():
    msg = BashExecutionMessage(
        command="ls",
        output="a",
        exit_code=0,
        cancelled=False,
        truncated=False,
        exclude_from_context=True,
    )
    result = await convert_to_llm([msg])
    assert len(result) == 0


@pytest.mark.asyncio
async def test_convert_to_llm_custom_string():
    msg = CustomMessage(
        custom_type="note", content="hello", display=True, timestamp=100
    )
    result = await convert_to_llm([msg])
    assert len(result) == 1
    assert result[0].content == [TextContent(type="text", text="hello")]
    assert result[0].timestamp == 100


@pytest.mark.asyncio
async def test_convert_to_llm_custom_list():
    msg = CustomMessage(
        custom_type="note",
        content=[TextContent(type="text", text="hello")],
        display=False,
        timestamp=200,
    )
    result = await convert_to_llm([msg])
    assert len(result) == 1
    assert result[0].content == [TextContent(type="text", text="hello")]
    assert result[0].timestamp == 200


@pytest.mark.asyncio
async def test_convert_to_llm_branch_summary():
    msg = BranchSummaryMessage(summary="s", from_id="f1", timestamp=456)
    result = await convert_to_llm([msg])
    assert len(result) == 1
    assert result[0].role == "user"
    text = result[0].content[0].text
    assert "s" in text
    assert "summary of a branch" in text


@pytest.mark.asyncio
async def test_convert_to_llm_compaction_summary():
    msg = CompactionSummaryMessage(summary="cs", tokens_before=100, timestamp=789)
    result = await convert_to_llm([msg])
    assert len(result) == 1
    text = result[0].content[0].text
    assert "cs" in text
    assert "compacted" in text


@pytest.mark.asyncio
async def test_convert_to_llm_standard_roles():
    user = UserMessage(role="user", content="hi", timestamp=10)
    assistant = AssistantMessage(
        role="assistant", content=[], provider="p", model="m", timestamp=20
    )
    tool = type("ToolResultMsg", (), {"role": "toolResult", "timestamp": 30})()
    result = await convert_to_llm([user, assistant, tool])
    assert len(result) == 3
    assert result[0] is user
    assert result[1] is assistant
    assert result[2] is tool


@pytest.mark.asyncio
async def test_convert_to_llm_unknown_role_skipped():
    class UnknownMsg:
        role = "unknown"

    result = await convert_to_llm([UnknownMsg()])
    assert len(result) == 0
