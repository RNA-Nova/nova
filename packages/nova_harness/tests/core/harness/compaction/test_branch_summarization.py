"""
分支摘要模块测试。

覆盖 collect_entries_for_branch_summary、prepare_branch_entries、generate_branch_summary。
"""

from typing import Optional

import pytest
from nova_ai import (
    AssistantMessage,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    TextContent,
    ToolCall,
    Usage,
    UserMessage,
)

import nova_harness.core.harness.compaction.branch_summarization as branch_module
from nova_harness.core.harness.compaction import (
    collect_entries_for_branch_summary,
    generate_branch_summary,
    prepare_branch_entries,
)
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.types.compaction import GenerateBranchSummaryOptions


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


def _user(text: str) -> UserMessage:
    return UserMessage(role="user", content=[TextContent(type="text", text=text)])


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text=text)],
        provider="test",
        model="test",
        stop_reason="stop",
        usage=Usage(),
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


@pytest.fixture
def session():
    return SessionManager(
        cwd="/tmp", session_dir="/tmp/nova-test", session_file=None, persist=False
    )


@pytest.fixture
def model():
    return _make_model()


@pytest.fixture
def branch_options(model):
    return GenerateBranchSummaryOptions(
        model=model, api_key="key", signal=None, reserve_tokens=1000
    )


# -----------------------------------------------------------------------------
# collect_entries_for_branch_summary
# -----------------------------------------------------------------------------


def test_collect_entries_no_old_leaf(session):
    u1 = session.append_message(_user("q"))  # noqa: F841
    result = collect_entries_for_branch_summary(session, None, u1)
    assert result.entries == []
    assert result.common_ancestor_id is None


def test_collect_entries_old_equals_target(session):
    u2 = session.append_message(_user("q"))  # noqa: F841
    a1 = session.append_message(_assistant("a"))  # noqa: F841
    result = collect_entries_for_branch_summary(session, a1, a1)
    assert result.entries == []
    assert result.common_ancestor_id == a1


def test_collect_entries_same_path_back_to_target(session):
    u1 = session.append_message(_user("q1"))
    a1 = session.append_message(_assistant("a1"))
    u2 = session.append_message(_user("q2"))
    a2 = session.append_message(_assistant("a2"))

    session.branch(a1)
    result = collect_entries_for_branch_summary(session, a2, a1)

    assert result.common_ancestor_id == a1
    assert any(e.id == u2 for e in result.entries)
    assert any(e.id == a2 for e in result.entries)
    assert all(e.id != u1 for e in result.entries)


def test_collect_entries_diverged_branches(session):
    session.append_message(_user("q1"))
    a1 = session.append_message(_assistant("a1"))
    u2 = session.append_message(_user("q2"))
    a2 = session.append_message(_assistant("a2"))

    # 从 a1 分出另一条分支
    session.branch(a1)
    u3 = session.append_message(_user("q3"))
    a3 = session.append_message(_assistant("a3"))

    result = collect_entries_for_branch_summary(session, a3, a2)
    assert result.common_ancestor_id == a1
    assert any(e.id == u3 for e in result.entries)
    assert any(e.id == a3 for e in result.entries)
    assert all(e.id not in (u2, a2) for e in result.entries)


# -----------------------------------------------------------------------------
# prepare_branch_entries
# -----------------------------------------------------------------------------


def test_prepare_branch_entries_respects_budget(session):
    u8 = session.append_message(_user("q1"))  # noqa: F841
    a7 = session.append_message(_assistant("a" * 400))  # noqa: F841
    u9 = session.append_message(_user("q2"))  # noqa: F841
    a8 = session.append_message(_assistant("b" * 400))  # noqa: F841

    prep = prepare_branch_entries(session.get_branch(), token_budget=150)
    assert prep.total_tokens <= 150
    assert 0 < len(prep.messages) <= 2


def test_prepare_branch_entries_hard_fits_summary(session):
    a1 = session.append_message(_assistant("a1" + "x" * 400))
    # 添加一个较长的分支摘要条目
    session.branch_with_summary(a1, "s" * 400)  # ~100 tokens

    prep = prepare_branch_entries(session.get_branch(), token_budget=50)
    assert any(getattr(m, "role", None) == "branchSummary" for m in prep.messages)


def test_prepare_branch_entries_cumulative_file_ops(session):
    session.append_message(_user("q1"))
    a1 = session.append_message(_tool_call("read", "/tmp/read.py"))
    session.branch_with_summary(
        a1,
        "summary",
        details={
            "read_files": ["/tmp/old.py"],
            "modified_files": ["/tmp/mod.py"],
        },
    )
    u11 = session.append_message(_user("q2"))  # noqa: F841
    session.append_message(_tool_call("write", "/tmp/new.py"))

    prep = prepare_branch_entries(session.get_branch())
    assert "/tmp/read.py" in prep.file_ops.read
    assert "/tmp/old.py" in prep.file_ops.read
    assert "/tmp/mod.py" in prep.file_ops.edited
    assert "/tmp/new.py" in prep.file_ops.written


# -----------------------------------------------------------------------------
# generate_branch_summary
# -----------------------------------------------------------------------------


async def test_generate_branch_summary_happy_path(session, branch_options, monkeypatch):
    u12 = session.append_message(_user("q"))  # noqa: F841
    a10 = session.append_message(_assistant("a"))  # noqa: F841

    async def fake_complete(*a, **k):
        return _summary_assistant("branch summary")

    monkeypatch.setattr(branch_module, "_complete_summarization", fake_complete)

    result = await generate_branch_summary(session.get_branch(), branch_options)
    assert result.summary is not None
    assert "Summary of that exploration:" in result.summary
    assert "branch summary" in result.summary
    assert result.aborted is False
    assert result.error is None


async def test_generate_branch_summary_aborted(session, branch_options, monkeypatch):
    u13 = session.append_message(_user("q"))  # noqa: F841

    async def fake_complete(*a, **k):
        return _summary_assistant("", stop_reason="aborted")

    monkeypatch.setattr(branch_module, "_complete_summarization", fake_complete)
    result = await generate_branch_summary(session.get_branch(), branch_options)
    assert result.aborted is True
    assert result.summary is None


async def test_generate_branch_summary_error(session, branch_options, monkeypatch):
    u14 = session.append_message(_user("q"))  # noqa: F841

    async def fake_complete(*a, **k):
        return _summary_assistant("", stop_reason="error", error_message="fail")

    monkeypatch.setattr(branch_module, "_complete_summarization", fake_complete)
    result = await generate_branch_summary(session.get_branch(), branch_options)
    assert result.error == "fail"


async def test_generate_branch_summary_no_messages(branch_options):
    result = await generate_branch_summary([], branch_options)
    assert result.summary == "No content to summarize"
