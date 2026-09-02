"""
SessionManager 单元测试：验证 context 构建、leaf 指针语义、持久化往返。
"""

import tempfile

import pytest
from nova_ai import ModelThinkingLevel
from nova_harness.core.harness.session import SessionManager


@pytest.fixture
def session_manager():
    """创建一个内存中的 SessionManager（persist=False）。"""
    return SessionManager(
        cwd="/tmp", session_dir="/tmp/nova-test", session_file=None, persist=False
    )


def test_branch_moves_leaf_in_memory_only(session_manager):
    """branch() 只移动内存中的 leaf 指针，不产生任何条目（对齐 TS）。"""
    msg_id = session_manager.append_message(__assistant_message("hello"))
    before = len(session_manager.get_entries())
    session_manager.branch(msg_id)

    assert len(session_manager.get_entries()) == before
    assert session_manager.get_leaf_id() == msg_id


def test_reset_leaf_clears_pointer_in_memory_only(session_manager):
    """reset_leaf() 只把内存中的 leaf 置为 None，不产生任何条目。"""
    session_manager.append_message(__assistant_message("hello"))
    before = len(session_manager.get_entries())
    session_manager.reset_leaf()

    assert len(session_manager.get_entries()) == before
    assert session_manager.get_leaf_id() is None


def test_branch_with_summary_moves_leaf_and_appends_summary(session_manager):
    """branch_with_summary 移动 leaf 到目标位置并追加 branch_summary。"""
    msg_id = session_manager.append_message(__assistant_message("hello"))
    summary_id = session_manager.branch_with_summary(msg_id, "summary")

    entries = session_manager.get_entries()
    types = [e.type for e in entries]
    assert "branch_summary" in types

    summary = session_manager.get_entry(summary_id)
    assert summary.parent_id == msg_id
    # branch_summary 追加后成为新的 leaf
    assert session_manager.get_leaf_id() == summary_id


def test_session_file_roundtrip_uses_camelcase():
    """持久化到文件后应为 snake_case，reopen 后 leaf 恢复为最后一条 entry。"""
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        msg_id = manager.append_message(__assistant_message("hello"))
        manager.branch(msg_id)
        manager.append_thinking_level_change(ModelThinkingLevel.LOW)
        file_path = manager.get_session_file()

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

        # 头部使用 snake_case parent_session
        header = lines[0]
        assert '"parent_session"' in header or '"cwd"' in header

        # 落盘为紧凑 JSON（对齐 TS JSON.stringify，无空格分隔）
        assert '"type":"session"' in header

        # 不再写入 leaf entry
        assert not any('"type":"leaf"' in line for line in lines)

        # thinking_level_change 使用 snake_case thinking_level
        thinking_line = next(
            line for line in lines if '"type":"thinking_level_change"' in line
        )
        assert '"thinking_level"' in thinking_line

        reopened = SessionManager.open(file_path)
        # 重载后 leaf 恢复为最后一条 entry（对齐 TS）
        leaf_entry = reopened.get_leaf_entry()
        assert leaf_entry is not None
        assert leaf_entry.type == "thinking_level_change"
        ctx = reopened.build_session_context()
        assert ctx.thinking_level == ModelThinkingLevel.LOW


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
