"""
会话模型工具单元测试。
"""

import asyncio
import json

import pytest
from nova_ai import AssistantMessage, TextContent, UserMessage

from nova_harness.core.harness.session.models import (
    _build_session_info_sync,
    build_session_info,
    list_sessions_from_dir,
)
from nova_harness.core.harness.session.utils import generate_session_id
from nova_harness.core.types.session import (
    SessionHeader,
    SessionInfo,
    SessionMessageEntry,
)


def _write_session(path, header, entries):
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(header.model_dump(), ensure_ascii=False) + "\n")
        for entry in entries:
            f.write(json.dumps(entry.model_dump(), ensure_ascii=False) + "\n")


def test_build_session_info_sync_with_messages(tmp_path):
    session_id = generate_session_id()
    header = SessionHeader(
        type="session",
        id=session_id,
        timestamp="2024-01-01T00:00:00",
        cwd="/tmp",
    )
    msg = UserMessage(role="user", content=[TextContent(type="text", text="hello")])
    entry = SessionMessageEntry(
        id="e1",
        parent_id=None,
        timestamp="2024-01-01T00:00:01",
        message=msg,
    )
    path = tmp_path / "s.jsonl"
    _write_session(path, header, [entry])

    info = _build_session_info_sync(str(path))
    assert isinstance(info, SessionInfo)
    assert info.id == session_id
    assert info.cwd == "/tmp"
    assert info.message_count == 1
    assert info.first_message == "hello"
    assert "hello" in info.all_messages_text


def test_build_session_info_sync_with_session_info_name(tmp_path):
    from nova_harness.core.types.session import SessionInfoEntry

    session_id = generate_session_id()
    header = SessionHeader(
        type="session",
        id=session_id,
        timestamp="2024-01-01T00:00:00",
        cwd="/tmp",
    )
    info_entry = SessionInfoEntry(
        id="i1", timestamp="2024-01-01T00:00:01", name="  My Session  "
    )
    msg = UserMessage(role="user", content="hello")
    msg_entry = SessionMessageEntry(
        id="e1", timestamp="2024-01-01T00:00:02", message=msg
    )
    path = tmp_path / "s.jsonl"
    _write_session(path, header, [info_entry, msg_entry])

    info = _build_session_info_sync(str(path))
    assert info.name == "My Session"


def test_build_session_info_sync_skips_non_user_assistant_roles(tmp_path):
    from nova_ai import ToolResultMessage

    session_id = generate_session_id()
    header = SessionHeader(
        type="session",
        id=session_id,
        timestamp="2024-01-01T00:00:00",
        cwd="/tmp",
    )
    tool_result = ToolResultMessage(role="toolResult", content=[], tool_call_id="tc1")
    entry = SessionMessageEntry(
        id="e1", timestamp="2024-01-01T00:00:01", message=tool_result
    )
    path = tmp_path / "s.jsonl"
    _write_session(path, header, [entry])

    info = _build_session_info_sync(str(path))
    assert info.message_count == 1
    assert info.first_message == "(no messages)"


def test_build_session_info_sync_skips_empty_text_content(tmp_path):
    from nova_ai import AssistantMessage

    session_id = generate_session_id()
    header = SessionHeader(
        type="session",
        id=session_id,
        timestamp="2024-01-01T00:00:00",
        cwd="/tmp",
    )
    assistant = AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text="")],
        provider="p",
        model="m",
    )
    entry = SessionMessageEntry(
        id="e1", timestamp="2024-01-01T00:00:01", message=assistant
    )
    path = tmp_path / "s.jsonl"
    _write_session(path, header, [entry])

    info = _build_session_info_sync(str(path))
    assert info.message_count == 1
    assert info.all_messages_text == ""


def test_build_session_info_sync_ignores_non_user_first(tmp_path):
    session_id = generate_session_id()
    header = SessionHeader(
        type="session",
        id=session_id,
        timestamp="2024-01-01T00:00:00",
        cwd="/tmp",
    )
    assistant = AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text="hi")],
        provider="p",
        model="m",
    )
    user = UserMessage(role="user", content=[TextContent(type="text", text="hello")])
    entries = [
        SessionMessageEntry(
            id="e1", timestamp="2024-01-01T00:00:01", message=assistant
        ),
        SessionMessageEntry(id="e2", timestamp="2024-01-01T00:00:02", message=user),
    ]
    path = tmp_path / "s.jsonl"
    _write_session(path, header, entries)

    info = _build_session_info_sync(str(path))
    assert info.first_message == "hello"
    assert "hi" in info.all_messages_text


def test_build_session_info_sync_empty_file(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert _build_session_info_sync(str(path)) is None


def test_build_session_info_sync_invalid_header(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"type": "message"}) + "\n", encoding="utf-8")
    assert _build_session_info_sync(str(path)) is None


def test_build_session_info_sync_first_entry_not_header(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps({"type": "custom", "id": "c1", "timestamp": "t", "custom_type": "x"})
        + "\n",
        encoding="utf-8",
    )
    assert _build_session_info_sync(str(path)) is None


def test_build_session_info_sync_exception_returns_none(tmp_path):
    path = tmp_path / "not-json.jsonl"
    path.write_text("not json", encoding="utf-8")
    assert _build_session_info_sync(str(path)) is None


@pytest.mark.asyncio
async def test_build_session_info_without_semaphore(tmp_path):
    session_id = generate_session_id()
    header = SessionHeader(
        type="session",
        id=session_id,
        timestamp="2024-01-01T00:00:00",
        cwd="/tmp",
    )
    msg = UserMessage(role="user", content="hello")
    entry = SessionMessageEntry(id="e1", timestamp="2024-01-01T00:00:01", message=msg)
    path = tmp_path / "s.jsonl"
    _write_session(path, header, [entry])

    info = await build_session_info(str(path))
    assert info.id == session_id


@pytest.mark.asyncio
async def test_build_session_info_with_semaphore(tmp_path):
    session_id = generate_session_id()
    header = SessionHeader(
        type="session",
        id=session_id,
        timestamp="2024-01-01T00:00:00",
        cwd="/tmp",
    )
    entry = SessionMessageEntry(
        id="e1",
        timestamp="2024-01-01T00:00:01",
        message=UserMessage(role="user", content="hello"),
    )
    path = tmp_path / "s.jsonl"
    _write_session(path, header, [entry])

    semaphore = asyncio.Semaphore(1)
    info = await build_session_info(str(path), semaphore)
    assert info.id == session_id


@pytest.mark.asyncio
async def test_list_sessions_from_dir_empty(tmp_path):
    result = await list_sessions_from_dir(str(tmp_path))
    assert result == []


@pytest.mark.asyncio
async def test_list_sessions_from_dir_with_progress(tmp_path):
    session_id = generate_session_id()
    header = SessionHeader(
        type="session",
        id=session_id,
        timestamp="2024-01-01T00:00:00",
        cwd="/tmp",
    )
    entry = SessionMessageEntry(
        id="e1",
        timestamp="2024-01-01T00:00:01",
        message=UserMessage(role="user", content="hello"),
    )
    _write_session(tmp_path / "a.jsonl", header, [entry])
    _write_session(tmp_path / "b.jsonl", header, [entry])

    progress = []

    def on_progress(current, total):
        progress.append((current, total))

    result = await list_sessions_from_dir(str(tmp_path), on_progress=on_progress)
    assert len(result) == 2
    assert progress == [(1, 2), (2, 2)]


@pytest.mark.asyncio
async def test_list_sessions_from_dir_ignores_exceptions(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_text("not json", encoding="utf-8")
    result = await list_sessions_from_dir(str(tmp_path))
    assert result == []


@pytest.mark.asyncio
async def test_list_sessions_from_dir_nonexistent():
    result = await list_sessions_from_dir("/tmp/nonexistent-dir-nova-test")
    assert result == []
