"""
Session 模块全面测试：覆盖 entry 生命周期、分支导航、文件持久化、
序列化、context 重建以及各种边界情况。
"""

import os
import tempfile

import pytest
from nova_ai import AssistantMessage, ModelThinkingLevel, TextContent, UserMessage
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
    session.append_thinking_level_change(ModelThinkingLevel.HIGH)
    ctx = session.build_session_context()
    assert ctx.thinking_level == ModelThinkingLevel.HIGH


def test_append_model_change_updates_context(session):
    a2 = session.append_message(_assistant("a"))  # noqa: F841
    session.append_model_change("openai", "gpt-4")
    ctx = session.build_session_context()
    assert ctx.model == ("openai", "gpt-4")


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


def test_branch_moves_leaf_in_memory_only(session):
    """branch() 只移动内存中的 leaf 指针，不产生条目（对齐 TS）。"""
    msg_id = session.append_message(_assistant("a"))  # noqa: F841
    before = len(session.get_entries())
    session.branch(msg_id)

    assert len(session.get_entries()) == before
    assert session.get_leaf_id() == msg_id


def test_multiple_branches_latest_leaf_wins(session):
    m1 = session.append_message(_assistant("a1"))
    m2 = session.append_message(_assistant("a2"))
    session.branch(m1)
    session.branch(m2)

    # 纯内存指针，当前 leaf 为最后一次 branch 的目标
    assert session.get_leaf_id() == m2


def test_reset_leaf_clears_pointer_in_memory_only(session):
    """reset_leaf() 只把内存中的 leaf 置为 None，不产生条目。"""
    a8 = session.append_message(_assistant("a"))  # noqa: F841
    before = len(session.get_entries())
    session.reset_leaf()

    assert len(session.get_entries()) == before
    assert session.get_leaf_id() is None


def test_branch_with_summary_moves_leaf_and_appends_summary(session):
    m1 = session.append_message(_assistant("a"))
    summary_id = session.branch_with_summary(m1, "summary text")

    entries = session.get_entries()
    types = [e.type for e in entries]
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
        manager.append_thinking_level_change(ModelThinkingLevel.LOW)
        manager.append_model_change("openai", "gpt-4")
        path = manager.get_session_file()

        reopened = SessionManager.open(path)
        assert reopened.get_header().version == 3
        assert reopened.get_leaf_id() == manager.get_leaf_id()

        ctx = reopened.build_session_context()
        assert ctx.thinking_level == ModelThinkingLevel.LOW
        assert ctx.model == ("openai", "gpt-4")


def test_persist_and_reopen_leaf_is_last_entry():
    """branch 只移动内存指针不落盘，reopen 后 leaf 恢复为最后一条 entry（对齐 TS）。"""
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        m1 = manager.append_message(_assistant("a1"))
        m2 = manager.append_message(_assistant("a2"))
        manager.branch(m1)

        reopened = SessionManager.open(manager.get_session_file())
        assert reopened.get_leaf_id() == m2


def test_rewrite_after_first_assistant_flushes_all_entries():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        manager.append_thinking_level_change(ModelThinkingLevel.MEDIUM)
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
        # header 存的是 abspath 归一后的值——期望经同函数现算（Windows 补盘符）
        assert forked.get_header().cwd == os.path.abspath("/workspace")
        assert forked.get_header().parent_session == source_path
        assert len(forked.get_entries()) == 2


def test_fork_from_preserves_unknown_entry_lines():
    """fork 走行级原文复制：未知类型条目在磁盘层不丢（对齐 TS fork）。

    类型化加载会丢弃未知条目（校验式解析取舍），但 fork 的文件复制语义
    要求合法 JSON 行原样保留；非法 JSON 行丢弃。
    """
    import json

    with tempfile.TemporaryDirectory() as tmp:
        source = SessionManager.create(cwd="/tmp", session_dir=tmp)
        source.append_message(_assistant("a1"))
        source_path = source.get_session_file()

        unknown_line = json.dumps(
            {
                "type": "future_entry_kind",
                "id": "future01",
                "parent_id": None,
                "timestamp": "2026-01-01T00:00:00.000Z",
                "payload": {"x": 1},
            },
            separators=(",", ":"),
        )
        with open(source_path, "a", encoding="utf-8") as f:
            f.write(unknown_line + "\n")
            f.write("{not valid json\n")

        forked = SessionManager.fork_from(source_path, target_cwd="/workspace")

        # 内存视图：未知条目不可见（校验式解析丢弃）
        assert len(forked.get_entries()) == 1

        # 磁盘层：未知类型行原样保留，非法 JSON 行被丢弃
        with open(forked.get_session_file(), "r", encoding="utf-8") as f:
            disk = f.read()
        assert unknown_line in disk
        assert "{not valid json" not in disk


# -----------------------------------------------------------------------------
# build_session_context 边界
# -----------------------------------------------------------------------------


def test_build_context_empty_entries():
    ctx = build_session_context([])
    assert ctx.messages == []
    assert ctx.model is None


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


def test_null_content_message_entry_is_repaired_not_dropped():
    """content 为 null 的消息条目按 TS 容错策略修复为 [] 并保留（不丢条目）。

    对齐 TS sessionEntryToContextMessages 的 null-content 修复分支：
    旧版本、fork、手工编辑的文件可能出现 content: null。
    """
    import json

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        a28 = manager.append_message(_assistant("real"))  # noqa: F841
        path = manager.get_session_file()

        # 手工追加一条 content: null 的 user 消息（模拟损坏/手编辑文件）
        broken_line = json.dumps(
            {
                "type": "message",
                "id": "broken01",
                "parent_id": None,
                "timestamp": "2026-01-01T00:00:00.000Z",
                "message": {"role": "user", "content": None, "timestamp": 1},
            }
        )
        with open(path, "a", encoding="utf-8") as f:
            f.write(broken_line + "\n")

        entries = load_entries_from_file(path)
        repaired = next(e for e in entries if getattr(e, "id", None) == "broken01")
        assert repaired.message.content == []

        # 投影链路不炸，修复后的条目参与上下文
        ctx = build_session_context(entries[1:])
        assert any(getattr(m, "role", None) == "user" for m in ctx.messages)


def test_none_fields_are_omitted_from_jsonl():
    """None 字段不落盘（对齐 TS undefined 键省略），且为紧凑 JSON。"""
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        manager.append_message(_assistant("a"))
        path = manager.get_session_file()

        with open(path, "r", encoding="utf-8") as f:
            header_line = f.readline()

        # header 的 parent_session 为 None → 键省略（对齐 TS）
        assert "parent_session" not in header_line
        # 紧凑分隔符（无空格）
        assert '"type":"session"' in header_line


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


def test_session_manager_open_invalid_file_raises():
    """非空但无法解析的会话文件必须抛错保护数据（对齐 TS，不静默覆盖）。"""
    with tempfile.TemporaryDirectory() as tmp:
        invalid_path = os.path.join(tmp, "not_a_session.jsonl")
        with open(invalid_path, "w") as f:
            f.write("not json\n")

        with pytest.raises(ValueError, match="not a valid session"):
            SessionManager.open(invalid_path)


# -----------------------------------------------------------------------------
# details 写入归一化（SessionManager._normalize_details）
# -----------------------------------------------------------------------------


def test_append_compaction_normalizes_pydantic_details(session):
    """pydantic details 在写入关口被归一化为 dict（内存/落盘/重载一种表示）。"""
    from nova_harness.core.types.compaction import CompactionDetails

    session.append_message(_assistant("a"))
    entry = session.append_compaction(
        "summary",
        "x",
        100,
        CompactionDetails(read_files=["a.py"], modified_files=["b.py"]),
    )
    assert isinstance(entry.details, dict)
    assert entry.details == {"read_files": ["a.py"], "modified_files": ["b.py"]}


def test_append_compaction_rejects_non_serializable_details(session):
    """非 JSON 原生类型且非 pydantic 的 details 在写入关口抛 TypeError。"""
    session.append_message(_assistant("a"))

    class Custom:
        pass

    with pytest.raises(TypeError, match="details"):
        session.append_compaction("summary", "x", 100, Custom())


def test_branch_with_summary_normalizes_details(session):
    m1 = session.append_message(_assistant("a"))
    session.branch_with_summary(m1, "s", details={"version": 3})
    entry = session.get_entry(session.get_leaf_id())
    assert entry.details == {"version": 3}


# -----------------------------------------------------------------------------
# 空文件与损坏文件
# -----------------------------------------------------------------------------


def test_open_empty_file_initializes_new_session():
    """空文件用新 header 初始化（对齐 TS），不抛错。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "empty.jsonl")
        open(path, "w").close()

        manager = SessionManager.open(path)
        assert manager.get_header() is not None
        assert manager.get_session_file() == os.path.abspath(path)


# -----------------------------------------------------------------------------
# 自定义 session id
# -----------------------------------------------------------------------------


def test_new_session_with_custom_id():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(
            cwd="/tmp", session_dir=tmp, session_id="my-session.1"
        )
        assert manager.get_session_id() == "my-session.1"


def test_new_session_with_invalid_id_raises():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="Session id"):
            SessionManager.create(cwd="/tmp", session_dir=tmp, session_id="bad id!")


# -----------------------------------------------------------------------------
# _persist_entry 的 flushed 语义（对齐 TS）
# -----------------------------------------------------------------------------


def test_entries_after_flush_are_appended_even_without_assistant():
    """文件已创建（flushed）后，无 assistant 的后续 entry 也立即追加落盘。"""
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        manager.append_message(_assistant("a"))
        assert manager._flushed

        manager.append_message(_user("follow-up"))
        with open(manager.get_session_file(), "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        # header + assistant + user 全部落盘
        assert len(lines) == 3


def test_first_write_creates_file_exclusively():
    """无 assistant 时不创建文件；首个 assistant 触发全量写出。"""
    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        manager.append_message(_user("u1"))
        assert not os.path.exists(manager.get_session_file())

        manager.append_message(_assistant("a1"))
        with open(manager.get_session_file(), "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        # header + user + assistant 全量写出
        assert len(lines) == 3


# -----------------------------------------------------------------------------
# build_session_context 的 leaf 语义
# -----------------------------------------------------------------------------


def test_build_context_after_reset_leaf_is_empty(session):
    """reset_leaf 后（leaf=None）上下文为空（对齐 TS resetLeaf 语义）。"""
    session.append_message(_user("u"))
    session.append_message(_assistant("a"))
    session.reset_leaf()
    ctx = session.build_session_context()
    assert ctx.messages == []


# -----------------------------------------------------------------------------
# find_most_recent_session 的 cwd 过滤
# -----------------------------------------------------------------------------


def test_find_most_recent_session_filters_by_cwd():
    from nova_harness.core.harness.session import find_most_recent_session

    with tempfile.TemporaryDirectory() as tmp:
        m1 = SessionManager.create(cwd="/proj/a", session_dir=tmp)
        m1.append_message(_assistant("a"))
        import time as _time

        _time.sleep(0.01)
        m2 = SessionManager.create(cwd="/proj/b", session_dir=tmp)
        m2.append_message(_assistant("b"))

        # 无过滤：返回最新的
        assert find_most_recent_session(tmp) == m2.get_session_file()
        # 按 cwd 过滤：只返回该项目的
        assert find_most_recent_session(tmp, "/proj/a") == m1.get_session_file()
        # 无匹配：None
        assert find_most_recent_session(tmp, "/proj/nonexistent") is None


# -----------------------------------------------------------------------------
# get_last_activity_time 时间单位统一（P0 回归：ms 与秒混合曾致溢出）
# -----------------------------------------------------------------------------


def test_get_last_activity_time_unifies_milliseconds(session):
    """消息时间戳（ms）与条目时间戳（ISO）统一为毫秒，fromtimestamp 不溢出。"""
    from nova_harness.core.harness.session import get_last_activity_time

    msg = _assistant("a")
    msg.timestamp = 1_700_000_000_000  # epoch 毫秒
    session.append_message(msg)

    last = get_last_activity_time(session.get_entries())
    assert last == 1_700_000_000_000


def test_build_session_info_with_ms_timestamp():
    """带 int(ms) 时间戳消息的会话能正常构建 SessionInfo（不再返回 None）。"""
    from nova_harness.core.harness.session.listing import _build_session_info_sync

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager.create(cwd="/tmp", session_dir=tmp)
        msg = _assistant("a")
        msg.timestamp = 1_700_000_000_000
        manager.append_message(msg)

        info = _build_session_info_sync(manager.get_session_file())
        assert info is not None
        assert info.message_count == 1


# -----------------------------------------------------------------------------
# fork_from 独占创建
# -----------------------------------------------------------------------------


def test_fork_from_writes_new_file_and_preserves_entries():
    with tempfile.TemporaryDirectory() as tmp:
        source = SessionManager.create(cwd="/tmp", session_dir=os.path.join(tmp, "src"))
        source.append_message(_assistant("a1"))
        source.append_message(_assistant("a2"))

        forked = SessionManager.fork_from(
            source.get_session_file(), "/tmp", session_dir=os.path.join(tmp, "dst")
        )
        assert forked.get_session_id() != source.get_session_id()
        assert len(forked.get_entries()) == 2
        assert forked.get_header().parent_session == os.path.abspath(
            source.get_session_file()
        )
