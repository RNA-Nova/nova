"""恢复读（entries_to_items）与同形性金标测试。

同形性（设计 §5.3）：**同一会话内容，实时事件流归约出的终态 item 清单 ==
恢复读（条目→item）产出的清单**。本文件用合成会话双路径各跑一遍，
归一化（id 按序重写、ts/durationMs/compaction reason 剔除）后逐字段相等。
"""

from typing import Any, List, Literal, Optional

from nova_agent import AgentToolResult, CustomAgentMessage
from nova_ai import (
    AssistantMessage,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from nova_harness.core.types.compaction import CompactionResult
from nova_harness.core.types.events import (
    AgentEndEvent,
    ItemEmissionEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from nova_harness.core.types.events.session import CompactionEndEvent
from nova_harness.core.types.messages import CustomMessage
from nova_harness.core.types.session.entries import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    LabelEntry,
    ModelChangeEntry,
    SessionMessageEntry,
)
from nova_harness.server.reduction import SessionReducer, entries_to_items
from nova_harness.server.types.items import (
    CustomItem,
    ItemStatus,
    NovaItem,
)


class _PkgItem(NovaItem):
    """测试用包级 item 变体。"""

    type: Literal["pkgThing"] = "pkgThing"
    value: str = ""


class _PkgMessage(CustomAgentMessage):
    """带 to_item + item_id 的包级消息（bash 形态的对照夹具）。"""

    role: Literal["pkgThing"] = "pkgThing"
    item_id: str = ""
    value: str = ""
    timestamp: int = 0

    def to_item(self) -> NovaItem:
        return _PkgItem(
            id=self.item_id,
            status=ItemStatus.DONE,
            source="user",
            ts=self.timestamp,
            value=self.value,
        )


# ---------------------------------------------------------------------------
# 恢复读单测
# ---------------------------------------------------------------------------


def test_user_message_entry():
    entry = SessionMessageEntry(
        id="e1", message=UserMessage(content="你好", timestamp=11)
    )
    items = entries_to_items([entry])
    assert len(items) == 1
    assert items[0].id == "e1"
    assert items[0].type == "userMessage"
    assert items[0].ts == 11


def test_assistant_entry_decomposition_and_tool_pairing():
    assistant = AssistantMessage(
        content=[
            ThinkingContent(thinking="想"),
            TextContent(text="答"),
            ToolCall(id="tc1", name="read", arguments={"p": "x"}),
        ],
        stop_reason=StopReason.TOOL_USE,
        timestamp=22,
    )
    result_msg = ToolResultMessage(
        tool_call_id="tc1",
        tool_name="read",
        content=[TextContent(text="内容")],
        details={"k": 1},
        timestamp=23,
    )
    entries = [
        SessionMessageEntry(id="e2", message=assistant),
        SessionMessageEntry(id="e3", message=result_msg),
    ]
    items = entries_to_items(entries)
    assert [i.type for i in items] == ["thinking", "agentMessage", "toolCall"]
    assert items[0].id == "e2:th0" and items[0].text == "想"
    assert items[1].id == "e2:t1" and items[1].text == "答"
    tool = items[2]
    assert tool.id == "tc1" and tool.tool == "read"  # 两路径同身份
    assert tool.status is ItemStatus.DONE
    assert tool.result.content[0].text == "内容"
    assert tool.result.details == {"k": 1}


def test_tool_call_without_result_is_cancelled():
    """中断语义：无配对结果的 toolCall 恢复为 cancelled（而非永远 running）。"""
    assistant = AssistantMessage(
        content=[ToolCall(id="tc9", name="bash", arguments={"c": "sleep"})],
        stop_reason=StopReason.ABORTED,
        timestamp=1,
    )
    items = entries_to_items([SessionMessageEntry(id="e4", message=assistant)])
    assert len(items) == 1
    assert items[0].status is ItemStatus.CANCELLED
    assert items[0].result is None


def test_error_tool_result_maps_failed():
    assistant = AssistantMessage(
        content=[ToolCall(id="tc2", name="write", arguments={})], timestamp=1
    )
    result_msg = ToolResultMessage(
        tool_call_id="tc2",
        tool_name="write",
        content=[TextContent(text="权限被拒")],
        is_error=True,
        timestamp=2,
    )
    items = entries_to_items(
        [
            SessionMessageEntry(id="e5", message=assistant),
            SessionMessageEntry(id="e6", message=result_msg),
        ]
    )
    assert items[0].status is ItemStatus.FAILED
    assert items[0].error == "权限被拒"


def test_package_message_via_to_item():
    msg = _PkgMessage(item_id="p1", value="数据", timestamp=5)
    items = entries_to_items([SessionMessageEntry(id="e7", message=msg)])
    assert len(items) == 1
    assert isinstance(items[0], _PkgItem)
    assert items[0].id == "p1"  # item_id 随消息落盘——与实时同 id
    assert items[0].value == "数据"


def test_compaction_and_branch_summary_entries():
    items = entries_to_items(
        [
            CompactionEntry(
                id="c1", summary="摘要", first_kept_entry_id="e3", tokens_before=99
            ),
            BranchSummaryEntry(id="b1", summary="分支", from_id="e2"),
        ]
    )
    assert [i.type for i in items] == ["compaction", "branchSummary"]
    assert items[0].summary == "摘要" and items[0].tokens_before == 99
    assert items[1].from_id == "e2"


def test_custom_message_entry_maps_custom_item():
    shown = CustomMessageEntry(
        id="m1", custom_type="notice", content="看", display=True
    )
    hidden = CustomMessageEntry(
        id="m2", custom_type="secret", content="藏", display=False
    )
    items = entries_to_items([shown, hidden])
    assert len(items) == 1
    assert isinstance(items[0], CustomItem)
    assert items[0].type == "notice"
    assert items[0].id == "m1"


def test_management_entries_produce_no_items():
    items = entries_to_items(
        [
            ModelChangeEntry(id="x1", provider="p", model_id="m"),
            LabelEntry(id="x2", target_id="e1", label="l"),
        ]
    )
    assert items == []


def test_custom_entry_maps_custom_item():
    """custom 条目（命令回执/扩展卡片）：可见但模型不读的呈现面——产 item。"""
    items = entries_to_items(
        [
            CustomEntry(
                id="x3",
                custom_type="command_result",
                data={"text": "已信任", "level": "info"},
            )
        ]
    )
    assert len(items) == 1
    assert items[0].type == "command_result"
    assert items[0].details == {"text": "已信任", "level": "info"}


# ---------------------------------------------------------------------------
# 同形性金标
# ---------------------------------------------------------------------------


def _normalize(items: List[Any]) -> List[dict]:
    """归一化：id 按序重写；ts/durationMs/reason 剔除（两路径不同源的元数据）。"""
    out = []
    for idx, item in enumerate(items):
        d = item.dump_wire() if isinstance(item, NovaItem) else item
        d = dict(d)
        d["id"] = f"#{idx}"
        d.pop("ts", None)
        d.pop("durationMs", None)
        d.pop("reason", None)  # compaction reason：实时取事件、恢复读条目不同源
        result = d.get("result")
        if isinstance(result, dict):
            result.pop("terminate", None)  # 循环控制提示，非呈现
        out.append(d)
    return out


def test_realtime_and_recovery_paths_are_isomorphic():
    """金标：实时事件流的终态 item 清单 == 恢复读的 item 清单。"""
    ts_user, ts_asst = 100, 200
    # ---- 恢复路径：直接构造分支条目 ----
    user_msg = UserMessage(content="你好", timestamp=ts_user)
    asst_msg = AssistantMessage(
        content=[
            TextContent(text="答"),
            ThinkingContent(thinking="想"),
            ToolCall(id="tc1", name="read", arguments={"p": "x"}),
        ],
        stop_reason=StopReason.TOOL_USE,
        timestamp=ts_asst,
    )
    tool_result = ToolResultMessage(
        tool_call_id="tc1",
        tool_name="read",
        content=[TextContent(text="内容")],
        details={"k": 1},
        timestamp=300,
    )
    pkg_msg = _PkgMessage(item_id="p1", value="输出", timestamp=400)
    entries = [
        SessionMessageEntry(id="e1", message=user_msg),
        SessionMessageEntry(id="e2", message=asst_msg),
        SessionMessageEntry(id="e3", message=tool_result),
        SessionMessageEntry(id="e4", message=pkg_msg),
        CustomMessageEntry(id="e5", custom_type="notice", content="便签", display=True),
        CompactionEntry(
            id="e6", summary="摘要", first_kept_entry_id="e2", tokens_before=9
        ),
    ]
    recovered = entries_to_items(entries)

    # ---- 实时路径：同一内容经事件流喂归约器 ----
    out: List[Any] = []
    reducer = SessionReducer(emit=lambda n: out.append(n))
    reducer.handle_event(MessageStartEvent(message=user_msg))
    # 助手流式：快照逐次增长（suffix 切分），end 定稿
    streaming = AssistantMessage(
        content=[], stop_reason=StopReason.TOOL_USE, timestamp=ts_asst
    )
    reducer.handle_event(MessageStartEvent(message=streaming))
    streaming.content = [TextContent(text="答")]
    reducer.handle_event(MessageUpdateEvent(message=streaming))
    streaming.content = [TextContent(text="答"), ThinkingContent(thinking="想")]
    reducer.handle_event(MessageUpdateEvent(message=streaming))
    streaming.content = [
        TextContent(text="答"),
        ThinkingContent(thinking="想"),
        ToolCall(id="tc1", name="read", arguments={"p": "x"}),
    ]
    reducer.handle_event(MessageEndEvent(message=streaming))
    reducer.handle_event(
        ToolExecutionStartEvent(tool_call_id="tc1", tool_name="read", args={"p": "x"})
    )
    reducer.handle_event(
        ToolExecutionEndEvent(
            tool_call_id="tc1",
            tool_name="read",
            result=AgentToolResult(
                content=[TextContent(text="内容")], details={"k": 1}
            ),
        )
    )
    # 工具结果消息事件（归约器忽略——不产独立 item）
    reducer.handle_event(MessageStartEvent(message=tool_result))
    reducer.handle_event(MessageEndEvent(message=tool_result))
    # 包级：流式 started + delta + record 定稿
    reducer.handle_event(
        ItemEmissionEvent(
            phase="started",
            item=_PkgItem(
                id="p1", status=ItemStatus.RUNNING, source="user", ts=400, value="输"
            ),
        )
    )
    reducer.handle_event(
        ItemEmissionEvent(phase="delta", item_id="p1", delta={"value": "出"})
    )
    reducer.handle_event(MessageStartEvent(message=pkg_msg))
    # 扩展注入消息（一次性）
    reducer.handle_event(
        MessageStartEvent(
            message=CustomMessage(
                custom_type="notice", content="便签", display=True, timestamp=500
            )
        )
    )
    # 压缩（一次性）
    reducer.handle_event(
        CompactionEndEvent(
            reason="manual",
            result=CompactionResult(
                summary="摘要", first_kept_entry_id="e2", tokens_before=9
            ),
        )
    )
    realtime = [n.item for n in out if n.type == "item_completed"]

    assert _normalize(realtime) == _normalize(recovered)
    # 包级 item 两路径同 id（item_id 随消息落盘）——不归一化也相等
    assert next(i for i in realtime if i.type == "pkgThing").id == "p1"
    assert next(i for i in recovered if i.type == "pkgThing").id == "p1"


def test_aborted_run_isomorphic_cancelled():
    """中断语义金标：abort 的 run——实时 sweep 与恢复配对缺失同为 cancelled。"""
    asst = AssistantMessage(
        content=[
            TextContent(text="半截"),
            ToolCall(id="tc2", name="bash", arguments={}),
        ],
        stop_reason=StopReason.ABORTED,
        timestamp=1,
    )
    recovered = entries_to_items([SessionMessageEntry(id="e8", message=asst)])

    out: List[Any] = []
    reducer = SessionReducer(emit=lambda n: out.append(n))
    reducer.handle_event(MessageStartEvent(message=asst))
    reducer.handle_event(MessageUpdateEvent(message=asst))
    reducer.handle_event(
        ToolExecutionStartEvent(tool_call_id="tc2", tool_name="bash", args={})
    )
    reducer.handle_event(AgentEndEvent(run_id="r1"))  # abort：无 message_end/tool_end
    realtime = [n.item for n in out if n.type == "item_completed"]

    assert _normalize(realtime) == _normalize(recovered)
    assert all(i["status"] == "cancelled" for i in _normalize(realtime))
