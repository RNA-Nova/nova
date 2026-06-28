"""
Session 模块全面测试：覆盖 entry 生命周期、分支导航、文件持久化、
序列化、context 重建以及各种边界情况。
"""

import os
import tempfile

import pytest
from nova_ai import AssistantMessage, TextContent, ThinkingLevel, UserMessage

from nova_harness.core.harness.session import (
    SessionManager,
    build_session_context,
    load_entries_from_file,
)


def _user(text: str) -> UserMessage:
    return UserMessage(role="user", content=[TextContent(type="text", text=text)])


def _assistant(text: str, model: str = "test-model") -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text=text)],
        provider="test",
        model=model,
        stop_reason="stop",
    )


@pytest.fixture
def session():
    """内存 SessionManager。"""
    return SessionManager(
        cwd="/tmp", session_dir="/tmp/nova-test", session_file=None, persist=False
    )


# -----------------------------------------------------------------------------
# 基础 entry 与 context
# -----------------------------------------------------------------------------


def test_new_session_has_version_3_and_session_id(session):
    header = session.get_header()
    assert header.version == 3
    assert len(header.id.replace("-", "")) == 32


def test_append_message_updates_leaf(session):
    msg_id = session.append_message(_assistant("hi"))  # noqa: F841
    assert session.get_leaf_id() == msg_id
    leaf_entry = session.get_leaf_entry()
    assert leaf_entry is not None
    assert leaf_entry.type == "message"


def test_append_thinking_level_change_updates_context(session):
    a1 = session.append_message(_assistant("a"))  # noqa: F841
    session.append_thinking_level_change(ThinkingLevel.HIGH)
    ctx = session.build_session_context()
    assert ctx.thinking_level == ThinkingLevel.HIGH


def test_append_model_change_updates_context(session):
    a2 = session.append_message(_assistant("a"))  # noqa: F841
    session.append_model_change("openai", "gpt-4")
    ctx = session.build_session_context()
    assert ctx.model == ("openai", "gpt-4")


def test_append_active_tools_change_updates_context(session):
    a3 = session.append_message(_assistant("a"))  # noqa: F841
    session.append_active_tools_change(["read", "bash"])
    ctx = session.build_session_context()
    assert ctx.active_tool_names == ["read", "bash"]


def test_append_custom_entry_ignored_by_context(session):
    a4 = session.append_message(_assistant("a"))  # noqa: F841
    session.append_custom_entry("plugin_state", {"key": "value"})
    ctx = session.build_session_context()
    assert len(ctx.messages) == 1


def test_append_custom_message_entry_included_in_context(session):
    a5 = session.append_message(_assistant("a"))  # noqa: F841
    session.append_custom_message_entry("note", "custom content", display=True)
    ctx = session.build_session_context()
    assert len(ctx.messages) == 2
    assert ctx.messages[1].role == "custom"


def test_label_change_tracks_and_removes_labels(session):
    msg_id = session.append_message(_assistant("a"))  # noqa: F841
    session.append_label_change(msg_id, "important")
    assert session.get_label(msg_id) == "important"

    session.append_label_change(msg_id, None)
    assert session.get_label(msg_id) is None


# -----------------------------------------------------------------------------
# leaf 与分支
# -----------------------------------------------------------------------------


def test_branch_writes_leaf_entry_and_redirects(session):
    msg_id = session.append_message(_assistant("a"))  # noqa: F841
    session.branch(msg_id)

    leaf_entries = [e for e in session.get_entries() if e.type == "leaf"]
    assert len(leaf_entries) == 1
    assert leaf_entries[0].target_id == msg_id
    assert session.get_leaf_id() == msg_id


def test_multiple_branches_latest_leaf_wins(session):
    m1 = session.append_message(_assistant("a1"))
    m2 = session.append_message(_assistant("a2"))
    session.branch(m1)
    session.branch(m2)

    # 两次 branch 写两个 leaf entry，当前 leaf 应为最后一次 target
    assert session.get_leaf_id() == m2


def test_reset_leaf_persists_null_target(session):
    a8 = session.append_message(_assistant("a"))  # noqa: F841
    session.reset_leaf()

    leaf_entries = [e for e in session.get_entries() if e.type == "leaf"]
    assert len(leaf_entries) == 1
    assert leaf_entries[0].target_id is None
    assert session.get_leaf_id() is None


def test_branch_with_summary_writes_leaf_then_summary(session):
    m1 = session.append_message(_assistant("a"))
    summary_id = session.branch_with_summary(m1, "summary text")

    entries = session.get_entries()
    types = [e.type for e in entries]
    assert types.count("leaf") == 1
    assert types.count("branch_summary") == 1

    summary = session.get_entry(summary_id)
    assert summary.parent_id == m1
    assert summary.from_id == m1
    # 当前 leaf 是 summary entry
    assert session.get_leaf_id() == summary_id


def test_branch_with_summary_from_root(session):
    a10 = session.append_message(_assistant("a"))  # noqa: F841
    summary_id = session.branch_with_summary(None, "back to root")
    summary = session.get_entry(summary_id)
    assert summary.parent_id is None
    assert summary.from_id == "root"


def test_get_branch_returns_path_to_leaf(session):
    m1 = session.append_message(_assistant("a1"))
    m2 = session.append_message(_assistant("a2"))
    session.append_message(_assistant("a3"))
    session.branch(m2)

    branch = session.get_branch()
    ids = [e.id for e in branch]
    assert ids == [m1, m2]


# -----------------------------------------------------------------------------
# 文件持久化与重开
# -----------------------------------------------------------------------------


def test_persist_and_reopen_keeps_entries():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        a14 = manager.append_message(_assistant("a1"))  # noqa: F841
        manager.append_thinking_level_change(ThinkingLevel.LOW)
        manager.append_model_change("openai", "gpt-4")
        manager.append_active_tools_change(["read"])
        path = manager.get_session_file()

        reopened = SessionManager.open(path)
        assert reopened.get_header().version == 3
        assert reopened.get_leaf_id() == manager.get_leaf_id()

        ctx = reopened.build_session_context()
        assert ctx.thinking_level == ThinkingLevel.LOW
        assert ctx.model == ("openai", "gpt-4")
        assert ctx.active_tool_names == ["read"]


def test_persist_and_reopen_leaf_redirect():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        m1 = manager.append_message(_assistant("a1"))
        manager.append_message(_assistant("a2"))
        manager.branch(m1)

        reopened = SessionManager.open(manager.get_session_file())
        assert reopened.get_leaf_id() == m1


def test_rewrite_after_first_assistant_flushes_all_entries():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        manager.append_thinking_level_change(ThinkingLevel.MEDIUM)
        # 此时不应已落盘（无 assistant）
        assert not manager._flushed
        a17 = manager.append_message(_assistant("a"))  # noqa: F841
        # 有 assistant 后触发 flush
        assert manager._flushed
        assert os.path.exists(manager.get_session_file())


# -----------------------------------------------------------------------------
# fork / branched session
# -----------------------------------------------------------------------------


def test_create_branched_session_copies_path():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        m1 = manager.append_message(_assistant("a1"))
        manager.append_message(_assistant("a2"))
        original_file = manager.get_session_file()

        new_path = manager.create_branched_session(m1)
        assert new_path is not None
        assert os.path.exists(new_path)

        reopened = SessionManager.open(new_path)
        assert reopened.get_header().parent_session == original_file
        assert reopened.get_leaf_id() == m1


def test_fork_from_copies_entries():
    with tempfile.TemporaryDirectory() as tmp:
        source = SessionManager.create(cwd="/tmp", session_dir=tmp)
        source.append_message(_assistant("a1"))
        source.append_message(_assistant("a2"))
        source_path = source.get_session_file()

        forked = SessionManager.fork_from(source_path, target_cwd="/workspace")
        assert forked.get_header().cwd == "/workspace"
        assert forked.get_header().parent_session == source_path
        assert len(forked.get_entries()) == 2


# -----------------------------------------------------------------------------
# build_session_context 边界
# -----------------------------------------------------------------------------


def test_build_context_empty_entries():
    ctx = build_session_context([])
    assert ctx.messages == []
    assert ctx.model is None
    assert ctx.active_tool_names is None


def test_build_context_with_unknown_entry_type_ignored(session):
    a20 = session.append_message(_assistant("a"))  # noqa: F841
    session.append_session_info("my session")
    ctx = session.build_session_context()
    assert len(ctx.messages) == 1


def test_build_context_model_from_latest_source(session):
    u1 = session.append_message(_user("q1"))  # noqa: F841
    a21 = session.append_message(_assistant("a1", model="m1"))  # noqa: F841
    session.append_model_change("openai", "gpt-4")
    a22 = session.append_message(_assistant("a2", model="m2"))  # noqa: F841

    ctx = session.build_session_context()
    # assistant message 更新 model 为 m2
    assert ctx.model == ("test", "m2")


def test_build_context_compaction_then_messages(session):
    u2 = session.append_message(_user("first"))  # noqa: F841
    a23 = session.append_message(_assistant("answer1"))  # noqa: F841
    u3 = session.append_message(_user("second"))  # noqa: F841
    a24 = session.append_message(_assistant("answer2"))  # noqa: F841
    session.append_compaction("summary", u2, 200)
    u4 = session.append_message(_user("third"))  # noqa: F841
    a25 = session.append_message(_assistant("answer3"))  # noqa: F841

    ctx = session.build_session_context()
    roles = [getattr(m, "role", None) for m in ctx.messages]
    assert "compactionSummary" in roles
    assert any(r == "user" for r in roles)


# -----------------------------------------------------------------------------
# 序列化字段
# -----------------------------------------------------------------------------


def test_model_dump_uses_snake_case(session):
    a26 = session.append_message(_assistant("a"))  # noqa: F841
    session.append_model_change("openai", "gpt-4")
    entries = session.get_entries()
    data = entries[-1].model_dump()
    assert "model_id" in data
    assert "modelId" not in data


def test_load_entries_from_file_with_snake_case():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        a27 = manager.append_message(_assistant("a"))  # noqa: F841
        path = manager.get_session_file()

        entries = load_entries_from_file(path)
        # load_entries_from_file 返回 header + entries
        message_entries = [e for e in entries if e.type == "message"]
        assert len(message_entries) == 1


# -----------------------------------------------------------------------------
# 错误边界
# -----------------------------------------------------------------------------


def test_branch_to_nonexistent_entry_raises(session):
    with pytest.raises(ValueError):
        session.branch("nonexistent")


def test_branch_with_summary_to_nonexistent_entry_raises(session):
    with pytest.raises(ValueError):
        session.branch_with_summary("nonexistent", "summary")


def test_label_change_to_nonexistent_entry_raises(session):
    with pytest.raises(ValueError):
        session.append_label_change("nonexistent", "label")


def test_session_manager_open_invalid_file_creates_new():
    with tempfile.TemporaryDirectory() as tmp:
        invalid_path = os.path.join(tmp, "not_a_session.jsonl")
        with open(invalid_path, "w") as f:
            f.write("not json\n")

        manager = SessionManager.open(invalid_path)
        assert manager.get_session_file() == invalid_path
        assert manager.get_header() is not None
