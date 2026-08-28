"""item 层类型测试（``server/types/items.py``）。

覆盖：
- 框架变体判别与线上形态（camelCase alias、ItemStatus 取 value）；
- ``FrameworkItem`` 判别联合 round-trip 与未知 type 拒绝；
- ``CustomItem`` 额外字段透传（包级变体的线上兜底形态）。
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from nova_harness.server.types.items import (
    AgentMessageItem,
    BranchSummaryItem,
    CompactionItem,
    CustomItem,
    FrameworkItem,
    ItemStatus,
    ThinkingItem,
    ToolCallItem,
    UserMessageItem,
)

# ---------------------------------------------------------------------------
# 框架变体
# ---------------------------------------------------------------------------


def test_user_message_item_wire_dump():
    item = UserMessageItem(id="i1", ts=123, content=[{"type": "text", "text": "hi"}])
    dumped = item.dump_wire()
    assert dumped["type"] == "userMessage"
    assert dumped["content"] == [{"type": "text", "text": "hi", "textSignature": None}]
    # 骨架字段齐全（线上恒含）
    assert dumped["status"] is None
    assert dumped["source"] is None


def test_tool_call_item_full_fields():
    item = ToolCallItem(
        id="t1",
        ts=1,
        status=ItemStatus.RUNNING,
        source="agent",
        tool="bash",
        args={"command": "ls"},
    )
    dumped = item.dump_wire()
    assert dumped["type"] == "toolCall"
    assert dumped["status"] == "running"  # Enum 取 value
    assert dumped["durationMs"] is None  # camelCase alias
    item.status = ItemStatus.DONE
    item.result = {"output": "ok"}
    item.duration_ms = 42
    assert item.dump_wire()["durationMs"] == 42


def test_remaining_variants_discriminators():
    assert AgentMessageItem(text="a").type == "agentMessage"
    assert ThinkingItem(text="t").type == "thinking"
    assert CompactionItem(summary="s", tokens_before=10).type == "compaction"
    branch = BranchSummaryItem(summary="s", from_id="e9")
    assert branch.type == "branchSummary"
    assert branch.dump_wire()["fromId"] == "e9"


# ---------------------------------------------------------------------------
# 判别联合
# ---------------------------------------------------------------------------


def test_framework_item_union_roundtrip():
    adapter = TypeAdapter(FrameworkItem)
    item = adapter.validate_python(
        {"id": "t1", "type": "toolCall", "ts": 1, "tool": "read", "status": "done"}
    )
    assert isinstance(item, ToolCallItem)
    assert item.status is ItemStatus.DONE  # 内存中保持 Enum 对象
    assert (
        adapter.validate_python({"type": "thinking", "text": "x"}).__class__
        is ThinkingItem
    )


def test_framework_item_union_rejects_unknown_type():
    adapter = TypeAdapter(FrameworkItem)
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "bashExecution", "id": "x"})


# ---------------------------------------------------------------------------
# CustomItem（包级兜底）
# ---------------------------------------------------------------------------


def test_custom_item_passes_through_extra_fields():
    item = CustomItem(id="b1", type="bashExecution", command="ls -la", exit_code=0)
    dumped = item.dump_wire()
    assert dumped["type"] == "bashExecution"
    assert dumped["command"] == "ls -la"  # 额外字段透传
    assert dumped["exit_code"] == 0  # extra 不走红线 alias，原样保留
    # round-trip：额外字段不丢
    reparsed = CustomItem.model_validate(dumped)
    assert reparsed.command == "ls -la"  # type: ignore[attr-defined]


def test_custom_item_default_type():
    assert CustomItem().type == "custom"
