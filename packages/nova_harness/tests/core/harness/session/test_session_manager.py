"""
SessionManager 单元测试：验证 leaf 持久化、active_tools_change、context 构建。
"""

import tempfile

import pytest

from nova_harness.core.harness.session import SessionManager


@pytest.fixture
def session_manager():
    """创建一个内存中的 SessionManager（persist=False）。"""
    return SessionManager(
        cwd="/tmp", session_dir="/tmp/nova-test", session_file=None, persist=False
    )


def test_append_active_tools_change_updates_context(session_manager):
    """append_active_tools_change 应被 build_session_context 识别。"""
    session_manager.append_message(__assistant_message("hello"))
    session_manager.append_active_tools_change(["read", "bash"])

    ctx = session_manager.build_session_context()
    assert ctx.active_tool_names == ["read", "bash"]


def test_leaf_entry_persists_branch(session_manager):
    """branch() 应写入 leaf entry 并正确恢复 leaf。"""
    msg_id = session_manager.append_message(__assistant_message("hello"))
    session_manager.branch(msg_id)

    entries = session_manager.get_entries()
    leaf_entries = [e for e in entries if e.type == "leaf"]
    assert len(leaf_entries) == 1
    assert leaf_entries[0].target_id == msg_id
    assert session_manager.get_leaf_id() == msg_id


def test_reset_leaf_persists_null_target(session_manager):
    """reset_leaf() 应写入 target_id 为 null 的 leaf entry。"""
    session_manager.append_message(__assistant_message("hello"))
    session_manager.reset_leaf()

    leaf_entries = [e for e in session_manager.get_entries() if e.type == "leaf"]
    assert len(leaf_entries) == 1
    assert leaf_entries[0].target_id is None
    assert session_manager.get_leaf_id() is None


def test_branch_with_summary_writes_leaf_then_summary(session_manager):
    """branch_with_summary 应先写 leaf entry 再写 branch_summary。"""
    msg_id = session_manager.append_message(__assistant_message("hello"))
    summary_id = session_manager.branch_with_summary(msg_id, "summary")

    entries = session_manager.get_entries()
    types = [e.type for e in entries]
    assert "leaf" in types
    assert "branch_summary" in types

    summary = session_manager.get_entry(summary_id)
    assert summary.parent_id == msg_id


def test_session_file_roundtrip_uses_camelcase():
    """持久化到文件后应为 camelCase，且 reopen 后 leaf 恢复正确。"""
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        msg_id = manager.append_message(__assistant_message("hello"))
        manager.branch(msg_id)
        manager.append_active_tools_change(["read"])
        file_path = manager.get_session_file()

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

        # 头部使用 snake_case parent_session
        header = lines[0]
        assert '"parent_session"' in header or '"cwd"' in header

        # leaf entry 使用 snake_case target_id
        leaf_line = next(line for line in lines if '"type": "leaf"' in line)
        assert '"target_id"' in leaf_line

        # active_tools_change 使用 snake_case active_tool_names
        tools_line = next(
            line for line in lines if '"type": "active_tools_change"' in line
        )
        assert '"active_tool_names"' in tools_line

        reopened = SessionManager.open(file_path)
        # active_tools_change 是 leaf entry 之后最新追加的普通 entry，当前 leaf 应为它
        leaf_entry = reopened.get_leaf_entry()
        assert leaf_entry is not None
        assert leaf_entry.type == "active_tools_change"
        ctx = reopened.build_session_context()
        assert ctx.active_tool_names == ["read"]


def __assistant_message(text: str):
    """构造一个最简的 assistant message。"""
    from nova_ai import AssistantMessage, TextContent

    return AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text=text)],
        provider="test",
        model="test-model",
        stop_reason="stop",
    )
