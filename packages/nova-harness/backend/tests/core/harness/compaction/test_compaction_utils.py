"""
compaction/utils.py 单元测试。

覆盖文件操作提取、对话序列化、文件列表计算与格式化。
"""

from nova_ai import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from nova_harness.core.harness.compaction.utils import (
    compute_file_lists,
    create_file_ops,
    extract_file_ops_from_message,
    format_file_operations,
    serialize_conversation,
)


def _user(text: str) -> UserMessage:
    return UserMessage(role="user", content=[TextContent(type="text", text=text)])


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text=text)],
        provider="test",
        model="test",
        stop_reason="stop",
    )


def _tool_call(name: str, path: str) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[
            ToolCall(
                type="toolCall",
                name=name,
                arguments={"path": path},
            )
        ],
        provider="test",
        model="test",
        stop_reason="toolUse",
    )


# -----------------------------------------------------------------------------
# extract_file_ops_from_message
# -----------------------------------------------------------------------------


def test_extract_file_ops_read_write_edit():
    file_ops = create_file_ops()

    extract_file_ops_from_message(_tool_call("read", "/tmp/a.py"), file_ops)
    assert "/tmp/a.py" in file_ops.read

    extract_file_ops_from_message(_tool_call("write", "/tmp/b.py"), file_ops)
    assert "/tmp/b.py" in file_ops.written

    extract_file_ops_from_message(_tool_call("edit", "/tmp/c.py"), file_ops)
    assert "/tmp/c.py" in file_ops.edited


def test_extract_file_ops_ignores_non_assistant():
    file_ops = create_file_ops()
    extract_file_ops_from_message(_user("hello"), file_ops)
    assert not file_ops.read
    assert not file_ops.written
    assert not file_ops.edited


def test_extract_file_ops_missing_path():
    class FakeBlock:
        type = "toolCall"
        name = "read"
        arguments = {}

    class FakeMessage:
        role = "assistant"
        content = [FakeBlock()]

    file_ops = create_file_ops()
    extract_file_ops_from_message(FakeMessage(), file_ops)  # type: ignore[arg-type]
    assert not file_ops.read


def test_extract_file_ops_unknown_tool_name():
    class FakeBlock:
        type = "toolCall"
        name = "bash"
        arguments = {"path": "/tmp/x.py"}

    class FakeMessage:
        role = "assistant"
        content = [FakeBlock()]

    file_ops = create_file_ops()
    extract_file_ops_from_message(FakeMessage(), file_ops)  # type: ignore[arg-type]
    assert not file_ops.read
    assert not file_ops.written
    assert not file_ops.edited


# -----------------------------------------------------------------------------
# serialize_conversation
# -----------------------------------------------------------------------------


def test_serialize_conversation_includes_thinking_and_tool_calls():
    user = _user("hello")
    assistant = AssistantMessage(
        role="assistant",
        content=[
            ThinkingContent(type="thinking", thinking="think deep"),
            TextContent(type="text", text="answer"),
            ToolCall(type="toolCall", name="read", arguments={"path": "/tmp/a.py"}),
        ],
        provider="test",
        model="test",
        stop_reason="stop",
    )

    text = serialize_conversation([user, assistant])
    assert "[User]: hello" in text
    assert "[Assistant thinking]: think deep" in text
    assert "[Assistant]: answer" in text
    assert '[Assistant tool calls]: read(path="/tmp/a.py")' in text


def test_serialize_conversation_truncates_long_tool_result():
    long_text = "x" * 3000
    tool_result = ToolResultMessage(
        role="toolResult",
        content=[TextContent(type="text", text=long_text)],
        tool_call_id="tc1",
        tool_name="read",
    )

    text = serialize_conversation([tool_result])
    assert text.startswith("[Tool result]:")
    assert "[... 1000 more characters truncated]" in text
    assert len(text) < len(long_text) + 100


# -----------------------------------------------------------------------------
# compute_file_lists & format_file_operations
# -----------------------------------------------------------------------------


def test_compute_file_lists_sorts_and_deduplicates():
    file_ops = create_file_ops()
    file_ops.read.update(["/b.py", "/a.py"])
    file_ops.edited.add("/b.py")
    file_ops.written.add("/c.py")

    read_files, modified_files = compute_file_lists(file_ops)
    assert read_files == ["/a.py"]
    assert modified_files == ["/b.py", "/c.py"]


def test_compute_file_lists_empty():
    read_files, modified_files = compute_file_lists(create_file_ops())
    assert read_files == []
    assert modified_files == []


def test_format_file_operations_empty():
    assert format_file_operations([], []) == ""


def test_format_file_operations_both_sections():
    text = format_file_operations(["/a.py"], ["/b.py"])
    assert "<read-files>" in text
    assert "<modified-files>" in text
    assert "/a.py" in text
    assert "/b.py" in text
