"""
TreeNavigator 单元测试。

覆盖会话树导航、分支摘要与 fork 选择器。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nova_ai import AssistantMessage, TextContent, UserMessage

from nova_harness.core.types.events import SessionTreeEvent


@pytest.fixture
def tree_session(make_agent_session):
    """构造一个带 mock extension runner 的 session。"""
    sess = make_agent_session()
    runner = MagicMock()
    runner.has_handlers.return_value = False
    runner.emit = AsyncMock()
    sess._extension_runner = runner
    sess.model_registry.get_api_key = AsyncMock(return_value="fake-key")
    return sess


@pytest.mark.asyncio
async def test_navigate_same_leaf_returns_cancelled_false(tree_session):
    """目标就是当前 leaf 时直接返回。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"
    result = await tree_session._tree.navigate("leaf-1")
    assert result == {"cancelled": False}


@pytest.mark.asyncio
async def test_navigate_entry_not_found_raises(tree_session):
    """目标 entry 不存在时抛 ValueError。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"
    tree_session.session_manager.get_entry.return_value = None
    with pytest.raises(ValueError, match="Entry target-1 not found"):
        await tree_session._tree.navigate("target-1")


@pytest.mark.asyncio
async def test_navigate_summarize_requires_model(tree_session):
    """summarize=True 但无模型时抛 RuntimeError。"""
    tree_session.agent.state.model = None
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"
    tree_session.session_manager.get_entry.return_value = MagicMock(type="message")
    with pytest.raises(RuntimeError, match="No model available"):
        await tree_session._tree.navigate("target-1", {"summarize": True})


@pytest.mark.asyncio
async def test_navigate_default_branches_to_parent(tree_session):
    """导航到用户消息时应在 parent 处 branch。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"
    tree_session.session_manager.get_entry.return_value = SimpleNamespace(
        type="message",
        message=UserMessage(role="user", content=[TextContent(text="hello")]),
        parent_id="parent-1",
        id="target-1",
    )
    tree_session.session_manager.build_session_context.return_value = MagicMock(
        messages=["msg"]
    )

    with patch(
        "nova_harness.core.agent_session.controllers.tree._branch_module.collect_entries_for_branch_summary",
        return_value=MagicMock(entries=[], common_ancestor_id=None),
    ):
        result = await tree_session._tree.navigate("target-1")

    assert result["cancelled"] is False
    assert result["editorText"] == "hello"
    tree_session.session_manager.branch.assert_called_once_with("parent-1")
    assert tree_session.agent.state.messages == ["msg"]


@pytest.mark.asyncio
async def test_navigate_custom_message_target(tree_session):
    """导航到 custom_message 时提取 content 作为 editorText。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"
    tree_session.session_manager.get_entry.return_value = SimpleNamespace(
        type="custom_message",
        content="custom note",
        parent_id="parent-2",
        id="target-2",
    )
    tree_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[]
    )

    with patch(
        "nova_harness.core.agent_session.controllers.tree._branch_module.collect_entries_for_branch_summary",
        return_value=MagicMock(entries=[], common_ancestor_id=None),
    ):
        result = await tree_session._tree.navigate("target-2")

    assert result["editorText"] == "custom note"
    tree_session.session_manager.branch.assert_called_once_with("parent-2")


@pytest.mark.asyncio
async def test_navigate_non_message_target(tree_session):
    """导航到非消息节点时直接 branch 到目标。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"
    tree_session.session_manager.get_entry.return_value = SimpleNamespace(
        type="compaction",
        parent_id="parent-3",
        id="target-3",
    )
    tree_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[]
    )

    with patch(
        "nova_harness.core.agent_session.controllers.tree._branch_module.collect_entries_for_branch_summary",
        return_value=MagicMock(entries=[], common_ancestor_id=None),
    ):
        result = await tree_session._tree.navigate("target-3")

    assert result["editorText"] is None
    tree_session.session_manager.branch.assert_called_once_with("target-3")


@pytest.mark.asyncio
async def test_navigate_emits_session_tree_event(tree_session):
    """导航完成后应发射 SessionTreeEvent。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"
    tree_session.session_manager.get_entry.return_value = SimpleNamespace(
        type="compaction",
        parent_id="parent-3",
        id="target-3",
    )
    tree_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[]
    )
    with patch(
        "nova_harness.core.agent_session.controllers.tree._branch_module.collect_entries_for_branch_summary",
        return_value=MagicMock(entries=[], common_ancestor_id=None),
    ):
        await tree_session._tree.navigate("target-3")

    tree_session._extension_runner.emit.assert_awaited_once()
    ev = tree_session._extension_runner.emit.call_args[0][0]
    assert isinstance(ev, SessionTreeEvent)
    assert ev.old_leaf_id == "leaf-1"


@pytest.mark.asyncio
async def test_navigate_with_summary(tree_session):
    """summarize=True 时生成分支摘要并创建 summary entry。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"
    tree_session.session_manager.get_entry.return_value = SimpleNamespace(
        type="compaction",
        parent_id="parent-3",
        id="target-3",
    )
    summary_entry = SimpleNamespace(id="summary-1")
    tree_session.session_manager.branch_with_summary.return_value = "summary-1"
    tree_session.session_manager.get_entry.return_value = summary_entry
    tree_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[]
    )

    # 需要同时支持 get_entry 的两种调用（获取 target 与 summary）
    def _entry_side(entry_id):
        if entry_id == "target-3":
            return SimpleNamespace(
                type="compaction", parent_id="parent-3", id="target-3"
            )
        return summary_entry

    tree_session.session_manager.get_entry.side_effect = _entry_side

    with (
        patch(
            "nova_harness.core.agent_session.controllers.tree._branch_module.collect_entries_for_branch_summary",
            return_value=MagicMock(
                entries=[SimpleNamespace()], common_ancestor_id=None
            ),
        ),
        patch(
            "nova_harness.core.agent_session.controllers.tree._branch_module.generate_branch_summary",
            AsyncMock(
                return_value=MagicMock(
                    summary="summary",
                    read_files=["a.py"],
                    modified_files=["b.py"],
                    aborted=False,
                    error=None,
                )
            ),
        ),
    ):
        result = await tree_session._tree.navigate(
            "target-3", {"summarize": True, "replace_instructions": False}
        )

    assert result["cancelled"] is False
    assert result["summaryEntry"] is summary_entry
    tree_session.session_manager.branch_with_summary.assert_called_once()


@pytest.mark.asyncio
async def test_navigate_summary_aborted_returns_cancelled(tree_session):
    """摘要生成被取消时返回 cancelled。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"

    def _entry_side(entry_id):
        return SimpleNamespace(type="compaction", parent_id="parent-3", id="target-3")

    tree_session.session_manager.get_entry.side_effect = _entry_side
    tree_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[]
    )

    with (
        patch(
            "nova_harness.core.agent_session.controllers.tree._branch_module.collect_entries_for_branch_summary",
            return_value=MagicMock(
                entries=[SimpleNamespace()], common_ancestor_id=None
            ),
        ),
        patch(
            "nova_harness.core.agent_session.controllers.tree._branch_module.generate_branch_summary",
            AsyncMock(return_value=MagicMock(aborted=True)),
        ),
    ):
        result = await tree_session._tree.navigate("target-3", {"summarize": True})

    assert result["cancelled"] is True
    assert result.get("aborted") is True


@pytest.mark.asyncio
async def test_navigate_summary_error_raises(tree_session):
    """摘要生成报错时应抛 RuntimeError。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"

    def _entry_side(entry_id):
        return SimpleNamespace(type="compaction", parent_id="parent-3", id="target-3")

    tree_session.session_manager.get_entry.side_effect = _entry_side
    tree_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[]
    )

    with (
        patch(
            "nova_harness.core.agent_session.controllers.tree._branch_module.collect_entries_for_branch_summary",
            return_value=MagicMock(
                entries=[SimpleNamespace()], common_ancestor_id=None
            ),
        ),
        patch(
            "nova_harness.core.agent_session.controllers.tree._branch_module.generate_branch_summary",
            AsyncMock(return_value=MagicMock(error="boom", aborted=False)),
        ),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await tree_session._tree.navigate(
                "target-3", {"summarize": True, "replace_instructions": False}
            )


@pytest.mark.asyncio
async def test_navigate_extension_cancel(tree_session):
    """session_before_tree 扩展 hook 取消时返回 cancelled。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"
    tree_session.session_manager.get_entry.return_value = SimpleNamespace(
        type="compaction",
        parent_id="parent-3",
        id="target-3",
    )
    tree_session._extension_runner.has_handlers.return_value = True
    tree_session._extension_runner.emit = AsyncMock(return_value=MagicMock(cancel=True))

    with patch(
        "nova_harness.core.agent_session.controllers.tree._branch_module.collect_entries_for_branch_summary",
        return_value=MagicMock(entries=[], common_ancestor_id=None),
    ):
        result = await tree_session._tree.navigate("target-3")

    assert result["cancelled"] is True


@pytest.mark.asyncio
async def test_navigate_extension_summary(tree_session):
    """session_before_tree 扩展 hook 提供 summary 时应使用。"""
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"
    tree_session.session_manager.get_entry.return_value = SimpleNamespace(
        type="compaction",
        parent_id="parent-3",
        id="target-3",
    )
    summary_entry = SimpleNamespace(id="summary-2")
    tree_session.session_manager.branch_with_summary.return_value = "summary-2"

    def _entry_side(entry_id):
        if entry_id == "target-3":
            return SimpleNamespace(
                type="compaction", parent_id="parent-3", id="target-3"
            )
        return summary_entry

    tree_session.session_manager.get_entry.side_effect = _entry_side
    tree_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[]
    )
    tree_session._extension_runner.has_handlers.return_value = True
    tree_session._extension_runner.emit = AsyncMock(
        return_value=MagicMock(
            cancel=False,
            summary=MagicMock(summary="ext-summary", details=None),
        )
    )

    with patch(
        "nova_harness.core.agent_session.controllers.tree._branch_module.collect_entries_for_branch_summary",
        return_value=MagicMock(entries=[SimpleNamespace()], common_ancestor_id=None),
    ):
        result = await tree_session._tree.navigate("target-3", {"summarize": True})

    assert result["summaryEntry"] is summary_entry


@pytest.mark.asyncio
async def test_navigate_summary_no_api_key_raises(tree_session):
    """生成摘要缺少 API key 时应抛错。"""
    tree_session.model_registry.get_api_key = AsyncMock(return_value="")
    tree_session.session_manager.get_leaf_id.return_value = "leaf-1"

    def _entry_side(entry_id):
        return SimpleNamespace(type="compaction", parent_id="parent-3", id="target-3")

    tree_session.session_manager.get_entry.side_effect = _entry_side
    tree_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[]
    )

    with patch(
        "nova_harness.core.agent_session.controllers.tree._branch_module.collect_entries_for_branch_summary",
        return_value=MagicMock(entries=[SimpleNamespace()], common_ancestor_id=None),
    ):
        with pytest.raises(RuntimeError, match="No API key"):
            await tree_session._tree.navigate("target-3", {"summarize": True})


def test_get_user_messages_for_forking(tree_session):
    """应只返回用户消息及其 entry id。"""
    tree_session.session_manager.get_entries.return_value = [
        SimpleNamespace(
            type="message",
            message=UserMessage(role="user", content=[TextContent(text="hi")]),
            id="e1",
        ),
        SimpleNamespace(
            type="message",
            message=AssistantMessage(
                role="assistant", content=[TextContent(text="ok")]
            ),
            id="e2",
        ),
        SimpleNamespace(type="custom", id="e3"),
    ]
    result = tree_session._tree.get_user_messages_for_forking()
    assert result == [{"entryId": "e1", "text": "hi"}]
