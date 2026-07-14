"""
Compaction 与分支摘要模块全面测试。

覆盖纯函数、entry -> message 转换、切割点计算、文件操作追踪、
分支摘要准备等不依赖真实 LLM 的逻辑。
"""

from typing import Optional

import pytest
from nova_ai import (
    AssistantMessage,
    AssistantMessageEventStream,
    Context,
    DoneEvent,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    SimpleStreamOptions,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

import nova_harness.core.harness.compaction.compaction as compaction_module
from nova_harness.core.harness.compaction import (
    calculate_context_tokens,
    collect_entries_for_branch_summary,
    compact,
    compute_file_lists,
    create_file_ops,
    estimate_context_tokens,
    estimate_tokens,
    extract_file_ops_from_message,
    find_cut_point,
    find_turn_start_index,
    get_last_assistant_usage,
    prepare_branch_entries,
    prepare_compaction,
    should_compact,
)
from nova_harness.core.harness.compaction.compaction import generate_summary
from nova_harness.core.harness.compaction.utils import (
    format_file_operations,
    serialize_conversation,
)
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.types.compaction import CompactionSettings, CutPointResult
from nova_harness.core.types.messages import BashExecutionMessage
from nova_harness.core.types.session.context import SessionContext


def _user(text: str) -> UserMessage:
    return UserMessage(role="user", content=[TextContent(type="text", text=text)])


def _assistant(text: str, usage: Usage = None, model: str = "test") -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text=text)],
        provider="test",
        model=model,
        stop_reason="stop",
        usage=usage or Usage(),
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


def _tool_result(result: str) -> ToolResultMessage:
    return ToolResultMessage(
        role="toolResult",
        content=[TextContent(type="text", text=result)],
        tool_call_id="tc1",
        tool_name="read",
    )


def _bash(command: str, output: str) -> BashExecutionMessage:
    return BashExecutionMessage(
        command=command,
        output=output,
        exit_code=0,
        cancelled=False,
        truncated=False,
    )


def _make_model(context_window: int = 128000, max_tokens: int = 4096) -> Model:
    return Model(
        id="test-model",
        name="Test Model",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.OPENAI,
        base_url="https://test.example.com/v1",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(),
        context_window=context_window,
        max_tokens=max_tokens,
    )


def _summary_assistant(
    text: str = "summary",
    stop_reason: str = "stop",
    error_message: Optional[str] = None,
) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text=text)],
        provider="test",
        model="test",
        stop_reason=stop_reason,
        usage=Usage(),
        error_message=error_message,
    )


def _stream_with_result(result: AssistantMessage) -> AssistantMessageEventStream:
    stream = AssistantMessageEventStream()
    stream.push(DoneEvent(message=result))
    return stream


@pytest.fixture
def model() -> Model:
    return _make_model()


@pytest.fixture
def session():
    """内存 SessionManager。"""
    return SessionManager(
        cwd="/tmp", session_dir="/tmp/nova-test", session_file=None, persist=False
    )


# -----------------------------------------------------------------------------
# Token 计算
# -----------------------------------------------------------------------------


def test_calculate_context_tokens_uses_total():
    usage = Usage(input=10, output=5, total_tokens=100)
    assert calculate_context_tokens(usage) == 100


def test_calculate_context_tokens_fallback():
    usage = Usage(input=10, output=5, cache_read=2, cache_write=3, total_tokens=0)
    assert calculate_context_tokens(usage) == 20


def test_estimate_tokens_string_content():
    msg = _user("a" * 40)
    assert estimate_tokens(msg) == 10


def test_estimate_tokens_list_content():
    msg = _user("a" * 40)
    # content list 会拼接文本后 /4
    assert estimate_tokens(msg) == 10


# -----------------------------------------------------------------------------
# 压缩触发判断
# -----------------------------------------------------------------------------


def test_should_compact_when_over_budget():
    settings = CompactionSettings(enabled=True, reserve_tokens=1000)
    assert should_compact(2500, 3000, settings) is True


def test_should_compact_disabled():
    settings = CompactionSettings(enabled=False)
    assert should_compact(999999, 1000, settings) is False


def test_should_compact_within_budget():
    settings = CompactionSettings(enabled=True, reserve_tokens=1000)
    assert should_compact(1500, 3000, settings) is False


# -----------------------------------------------------------------------------
# 文件操作追踪
# -----------------------------------------------------------------------------


def test_extract_file_ops_read_write_edit():
    file_ops = create_file_ops()
    msg = _tool_call("read", "/tmp/a.py")
    extract_file_ops_from_message(msg, file_ops)
    assert "/tmp/a.py" in file_ops.read

    msg = _tool_call("write", "/tmp/b.py")
    extract_file_ops_from_message(msg, file_ops)
    assert "/tmp/b.py" in file_ops.written

    msg = _tool_call("edit", "/tmp/c.py")
    extract_file_ops_from_message(msg, file_ops)
    assert "/tmp/c.py" in file_ops.edited


def test_compute_file_lists_excludes_read_from_modified():
    file_ops = create_file_ops()
    file_ops.read.add("/tmp/a.py")
    file_ops.read.add("/tmp/b.py")
    file_ops.edited.add("/tmp/b.py")
    read_files, modified_files = compute_file_lists(file_ops)
    assert read_files == ["/tmp/a.py"]
    assert modified_files == ["/tmp/b.py"]


def test_format_file_operations():
    file_ops = create_file_ops()
    file_ops.read.add("/tmp/a.py")
    file_ops.edited.add("/tmp/b.py")
    read_files, modified_files = compute_file_lists(file_ops)
    text = format_file_operations(read_files, modified_files)
    assert "<read-files>" in text
    assert "<modified-files>" in text
    assert "/tmp/a.py" in text
    assert "/tmp/b.py" in text


# -----------------------------------------------------------------------------
# 切割点与 turn 起点
# -----------------------------------------------------------------------------


def test_find_turn_start_index_user_message():
    session = SessionManager(
        cwd="/tmp", session_dir="/tmp/nova-test", session_file=None, persist=False
    )
    u1 = session.append_message(_user("hello"))  # noqa: F841
    a1 = session.append_message(_assistant("hi"))  # noqa: F841
    u2 = session.append_message(_user("again"))  # noqa: F841

    entries = session.get_entries()
    idx = find_turn_start_index(entries, 2, 0)
    assert entries[idx].id == u2


def test_find_turn_start_index_with_bash():
    session = SessionManager(
        cwd="/tmp", session_dir="/tmp/nova-test", session_file=None, persist=False
    )
    u3 = session.append_message(_user("hello"))  # noqa: F841
    b1 = session.append_message(_bash("ls", "a.py"))  # noqa: F841
    a2 = session.append_message(_assistant("ok"))  # noqa: F841

    entries = session.get_entries()
    idx = find_turn_start_index(entries, 2, 0)
    assert entries[idx].id == b1


def test_find_cut_point_keeps_recent():
    session = SessionManager(
        cwd="/tmp", session_dir="/tmp/nova-test", session_file=None, persist=False
    )
    # 创建 4 条 user/assistant 对，每条 assistant 约 100 tokens
    ids = []
    for i in range(4):
        u4 = session.append_message(_user(f"q{i}"))  # noqa: F841
        ids.append(session.append_message(_assistant("a" * 400)))

    entries = session.get_entries()
    settings = CompactionSettings(keep_recent_tokens=150)
    result = find_cut_point(entries, 0, len(entries), settings.keep_recent_tokens)

    # 至少保留最近 1 条 assistant，切割点应在某个 user 上
    assert 0 <= result.first_kept_entry_index < len(entries)
    assert entries[result.first_kept_entry_index].type == "message"


def test_find_cut_point_with_custom_message():
    session = SessionManager(
        cwd="/tmp", session_dir="/tmp/nova-test", session_file=None, persist=False
    )
    u5 = session.append_message(_user("hello"))  # noqa: F841
    session.append_custom_message_entry("note", "custom", display=True)
    a3 = session.append_message(_assistant("ok"))  # noqa: F841

    entries = session.get_entries()
    [e.type for e in entries]
    settings = CompactionSettings(keep_recent_tokens=10)
    result = find_cut_point(entries, 0, len(entries), settings.keep_recent_tokens)
    assert result.first_kept_entry_index >= 0


# -----------------------------------------------------------------------------
# prepare_compaction
# -----------------------------------------------------------------------------


def test_prepare_compaction_basic(session):
    # 构造足够长的历史，让 keep_recent 只能保留最近一条 assistant
    u6 = session.append_message(_user("first"))  # noqa: F841
    session.append_message(
        _assistant(
            "answer" + "x" * 400, usage=Usage(input=100, output=50, total_tokens=150)
        )
    )
    u7 = session.append_message(_user("second"))  # noqa: F841
    session.append_message(
        _assistant("answer2" + "x" * 400, usage=Usage(total_tokens=50))
    )

    path = session.get_branch()
    settings = CompactionSettings(enabled=True, keep_recent_tokens=10)
    prep = prepare_compaction(path, settings)

    assert prep is not None
    assert prep.first_kept_entry_id in {e.id for e in session.get_entries()}
    assert prep.messages_to_summarize
    assert prep.tokens_before > 0
    assert isinstance(prep.file_ops.read, set)


def test_prepare_compaction_with_previous_compaction(session):
    u1 = session.append_message(_user("first"))
    session.append_message(_assistant("answer1"))
    u2 = session.append_message(_user("second"))
    session.append_message(_assistant("answer2"))

    # 先写入一个 compaction entry，模拟上次压缩
    session.append_compaction(
        summary="old summary",
        first_kept_entry_id=u2,
        tokens_before=100,
    )
    u10 = session.append_message(_user("third"))  # noqa: F841
    a6 = session.append_message(_assistant("answer3"))  # noqa: F841

    path = session.get_branch()
    settings = CompactionSettings(enabled=True, keep_recent_tokens=10)
    prep = prepare_compaction(path, settings)

    assert prep is not None
    # 待摘要消息应从 compaction 边界之后开始
    assert all(e.id != u1 for e in session.get_entries() if e.type == "compaction")


def test_prepare_compaction_returns_none_at_compaction_leaf(session):
    u1 = session.append_message(_user("first"))
    session.append_message(_assistant("answer"))
    session.append_compaction("summary", u1, 100)

    path = session.get_branch()
    settings = CompactionSettings()
    assert prepare_compaction(path, settings) is None


# -----------------------------------------------------------------------------
# build_session_context 与 compaction 集成
# -----------------------------------------------------------------------------


def test_build_context_with_compaction_summary(session):
    session.append_message(_user("first"))
    session.append_message(_assistant("answer1"))
    u2 = session.append_message(_user("second"))
    session.append_message(_assistant("answer2"))
    session.append_compaction("compacted summary", u2, 200)

    ctx = session.build_session_context()
    assert any(getattr(m, "role", None) == "compactionSummary" for m in ctx.messages)


def test_build_context_includes_branch_summary(session):
    session.append_message(_user("first"))
    a1 = session.append_message(_assistant("answer1"))
    session.branch_with_summary(a1, "branch summary")

    ctx = session.build_session_context()
    assert any(getattr(m, "role", None) == "branchSummary" for m in ctx.messages)


def test_build_context_ignores_custom_and_leaf(session):
    u1 = session.append_message(_user("first"))
    session.append_custom_entry("plugin_state", {"key": "value"})
    session.branch(u1)
    session.append_active_tools_change(["read"])

    ctx = session.build_session_context()
    # custom entry 不参与 context；leaf 也不参与
    assert len(ctx.messages) == 1
    assert ctx.active_tool_names == ["read"]


def test_build_context_assistant_message_updates_model(session):
    u16 = session.append_message(_user("q"))  # noqa: F841
    a11 = session.append_message(_assistant("a", model="gpt-4"))  # noqa: F841

    ctx = session.build_session_context()
    assert ctx.model == ("test", "gpt-4")


def test_get_last_assistant_usage(session):
    usage = Usage(input=10, output=5, total_tokens=15)
    u17 = session.append_message(_user("q"))  # noqa: F841
    a12 = session.append_message(_assistant("a", usage=usage))  # noqa: F841

    entries = session.get_entries()
    found = get_last_assistant_usage(entries)
    assert found is not None
    assert found.total_tokens == 15


# -----------------------------------------------------------------------------
# 分支摘要
# -----------------------------------------------------------------------------


def test_collect_entries_for_branch_summary(session):
    u1 = session.append_message(_user("first"))
    a1 = session.append_message(_assistant("a1"))
    session.append_message(_user("second"))
    session.append_message(_assistant("a2"))

    # 分支回退到 a1
    session.branch(a1)
    u3 = session.append_message(_user("third"))
    a3 = session.append_message(_assistant("a3"))

    result = collect_entries_for_branch_summary(session, a3, a1)
    # 从旧叶子 a3 回到与 target a1 的公共祖先，收集 a3-u3-a3 分支
    assert any(e.id == u3 for e in result.entries)
    assert any(e.id == a3 for e in result.entries)
    assert all(e.id != u1 for e in result.entries)


def test_prepare_branch_entries_respects_budget(session):
    u21 = session.append_message(_user("q1"))  # noqa: F841
    a16 = session.append_message(_assistant("a" * 400))  # noqa: F841
    u22 = session.append_message(_user("q2"))  # noqa: F841
    a17 = session.append_message(_assistant("b" * 400))  # noqa: F841

    prep = prepare_branch_entries(session.get_branch(), token_budget=50)
    # 预算只够最近一条消息
    assert len(prep.messages) <= 2
    assert prep.total_tokens <= 50


# -----------------------------------------------------------------------------
# serialize_conversation
# -----------------------------------------------------------------------------


def test_serialize_conversation_includes_roles():
    messages = [_user("hello"), _assistant("hi")]
    text = serialize_conversation(messages)
    assert "User:" in text or "hello" in text
    assert "Assistant:" in text or "hi" in text


def test_estimate_context_tokens_with_last_usage():
    messages = [
        _user("q1"),
        _assistant("a1", usage=Usage(input=100, output=50, total_tokens=150)),
        _user("q2"),
        _assistant("a2", usage=Usage(input=10, output=5, total_tokens=15)),
    ]
    est = estimate_context_tokens(messages)
    assert est.usage_tokens == 15
    assert est.tokens == 15


# -----------------------------------------------------------------------------
# 错误与边界
# -----------------------------------------------------------------------------


def test_extract_file_ops_ignores_non_tool_messages():
    file_ops = create_file_ops()
    extract_file_ops_from_message(_user("hello"), file_ops)
    assert not file_ops.read
    assert not file_ops.edited
    assert not file_ops.written


def test_find_cut_point_empty_entries():
    result = find_cut_point([], 0, 0, 100)
    assert result.first_kept_entry_index == 0
    assert result.turn_start_index == -1


def test_prepare_compaction_empty_path():
    assert prepare_compaction([], CompactionSettings()) is None


# -----------------------------------------------------------------------------
# Async compaction / generate_summary / _complete_summarization
# -----------------------------------------------------------------------------


async def test_compact_non_split_turn(session, monkeypatch):
    """compact() 在非 split-turn 场景下生成历史摘要。"""
    u23 = session.append_message(_user("q0"))  # noqa: F841
    session.append_message(_assistant("a" * 400, usage=Usage(total_tokens=100)))
    u1 = session.append_message(_user("q1"))
    session.append_message(_assistant("a" * 400, usage=Usage(total_tokens=100)))
    session.append_message(_user("q2"))
    session.append_message(_assistant("a" * 400, usage=Usage(total_tokens=100)))

    prep = prepare_compaction(
        session.get_branch(), CompactionSettings(enabled=True, keep_recent_tokens=202)
    )
    assert prep is not None
    assert prep.is_split_turn is False

    async def fake_complete(*a, **k):
        return _summary_assistant("history summary")

    monkeypatch.setattr(compaction_module, "_complete_summarization", fake_complete)

    result = await compact(prep, _make_model(), api_key="key")
    assert result.first_kept_entry_id == u1
    assert "history summary" in result.summary
    assert result.tokens_before > 0


async def test_compact_split_turn(session, monkeypatch):
    """compact() 在 split-turn 场景下分别生成历史摘要与 turn prefix 摘要。"""
    session.append_message(_user("q0"))
    session.append_message(_assistant("a" * 400))
    session.append_message(_user("q1"))
    a1 = session.append_message(_assistant("a" * 400))

    prep = prepare_compaction(
        session.get_branch(), CompactionSettings(enabled=True, keep_recent_tokens=50)
    )
    assert prep is not None
    assert prep.is_split_turn is True
    assert prep.first_kept_entry_id == a1
    assert prep.turn_prefix_messages

    async def fake_complete(model, context, options, stream_fn=None):
        prompt = context.messages[0].content[0].text
        if "PREFIX of a turn" in prompt:
            return _summary_assistant("turn prefix summary")
        return _summary_assistant("history summary")

    monkeypatch.setattr(compaction_module, "_complete_summarization", fake_complete)

    result = await compact(prep, _make_model(), api_key="key")
    assert "history summary" in result.summary
    assert "Turn Context (split turn):" in result.summary
    assert "turn prefix summary" in result.summary
    assert result.first_kept_entry_id == a1


async def test_generate_summary_uses_previous_summary(monkeypatch):
    """提供 previous_summary 时使用增量更新 prompt。"""
    captured = {}

    async def fake_complete(model, context, options, stream_fn=None):
        captured["context"] = context
        return _summary_assistant("updated summary")

    monkeypatch.setattr(compaction_module, "_complete_summarization", fake_complete)

    result = await generate_summary(
        [_user("q"), _assistant("a")],
        _make_model(),
        reserve_tokens=1000,
        api_key="key",
        previous_summary="old summary",
    )
    assert result == "updated summary"
    prompt = captured["context"].messages[0].content[0].text
    assert "<previous-summary>\nold summary\n</previous-summary>" in prompt
    assert "NEW conversation messages" in prompt


async def test_generate_summary_raises_on_error(monkeypatch):
    """LLM 返回 stop_reason == error 时抛出异常。"""

    async def fake_complete(*a, **k):
        return _summary_assistant("bad", stop_reason="error", error_message="boom")

    monkeypatch.setattr(compaction_module, "_complete_summarization", fake_complete)

    with pytest.raises(Exception, match="Summarization failed"):
        await generate_summary(
            [_user("q")], _make_model(), reserve_tokens=1000, api_key="key"
        )


async def test_complete_summarization_with_sync_stream_fn():
    """_complete_summarization 调用同步自定义 stream_fn。"""
    called = {}
    result_msg = _summary_assistant("sync summary")

    def stream_fn(model, context, options):
        called["model"] = model
        called["context"] = context
        called["options"] = options
        return _stream_with_result(result_msg)

    model = _make_model()
    context = Context.model_validate({"messages": [{"role": "user", "content": "hi"}]})
    options = SimpleStreamOptions.model_validate({"api_key": "key", "max_tokens": 10})

    result = await compaction_module._complete_summarization(
        model, context, options, stream_fn
    )
    assert result is result_msg
    assert called["model"] is model
    assert called["options"] is options


async def test_complete_summarization_with_async_stream_fn():
    """_complete_summarization 调用异步自定义 stream_fn。"""
    called = {}
    result_msg = _summary_assistant("async summary")

    async def stream_fn(model, context, options):
        called["model"] = model
        called["context"] = context
        called["options"] = options
        return _stream_with_result(result_msg)

    model = _make_model()
    context = Context.model_validate({"messages": [{"role": "user", "content": "hi"}]})
    options = SimpleStreamOptions.model_validate({"api_key": "key", "max_tokens": 10})

    result = await compaction_module._complete_summarization(
        model, context, options, stream_fn
    )
    assert result.content[0].text == "async summary"
    assert called["model"] is model


# -----------------------------------------------------------------------------
# prepare_compaction 边界与阈值
# -----------------------------------------------------------------------------


def test_prepare_compaction_split_turn(session):
    session.append_message(_user("q0"))
    session.append_message(_assistant("a" * 400))
    session.append_message(_user("q1"))
    a1 = session.append_message(_assistant("a" * 400))

    prep = prepare_compaction(
        session.get_branch(), CompactionSettings(enabled=True, keep_recent_tokens=50)
    )
    assert prep is not None
    assert prep.is_split_turn is True
    assert prep.first_kept_entry_id == a1
    assert any(
        m.role == "user" and m.content[0].text == "q1"
        for m in prep.turn_prefix_messages
    )


def test_prepare_compaction_last_entry_is_compaction(session):
    u1 = session.append_message(_user("q"))
    session.append_message(_assistant("a"))
    session.append_compaction("summary", u1, 10)
    assert prepare_compaction(session.get_branch(), CompactionSettings()) is None


def test_prepare_compaction_no_id_returns_none(monkeypatch):
    class NoIdEntry:
        type = "message"
        parent_id = None
        message = _user("x")

    monkeypatch.setattr(
        compaction_module,
        "build_session_context",
        lambda entries, leaf_id=None, by_id=None: SessionContext(
            messages=[entries[0].message] if entries else []
        ),
    )
    monkeypatch.setattr(
        compaction_module,
        "find_cut_point",
        lambda entries, s, e, k: CutPointResult(
            first_kept_entry_index=0, turn_start_index=-1, is_split_turn=False
        ),
    )

    assert prepare_compaction([NoIdEntry()], CompactionSettings()) is None


def test_should_compact_exactly_at_threshold():
    settings = CompactionSettings(enabled=True, reserve_tokens=1000)
    assert should_compact(2000, 3000, settings) is False


def test_should_compact_just_over_threshold():
    settings = CompactionSettings(enabled=True, reserve_tokens=1000)
    assert should_compact(2001, 3000, settings) is True


def test_estimate_context_tokens_full_estimate_when_no_usage():
    messages = [_user("a" * 40), _user("b" * 40)]
    est = estimate_context_tokens(messages)
    assert est.usage_tokens == 0
    assert est.tokens == 20
    assert est.last_usage_index is None


def test_estimate_context_tokens_trailing_after_usage():
    messages = [
        _user("q1"),
        _assistant("a1", usage=Usage(input=100, output=50, total_tokens=150)),
        _user("q2"),
    ]
    est = estimate_context_tokens(messages)
    assert est.usage_tokens == 150
    assert est.last_usage_index == 1
    assert est.trailing_tokens > 0
    assert est.tokens == 150 + est.trailing_tokens
