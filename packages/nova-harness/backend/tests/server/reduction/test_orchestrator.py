"""SessionReducer 表格测试：事件序列 → item 通知序列。

归约器是 server 侧的唯一翻译点——每条用例声明一段事件序列，断言产出的
通知序列（类型 + 载荷关键字段）与在飞台账状态。设计见
``examples/server-item-layer-design.md`` §5。
"""

from typing import Any, List, Literal, Optional

import pytest
from nova_agent import CustomAgentMessage
from nova_ai import (
    AssistantMessage,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    UserMessage,
)

from nova_harness.core.types.compaction import CompactionResult
from nova_harness.core.types.events import (
    AgentEndEvent,
    EntryAppendedEvent,
    ItemEmissionEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    SessionReplacedEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from nova_harness.core.types.events.session import CompactionEndEvent
from nova_harness.core.types.messages import (
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
)
from nova_harness.core.types.session.entries import CustomEntry, LabelEntry
from nova_harness.server.reduction import SessionReducer, apply_delta
from nova_harness.server.types.items import (
    AgentMessageItem,
    CustomItem,
    ItemStatus,
    NovaItem,
    ThinkingItem,
    ToolCallItem,
    UserMessageItem,
)


@pytest.fixture
def emitted():
    """收集归约产物的归约器：返回 (reducer, 通知快照列表)。

    深拷贝对齐线上语义——broadcast 在发射时刻序列化（dump_wire），
    帧是快照而非活引用；这里同理固化每一帧的"发射时刻状态"。
    """
    import copy

    notifications: List[Any] = []
    reducer = SessionReducer(emit=lambda n: notifications.append(copy.deepcopy(n)))
    return reducer, notifications


def _types(notifications: List[Any]) -> List[str]:
    return [n.type for n in notifications]


def _assistant(content, stop_reason=StopReason.STOP) -> AssistantMessage:
    return AssistantMessage(content=content, stop_reason=stop_reason)


# ---------------------------------------------------------------------------
# 用户消息
# ---------------------------------------------------------------------------


def test_user_message_one_shot(emitted):
    reducer, out = emitted
    reducer.handle_event(
        MessageStartEvent(message=UserMessage(content="你好", timestamp=7))
    )
    assert _types(out) == ["item_started", "item_completed"]
    item = out[0].item
    assert isinstance(item, UserMessageItem)
    assert item.source == "user"
    assert item.ts == 7
    assert [b.text for b in item.content] == ["你好"]


# ---------------------------------------------------------------------------
# 助手流式
# ---------------------------------------------------------------------------


def test_assistant_text_stream_suffix_deltas(emitted):
    reducer, out = emitted
    msg = _assistant([])
    reducer.handle_event(MessageStartEvent(message=msg))
    msg.content = [TextContent(text="Hel")]
    reducer.handle_event(MessageUpdateEvent(message=msg))
    msg.content = [TextContent(text="Hello")]
    reducer.handle_event(MessageUpdateEvent(message=msg))
    reducer.handle_event(MessageEndEvent(message=msg))

    assert _types(out) == ["item_started", "item_delta", "item_delta", "item_completed"]
    started = out[0].item
    assert (
        isinstance(started, AgentMessageItem) and started.status is ItemStatus.RUNNING
    )
    # 快照 → 后缀增量（记上次长度取后缀）
    assert out[1].delta == {"text": "Hel"}
    assert out[2].delta == {"text": "lo"}
    final = out[3].item
    assert final.text == "Hello" and final.status is ItemStatus.DONE
    assert reducer.in_flight_items() == []


def test_assistant_thinking_and_text_interleaved(emitted):
    reducer, out = emitted
    msg = _assistant([])
    reducer.handle_event(MessageStartEvent(message=msg))
    msg.content = [ThinkingContent(thinking="想"), TextContent(text="答")]
    reducer.handle_event(MessageUpdateEvent(message=msg))
    reducer.handle_event(MessageEndEvent(message=msg))

    started_items = [n.item for n in out if n.type == "item_started"]
    assert [type(i) for i in started_items] == [ThinkingItem, AgentMessageItem]
    assert [n.item.text for n in out if n.type == "item_completed"] == ["想", "答"]


def test_assistant_aborted_marks_cancelled(emitted):
    reducer, out = emitted
    msg = _assistant([TextContent(text="半截")], stop_reason=StopReason.ABORTED)
    reducer.handle_event(MessageStartEvent(message=msg))
    reducer.handle_event(MessageUpdateEvent(message=msg))
    reducer.handle_event(MessageEndEvent(message=msg))
    assert out[-1].item.status is ItemStatus.CANCELLED


# ---------------------------------------------------------------------------
# LLM 工具执行
# ---------------------------------------------------------------------------


def test_tool_call_full_lifecycle(emitted):
    reducer, out = emitted
    reducer.handle_event(
        ToolExecutionStartEvent(tool_call_id="tc1", tool_name="read", args={"p": 1})
    )
    reducer.handle_event(
        ToolExecutionUpdateEvent(
            tool_call_id="tc1", tool_name="read", partial_result="部分"
        )
    )
    reducer.handle_event(
        ToolExecutionEndEvent(tool_call_id="tc1", tool_name="read", result={"ok": True})
    )

    assert _types(out) == ["item_started", "item_delta", "item_completed"]
    started = out[0].item
    assert isinstance(started, ToolCallItem)
    assert started.id == "tc1" and started.tool == "read"
    assert started.status is ItemStatus.RUNNING and started.source == "agent"
    assert out[1].delta == {"partialResult": "部分"}  # wire 键 camelCase
    final = out[2].item
    assert final.status is ItemStatus.DONE
    assert final.result == {"ok": True}
    assert final.partial_result is None  # 定稿清空瞬态
    assert final.duration_ms is not None and final.duration_ms >= 0


def test_tool_call_error_end(emitted):
    reducer, out = emitted
    reducer.handle_event(ToolExecutionStartEvent(tool_call_id="tc2", tool_name="bash"))
    result = type(
        "R", (), {"content": [TextContent(text="boom")]}
    )()  # 鸭型结果（extract_text_from_content 兼容）
    reducer.handle_event(
        ToolExecutionEndEvent(
            tool_call_id="tc2", tool_name="bash", result=result, is_error=True
        )
    )
    final = out[-1].item
    assert final.status is ItemStatus.FAILED
    assert final.error == "boom"


def test_tool_end_without_start_synthesizes(emitted):
    """防御：start/end 本应配对；缺失 start 时补建再定稿，客户端总能看到完整生命周期。"""
    reducer, out = emitted
    reducer.handle_event(ToolExecutionEndEvent(tool_call_id="tc9", tool_name="x"))
    assert _types(out) == ["item_started", "item_completed"]
    assert out[-1].item.status is ItemStatus.DONE


# ---------------------------------------------------------------------------
# 自定义消息与包级消息
# ---------------------------------------------------------------------------


def test_custom_message_maps_custom_item(emitted):
    reducer, out = emitted
    reducer.handle_event(
        MessageStartEvent(
            message=CustomMessage(
                custom_type="notice", content="hi", display=True, timestamp=3
            )
        )
    )
    item = out[0].item
    assert isinstance(item, CustomItem) and item.type == "notice"
    assert item.details["content"] == "hi"


def test_custom_message_not_displayed_produces_nothing(emitted):
    reducer, out = emitted
    reducer.handle_event(
        MessageStartEvent(
            message=CustomMessage(
                custom_type="hidden", content="x", display=False, timestamp=0
            )
        )
    )
    assert out == []


class _PkgMessage(CustomAgentMessage):
    """带 to_item 的包级消息（测试夹具）。"""

    role: Literal["pkgThing"] = "pkgThing"
    item_id: str = ""
    value: str = ""
    timestamp: int = 0

    def to_item(self) -> NovaItem:
        return CustomItem(
            id=self.item_id, type="pkgThing", ts=self.timestamp, details=self.value
        )


def test_package_message_completes_streamed_item(emitted):
    """包已提前 started 的 item：record 的消息是权威定稿，不重复 started。"""
    reducer, out = emitted
    pre = CustomItem(id="p1", type="pkgThing", status=ItemStatus.RUNNING)
    reducer.handle_event(ItemEmissionEvent(phase="started", item=pre))
    reducer.handle_event(
        MessageStartEvent(message=_PkgMessage(item_id="p1", value="终态"))
    )
    assert _types(out) == ["item_started", "item_completed"]
    assert out[-1].item.details == "终态"
    assert reducer.in_flight_items() == []


def test_package_message_without_stream_one_shot(emitted):
    reducer, out = emitted
    reducer.handle_event(MessageStartEvent(message=_PkgMessage(value="直接")))
    assert _types(out) == ["item_started", "item_completed"]
    assert out[0].item.id  # 归约器补铸 id


def test_package_message_without_to_item_falls_back(emitted):
    class BareMessage(CustomAgentMessage):
        role: Literal["bare"] = "bare"
        payload: str = ""
        timestamp: int = 0

    reducer, out = emitted
    reducer.handle_event(MessageStartEvent(message=BareMessage(payload="数据")))
    item = out[0].item
    assert isinstance(item, CustomItem) and item.type == "bare"
    assert item.details["payload"] == "数据"


# ---------------------------------------------------------------------------
# item_emission 承接（类型边界）
# ---------------------------------------------------------------------------


def test_item_emission_delta_and_garbage(emitted):
    reducer, out = emitted
    item = CustomItem(id="e1", type="bashExecution", output="a")
    reducer.handle_event(ItemEmissionEvent(phase="started", item=item))
    reducer.handle_event(
        ItemEmissionEvent(phase="delta", item_id="e1", delta={"output": "b"})
    )
    # 垃圾载荷：非 NovaItem——丢弃 + 不产生帧
    reducer.handle_event(ItemEmissionEvent(phase="started", item={"not": "item"}))
    # 未知 id 的 delta——忽略
    reducer.handle_event(
        ItemEmissionEvent(phase="delta", item_id="nobody", delta={"x": 1})
    )

    assert _types(out) == ["item_started", "item_delta"]
    assert out[1].delta == {"output": "b"}
    # 在飞台账同步合并（字符串追加）
    assert reducer.in_flight_items()[0].output == "ab"


# ---------------------------------------------------------------------------
# 压缩 / 中断 / 会话替换
# ---------------------------------------------------------------------------


def test_compaction_end_produces_item(emitted):
    reducer, out = emitted
    reducer.handle_event(
        CompactionEndEvent(
            reason="manual",
            result=CompactionResult(
                summary="摘要", first_kept_entry_id="e3", tokens_before=100
            ),
        )
    )
    assert _types(out) == ["item_started", "item_completed"]
    assert out[0].item.type == "compaction"
    assert out[0].item.summary == "摘要"

    reducer.handle_event(CompactionEndEvent(reason="manual", result=None, aborted=True))
    assert len(out) == 2  # aborted 不产 item


def test_agent_end_sweeps_in_flight_as_cancelled(emitted):
    reducer, out = emitted
    reducer.handle_event(ToolExecutionStartEvent(tool_call_id="t1", tool_name="bash"))
    msg = _assistant([TextContent(text="半截")])
    reducer.handle_event(MessageStartEvent(message=msg))
    reducer.handle_event(MessageUpdateEvent(message=msg))

    reducer.handle_event(AgentEndEvent(run_id="r1"))

    swept = [n.item for n in out if n.type == "item_completed"]
    assert len(swept) == 2  # 在飞工具 + 流式文本块
    assert "t1" in {i.id for i in swept}
    assert all(i.status is ItemStatus.CANCELLED for i in swept)
    assert reducer.in_flight_items() == []


def test_session_replaced_clears_state(emitted):
    reducer, out = emitted
    reducer.handle_event(ToolExecutionStartEvent(tool_call_id="t1", tool_name="bash"))
    reducer.handle_event(SessionReplacedEvent(reason="new"))
    assert reducer.in_flight_items() == []
    # 替换不产 completed——内容整体替换由客户端 resync 覆盖
    assert [n for n in out if n.type == "item_completed"] == []


def test_recovery_only_messages_produce_nothing(emitted):
    reducer, out = emitted
    reducer.handle_event(
        MessageStartEvent(
            message=CompactionSummaryMessage(summary="s", tokens_before=1, timestamp=1)
        )
    )
    reducer.handle_event(
        MessageStartEvent(
            message=BranchSummaryMessage(summary="s", from_id="e1", timestamp=1)
        )
    )
    assert out == []


# ---------------------------------------------------------------------------
# apply_delta 合并规则
# ---------------------------------------------------------------------------


def test_apply_delta_append_whitelist_and_alias_resolution():
    item = ToolCallItem(id="t", partial_result="a")
    apply_delta(item, {"partialResult": "b"})  # 白名单外字段：替换（不追加）
    assert item.partial_result == "b"
    apply_delta(item, {"partialResult": None})  # 非字符串替换
    assert item.partial_result is None


def test_apply_delta_status_enum_replaces_not_appends():
    """str-Enum 字段按替换合并——值类型猜测会拼出 'pendingrunning'。"""
    item = ToolCallItem(id="t", status=ItemStatus.PENDING)
    apply_delta(item, {"status": ItemStatus.RUNNING})
    assert item.status is ItemStatus.RUNNING


def test_apply_delta_extra_fields_on_custom_item():
    item = CustomItem(id="c", type="x", output="a")
    apply_delta(item, {"output": "b"})
    assert item.output == "ab"
    apply_delta(item, {"exitCode": 0})  # 未声明键落 extra 区
    assert item.model_dump(mode="json").get("exitCode") == 0 or item.exitCode == 0


# ---------------------------------------------------------------------------
# D1：toolCall 块参数流式建卡（PENDING）+ AgentMessageItem.error
# ---------------------------------------------------------------------------


def test_tool_call_pending_streams_args_then_runs(emitted):
    """pi 两阶段卡片：参数流式期即建 PENDING 卡，执行开始转 RUNNING。"""
    reducer, out = emitted
    msg = _assistant([])
    reducer.handle_event(MessageStartEvent(message=msg))
    # 参数部分到达（nova_ai 增量解析：arguments 为部分填充对象）
    msg.content = [ToolCall(id="tc1", name="edit", arguments={"path": "a.py"})]
    reducer.handle_event(MessageUpdateEvent(message=msg))
    msg.content = [
        ToolCall(id="tc1", name="edit", arguments={"path": "a.py", "old": "x"})
    ]
    reducer.handle_event(MessageUpdateEvent(message=msg))
    # message_end 不收 PENDING 工具卡（执行窗口在后）
    reducer.handle_event(MessageEndEvent(message=msg))
    # 执行开始：PENDING → RUNNING + 权威 args
    reducer.handle_event(
        ToolExecutionStartEvent(
            tool_call_id="tc1",
            tool_name="edit",
            args={"path": "a.py", "old": "x", "new": "y"},
        )
    )
    reducer.handle_event(
        ToolExecutionEndEvent(tool_call_id="tc1", tool_name="edit", result={"ok": True})
    )

    assert _types(out) == [
        "item_started",  # PENDING 建卡
        "item_delta",  # args 增长
        "item_delta",  # args_complete 标记（message_end——预览时点）
        "item_delta",  # PENDING → RUNNING + 权威 args
        "item_completed",
    ]
    started = out[0].item
    assert isinstance(started, ToolCallItem)
    assert started.status is ItemStatus.PENDING
    assert started.args == {"path": "a.py"}
    assert out[1].delta == {"args": {"path": "a.py", "old": "x"}}
    assert out[2].delta == {"argsComplete": True}
    assert out[3].delta["status"] is ItemStatus.RUNNING
    assert out[4].item.status is ItemStatus.DONE


def test_pending_tool_card_swept_on_abort(emitted):
    """参数流式中建卡、执行未开始即 abort：PENDING 卡按 cancelled 定稿。"""
    reducer, out = emitted
    msg = _assistant([ToolCall(id="tc7", name="write", arguments={"path": "b.py"})])
    reducer.handle_event(MessageStartEvent(message=msg))
    reducer.handle_event(MessageUpdateEvent(message=msg))
    reducer.handle_event(AgentEndEvent(run_id="r1"))
    swept = [n.item for n in out if n.type == "item_completed"]
    assert len(swept) == 1
    assert swept[0].id == "tc7" and swept[0].status is ItemStatus.CANCELLED


def test_failed_assistant_end_attaches_error_text(emitted):
    reducer, out = emitted
    msg = _assistant([TextContent(text="半")])
    msg.stop_reason = StopReason.ERROR
    msg.error_message = "context overflow: 超过模型上下文窗口"
    reducer.handle_event(MessageStartEvent(message=msg))
    reducer.handle_event(MessageUpdateEvent(message=msg))
    reducer.handle_event(MessageEndEvent(message=msg))
    final = out[-1].item
    assert final.status is ItemStatus.FAILED
    assert final.error == "context overflow: 超过模型上下文窗口"


def test_custom_entry_appended_maps_custom_item(emitted):
    """custom 条目实时路径：entry_appended → CustomItem 一次性两帧。"""
    reducer, out = emitted
    entry = CustomEntry(
        id="e1", custom_type="command_result", data={"text": "已信任", "level": "info"}
    )
    reducer.handle_event(EntryAppendedEvent(entry=entry))
    assert _types(out) == ["item_started", "item_completed"]
    item = out[0].item
    assert isinstance(item, CustomItem)
    assert item.type == "command_result"
    assert item.id == "e1"
    assert item.details == {"text": "已信任", "level": "info"}


def test_non_custom_entry_appended_ignored(emitted):
    reducer, out = emitted
    reducer.handle_event(
        EntryAppendedEvent(entry=LabelEntry(id="l1", target_id="e1", label="x"))
    )
    assert out == []
