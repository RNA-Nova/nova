"""TreeNavigator 编排行为测试。

SessionManager 层（leaf 指针/分叉/持久化）的覆盖在
tests/core/harness/session/test_session_comprehensive.py；本文件补
**控制器编排层**：同叶早退、未知条目、目标类型分派（user/assistant/
custom_message）、编辑器回填、摘要三路径（成功/abort/错误）、扩展取消、
abort controller 清理、SessionReplacedEvent 发射。
"""

from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from nova_ai import AssistantMessage, Model, ModelCost, Usage, UserMessage
from nova_harness.core.agent_session.controllers.tree import TreeNavigator
from nova_harness.core.agent_session.controllers import tree as tree_module
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.types.config.settings import BranchSummarySettings
from nova_harness.core.types.session.options import NavigateOptions


def _model() -> Model:
    return Model(
        id="m",
        name="m",
        api="openai-completions",
        provider="test",
        base_url="",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0),
        context_window=1000,
        max_tokens=4096,
    )


def _assistant(text: str, *, timestamp: int = 0) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[{"type": "text", "text": text}],
        api="openai-completions",
        provider="test",
        model="m",
        usage=Usage(),
        stop_reason="stop",
        timestamp=timestamp,
    )


def _session(tmp_path, *, model: Optional[Model] = None):
    """MagicMock 会话 + 真实 SessionManager（同 test_compaction_controller 形态）。"""
    session = MagicMock()
    session.model = model
    session.thinking_level = None
    session._extension_runner = None
    session._branch_summary_abort_controller = None
    session.settings_manager.get_branch_summary_settings.return_value = (
        BranchSummarySettings(reserve_tokens=16384)
    )
    session.session_manager = SessionManager.create("/tmp/cwd", str(tmp_path))
    session.agent.state.messages = []
    return session


def _four_message_branch(session):
    """user1 → assistant1 → user2 → assistant2，返回 (user1, a1, user2, a2) 条目 id。"""
    sm = session.session_manager
    u1 = sm.append_message(UserMessage(role="user", content="问题一", timestamp=1))
    a1 = sm.append_message(_assistant("回答一", timestamp=2))
    u2 = sm.append_message(UserMessage(role="user", content="问题二", timestamp=3))
    a2 = sm.append_message(_assistant("回答二", timestamp=4))
    return u1, a1, u2, a2


def _emitted(session):
    return [call.args[0] for call in session._emit.call_args_list]


@pytest.mark.asyncio
async def test_navigate_to_current_leaf_early_return(tmp_path):
    """目标即当前 leaf：早退，不动状态、不发事件。"""
    session = _session(tmp_path)
    _, _, _, a2 = _four_message_branch(session)
    navigator = TreeNavigator(session)

    result = await navigator.navigate(a2)

    assert result == {"cancelled": False}
    assert session._emit.call_count == 0
    assert session.session_manager.get_leaf_id() == a2
    assert session._branch_summary_abort_controller is None


@pytest.mark.asyncio
async def test_navigate_unknown_entry_raises(tmp_path):
    session = _session(tmp_path)
    _four_message_branch(session)
    navigator = TreeNavigator(session)

    with pytest.raises(ValueError, match="not found"):
        await navigator.navigate("no-such-entry")


@pytest.mark.asyncio
async def test_navigate_to_user_message_moves_leaf_and_fills_editor(tmp_path):
    """用户消息目标：leaf 移到父条目，原文回填编辑器，状态按新分支重建。"""
    session = _session(tmp_path)
    u1, a1, u2, _ = _four_message_branch(session)
    navigator = TreeNavigator(session)

    result = await navigator.navigate(u2)

    assert result["cancelled"] is False
    assert result["editor_text"] == "问题二"
    assert session.session_manager.get_leaf_id() == a1  # user2 的父条目
    # 状态按新分支重建：只剩 user1 + assistant1
    assert [m.role for m in session.agent.state.messages] == ["user", "assistant"]
    assert any(
        getattr(e, "reason", None) == "navigate" for e in _emitted(session)
    ), "SessionReplacedEvent(reason='navigate') 未发射"
    assert session._branch_summary_abort_controller is None


@pytest.mark.asyncio
async def test_navigate_to_assistant_keeps_leaf_at_entry(tmp_path):
    """非用户消息目标：leaf 即条目本身，编辑器不回填。"""
    session = _session(tmp_path)
    u1, a1, _, _ = _four_message_branch(session)
    navigator = TreeNavigator(session)

    result = await navigator.navigate(a1)

    assert result["cancelled"] is False
    assert result["editor_text"] is None
    assert session.session_manager.get_leaf_id() == a1
    assert [m.role for m in session.agent.state.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_navigate_to_custom_message(tmp_path):
    """custom_message 目标：leaf 移到父条目，文本内容回填编辑器。"""
    session = _session(tmp_path)
    sm = session.session_manager
    u1 = sm.append_message(UserMessage(role="user", content="问题一", timestamp=1))
    a1 = sm.append_message(_assistant("回答一", timestamp=2))
    # 批注挂在中间（追加即 leaf——不能直接导航到叶上，否则同叶早退）
    custom = sm.append_custom_message_entry("note", "a1 的批注")
    sm.append_message(UserMessage(role="user", content="问题二", timestamp=3))
    sm.append_message(_assistant("回答二", timestamp=4))
    navigator = TreeNavigator(session)

    result = await navigator.navigate(custom)

    assert result["cancelled"] is False
    assert result["editor_text"] == "a1 的批注"
    assert session.session_manager.get_leaf_id() == a1


@pytest.mark.asyncio
async def test_navigate_summarize_without_model_raises(tmp_path):
    """summarize=True 但无模型：报错且 leaf 不动。"""
    session = _session(tmp_path, model=None)
    _, _, u2, a2 = _four_message_branch(session)
    navigator = TreeNavigator(session)

    with pytest.raises(RuntimeError, match="No model"):
        await navigator.navigate(u2, NavigateOptions(summarize=True))

    assert session.session_manager.get_leaf_id() == a2
    assert session._branch_summary_abort_controller is None


@pytest.mark.asyncio
async def test_navigate_with_summary_appends_entry_and_moves_leaf(
    tmp_path, monkeypatch
):
    """摘要路径：生成分支摘要条目，leaf 移到摘要条目，事件带 summary_entry。"""
    session = _session(tmp_path, model=_model())
    _, a1, u2, _ = _four_message_branch(session)

    monkeypatch.setattr(
        tree_module,
        "get_summarization_request_auth",
        AsyncMock(return_value=("k", {}, None)),
    )
    generate = AsyncMock(
        return_value=MagicMock(
            aborted=False,
            error=None,
            summary="到 user2 之前的摘要",
            read_files=["a.py"],
            modified_files=[],
        )
    )
    monkeypatch.setattr(
        tree_module._branch_module, "generate_branch_summary", generate
    )

    navigator = TreeNavigator(session)
    result = await navigator.navigate(u2, NavigateOptions(summarize=True))

    assert result["cancelled"] is False
    summary_entry = result["summary_entry"]
    assert summary_entry is not None
    assert summary_entry.summary == "到 user2 之前的摘要"
    assert session.session_manager.get_leaf_id() == summary_entry.id
    generate.assert_called_once()
    assert session._branch_summary_abort_controller is None
    assert any(getattr(e, "reason", None) == "navigate" for e in _emitted(session))


@pytest.mark.asyncio
async def test_navigate_summary_aborted_returns_cancelled(tmp_path, monkeypatch):
    """摘要被 abort：返回 cancelled+aborted，leaf 不动，controller 清理。"""
    session = _session(tmp_path, model=_model())
    _, _, u2, a2 = _four_message_branch(session)

    monkeypatch.setattr(
        tree_module,
        "get_summarization_request_auth",
        AsyncMock(return_value=("k", {}, None)),
    )
    monkeypatch.setattr(
        tree_module._branch_module,
        "generate_branch_summary",
        AsyncMock(return_value=MagicMock(aborted=True, error=None)),
    )

    navigator = TreeNavigator(session)
    result = await navigator.navigate(u2, NavigateOptions(summarize=True))

    assert result == {"cancelled": True, "aborted": True}
    assert session.session_manager.get_leaf_id() == a2
    assert session._branch_summary_abort_controller is None
    assert not any(
        getattr(e, "reason", None) == "navigate" for e in _emitted(session)
    )


@pytest.mark.asyncio
async def test_navigate_summary_error_raises_and_cleans_controller(
    tmp_path, monkeypatch
):
    """摘要失败：抛 RuntimeError，leaf 不动，abort controller 经 finally 清理。"""
    session = _session(tmp_path, model=_model())
    _, _, u2, a2 = _four_message_branch(session)

    monkeypatch.setattr(
        tree_module,
        "get_summarization_request_auth",
        AsyncMock(return_value=("k", {}, None)),
    )
    monkeypatch.setattr(
        tree_module._branch_module,
        "generate_branch_summary",
        AsyncMock(return_value=MagicMock(aborted=False, error="LLM 摘要失败")),
    )

    navigator = TreeNavigator(session)
    with pytest.raises(RuntimeError, match="LLM 摘要失败"):
        await navigator.navigate(u2, NavigateOptions(summarize=True))

    assert session.session_manager.get_leaf_id() == a2
    assert session._branch_summary_abort_controller is None


@pytest.mark.asyncio
async def test_navigate_cancelled_by_extension_hook(tmp_path, monkeypatch):
    """session_before_tree 扩展取消：leaf 不动、不生成摘要、不发事件。"""
    session = _session(tmp_path, model=_model())
    _, _, u2, a2 = _four_message_branch(session)

    runner = MagicMock()
    runner.has_handlers.return_value = True
    runner.emit = AsyncMock(return_value=MagicMock(cancel=True))
    session._extension_runner = runner

    generate = AsyncMock()
    monkeypatch.setattr(
        tree_module._branch_module, "generate_branch_summary", generate
    )

    navigator = TreeNavigator(session)
    result = await navigator.navigate(u2, NavigateOptions(summarize=True))

    assert result == {"cancelled": True}
    assert session.session_manager.get_leaf_id() == a2
    generate.assert_not_called()
    assert session._branch_summary_abort_controller is None
    assert session._emit.call_count == 0


@pytest.mark.asyncio
async def test_navigate_emits_tree_event_with_bool_from_extension(tmp_path):
    """扩展 runner 在场（无 before_tree handler）时，无摘要导航也要干净发射
    SessionTreeEvent——from_extension 必须是 bool 而非 None。

    回归：PTY 实逮的 -32603（bool 字段收 None 校验爆炸，导航半完成：
    leaf 已迁移但事件发射崩溃、RPC 报错）。
    """
    session = _session(tmp_path)
    _, _, u2, _ = _four_message_branch(session)
    runner = MagicMock()
    runner.has_handlers.return_value = False
    runner.emit = AsyncMock(return_value=None)
    session._extension_runner = runner

    navigator = TreeNavigator(session)
    result = await navigator.navigate(u2)

    assert result["cancelled"] is False
    tree_events = [
        call.args[0]
        for call in runner.emit.call_args_list
        if getattr(call.args[0], "type", None) == "session_tree"
    ]
    assert len(tree_events) == 1
    assert tree_events[0].from_extension is False


@pytest.mark.asyncio
async def test_navigate_with_label_appends_label_change(tmp_path):
    """label 选项（无摘要）：标签落在目标条目上。"""
    session = _session(tmp_path)
    _, _, u2, _ = _four_message_branch(session)
    navigator = TreeNavigator(session)

    await navigator.navigate(u2, NavigateOptions(label="书签"))

    entries = session.session_manager.get_entries()
    label_entries = [e for e in entries if e.type == "label"]
    assert len(label_entries) == 1
    assert label_entries[0].target_id == u2
    assert label_entries[0].label == "书签"
