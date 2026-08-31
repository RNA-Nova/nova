"""状态编排器：在飞 item 状态机（server 侧归约）。

``SessionReducer`` 由 server 按会话挂摘（订阅会话事件总线，与广播并列的
一个 listener），把**内容事件**归约为 item 三态通知，产出经
``broadcast_event`` 扇出（类型化通知帧，不上内部总线）：

- 用户消息：message_start → UserMessageItem 一次性（started+completed 连发）；
- 助手流式：message_start/update/end → AgentMessageItem/ThinkingItem 的
  started/delta/completed（快照 → 后缀增量——记上次长度取后缀）；
- LLM 工具：tool_execution_start/update/end → ToolCallItem 三态
  （id = tool_call_id，实时/恢复两路径同身份）；
- 包级 item：``item_emission`` 信封承接（**类型边界**——isinstance 校验
  NovaItem，垃圾丢弃 + 日志）；record 时的 message_start 经消息的
  ``to_item()`` 定稿（completed）；无 ``to_item`` 的包级消息降级
  CustomItem 兜底（全字段透传）；
- 压缩：compaction_end（成功）→ CompactionItem 一次性；
- 中断语义：agent_end 时全部在飞 item 按 ``cancelled`` 定稿；
- session_replaced：清空状态（内容整体替换由客户端 resync 覆盖）。

设计文档：``examples/server-item-layer-design.md`` §5。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional

from nova_ai import AssistantMessage, ToolCall

from nova_harness.core.types.events import (
    AGENT_END,
    COMPACTION_END,
    ITEM_EMISSION,
    MESSAGE_END,
    MESSAGE_START,
    MESSAGE_UPDATE,
    SESSION_REPLACED,
    TOOL_EXECUTION_END,
    TOOL_EXECUTION_START,
    TOOL_EXECUTION_UPDATE,
)
from nova_harness.core.utils.messages import extract_text_from_content
from nova_harness.server.types.items import (
    AgentMessageItem,
    CompactionItem,
    CustomItem,
    ItemStatus,
    NovaItem,
    ToolCallItem,
)
from nova_harness.server.types.notifications import (
    ItemCompletedNotification,
    ItemDeltaNotification,
    ItemStartedNotification,
)

from .mapping import (
    apply_delta,
    assistant_blocks,
    block_text,
    make_stream_item,
    message_to_final_item,
    parse_entry_ts,
    stream_item_status,
    user_message_to_item,
)

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


class _AssistantStream:
    """一条流式助手消息的归约状态（block_key → (item_id, 已发射文本长度)；
    pending_tools = 本条消息流式期建立的 PENDING 工具卡 id（message_end 时
    标记 args_complete——执行前预览的触发时点））。"""

    __slots__ = ("blocks", "pending_tools")

    def __init__(self) -> None:
        self.blocks: Dict[str, tuple[str, int]] = {}
        self.pending_tools: List[str] = []


class SessionReducer:
    """把会话事件流归约为 item 通知（每会话一个实例）。

    ``emit`` 为产出出口（通常是 ``session._emit``）；实例状态全部在
    内存——落盘仍是消息形，item 层是纯线上产物。
    """

    def __init__(self, emit: Callable[[Any], None]) -> None:
        self._emit = emit
        # 在飞 item（started 未 completed）——syncSession 快照的在飞段来源
        self._in_flight: Dict[str, NovaItem] = {}
        # id(message) → 流式归约状态
        self._streams: Dict[int, _AssistantStream] = {}

    # ------------------------------------------------------------------
    # 公共查询
    # ------------------------------------------------------------------

    def in_flight_items(self) -> list[NovaItem]:
        """当前在飞 item（syncSession 快照拼接用——重连客户端据此对在飞
        流式/执行补齐 started 状态，后续 delta 才能对上号）。"""
        return list(self._in_flight.values())

    # ------------------------------------------------------------------
    # 事件入口
    # ------------------------------------------------------------------

    def handle_event(self, event: Any) -> None:
        """会话事件总线入口（listener 形态；归约异常不污染主流程）。"""
        try:
            self._dispatch(event)
        except Exception:
            logger.warning("SessionReducer 归约事件失败", exc_info=True)

    def _dispatch(self, event: Any) -> None:
        event_type = getattr(event, "type", None)
        if event_type == MESSAGE_START:
            self._on_message_start(getattr(event, "message", None))
        elif event_type == MESSAGE_UPDATE:
            self._on_message_update(getattr(event, "message", None))
        elif event_type == MESSAGE_END:
            self._on_message_end(getattr(event, "message", None))
        elif event_type == TOOL_EXECUTION_START:
            self._on_tool_start(event)
        elif event_type == TOOL_EXECUTION_UPDATE:
            self._on_tool_update(event)
        elif event_type == TOOL_EXECUTION_END:
            self._on_tool_end(event)
        elif event_type == COMPACTION_END:
            self._on_compaction_end(event)
        elif event_type == AGENT_END:
            self._on_agent_end()
        elif event_type == SESSION_REPLACED:
            self._reset()
        elif event_type == "entry_appended":  # 无同名常量（事件类内联字面量）
            self._on_entry_appended(event)
        elif event_type == ITEM_EMISSION:
            self._on_item_emission(event)

    def _reset(self) -> None:
        self._in_flight.clear()
        self._streams.clear()

    # ------------------------------------------------------------------
    # 发射
    # ------------------------------------------------------------------

    def _start(self, item: NovaItem) -> None:
        self._in_flight[item.id] = item
        self._emit(ItemStartedNotification(item=item))

    def _delta(self, item: NovaItem, delta: Dict[str, Any]) -> None:
        apply_delta(item, delta)
        self._emit(ItemDeltaNotification(id=item.id, delta=delta))

    def _complete(self, item: NovaItem) -> None:
        self._in_flight.pop(item.id, None)
        self._emit(ItemCompletedNotification(item=item))

    def _one_shot(self, item: NovaItem) -> None:
        """一次性内容：started + completed 连发（无流式无 pending）。"""
        self._emit(ItemStartedNotification(item=item))
        self._emit(ItemCompletedNotification(item=item))

    @staticmethod
    def _mint(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # 消息事件
    # ------------------------------------------------------------------

    def _on_message_start(self, message: Any) -> None:
        if message is None:
            return
        role = getattr(message, "role", None)
        if role == "user":
            self._one_shot(user_message_to_item(message, self._mint("user")))
        elif role == "assistant":
            self._streams[id(message)] = _AssistantStream()
        elif role in ("toolResult", None):
            # toolResult 归 ToolCallItem 承载，不单独成 item
            return
        else:
            self._on_custom_message(message)

    def _on_custom_message(self, message: Any) -> None:
        item = message_to_final_item(message, self._mint("pkg"))
        if item is None:
            return
        if item.id in self._in_flight:
            # 包已提前 started/delta 流式——本消息是权威定稿
            self._complete(item)
        else:
            self._one_shot(item)

    def _on_message_update(self, message: Any) -> None:
        stream = self._streams.get(id(message))
        if stream is None:
            return
        for block_key, block in assistant_blocks(message):
            text = block_text(block)
            state = stream.blocks.get(block_key)
            if state is None:
                # ts 取消息时间戳（与恢复读同源——同形性）而非铸造时刻
                item = make_stream_item(
                    block,
                    self._mint("stream"),
                    getattr(message, "timestamp", 0) or _now_ms(),
                )
                stream.blocks[block_key] = (item.id, 0)
                self._start(item)
                state = (item.id, 0)
            item_id, emitted_len = state
            if len(text) > emitted_len:
                suffix = text[emitted_len:]
                stream.blocks[block_key] = (item_id, len(text))
                self._delta(self._in_flight[item_id], {"text": suffix})
        # toolCall 块：参数流式期即建 PENDING 卡片（pi 两阶段卡片对位——
        # 参数逐段累积可见；tool_execution_start 转 RUNNING，定稿归
        # tool_execution_end；message_end 不收它——执行窗口在后）
        for block in getattr(message, "content", []) or []:
            if not isinstance(block, ToolCall) or not block.id or not block.name:
                continue
            existing = self._in_flight.get(block.id)
            if existing is None:
                stream.pending_tools.append(block.id)
                self._start(
                    ToolCallItem(
                        id=block.id,
                        status=ItemStatus.PENDING,
                        source="agent",
                        ts=getattr(message, "timestamp", 0) or _now_ms(),
                        tool=block.name,
                        args=block.arguments,
                    )
                )
            elif (
                isinstance(existing, ToolCallItem)
                and existing.status is ItemStatus.PENDING
            ):
                if existing.args != block.arguments:
                    self._delta(existing, {"args": block.arguments})

    def _on_message_end(self, message: Any) -> None:
        stream = self._streams.pop(id(message), None)
        if stream is None:
            return
        status = stream_item_status(getattr(message, "stop_reason", None))
        # 失败定稿时把 error_message 附上文本 item（用户可见的错误行数据源）
        error_text = (
            getattr(message, "error_message", None)
            if status is ItemStatus.FAILED
            else None
        )
        for block_key, (item_id, _) in stream.blocks.items():
            item = self._in_flight.get(item_id)
            if item is not None:
                item.status = status
                if error_text and isinstance(item, AgentMessageItem):
                    item.error = error_text
                self._complete(item)
        # 参数流式窗口关闭：本条消息的 PENDING 工具卡标记 args_complete
        # （"参数完整、执行未开始"——edit 类执行前只读预览的触发时点）
        for tool_id in stream.pending_tools:
            tool_item = self._in_flight.get(tool_id)
            if (
                isinstance(tool_item, ToolCallItem)
                and tool_item.status is ItemStatus.PENDING
            ):
                self._delta(tool_item, {"argsComplete": True})

    # ------------------------------------------------------------------
    # 工具执行事件
    # ------------------------------------------------------------------

    def _on_tool_start(self, event: Any) -> None:
        tool_call_id = getattr(event, "tool_call_id", "")
        existing = self._in_flight.get(tool_call_id)
        if isinstance(existing, ToolCallItem) and existing.status is ItemStatus.PENDING:
            # 参数流式卡片转执行态：status + 权威 args（校验后定稿）
            self._delta(
                existing,
                {"status": ItemStatus.RUNNING, "args": getattr(event, "args", None)},
            )
            return
        item = ToolCallItem(
            id=tool_call_id or self._mint("tool"),
            status=ItemStatus.RUNNING,
            source="agent",
            ts=_now_ms(),
            tool=getattr(event, "tool_name", ""),
            args=getattr(event, "args", None),
        )
        self._start(item)

    def _on_tool_update(self, event: Any) -> None:
        item = self._in_flight.get(getattr(event, "tool_call_id", ""))
        if not isinstance(item, ToolCallItem):
            return
        self._delta(item, {"partialResult": getattr(event, "partial_result", None)})

    def _on_tool_end(self, event: Any) -> None:
        tool_call_id = getattr(event, "tool_call_id", "")
        item = self._in_flight.get(tool_call_id)
        if item is None:
            # 未见 start（防御——start/end 在循环内配对，正常不可达）：
            # 补建再定稿，保证客户端始终看到完整生命周期
            self._on_tool_start(event)
            item = self._in_flight[tool_call_id]
        if not isinstance(item, ToolCallItem):
            return
        is_error = bool(getattr(event, "is_error", False))
        result = getattr(event, "result", None)
        item.status = ItemStatus.FAILED if is_error else ItemStatus.DONE
        item.result = result
        item.partial_result = None
        item.duration_ms = max(0, _now_ms() - item.ts)
        if is_error:
            item.error = extract_text_from_content(getattr(result, "content", []) or [])
        self._complete(item)

    # ------------------------------------------------------------------
    # 压缩 / run 终结
    # ------------------------------------------------------------------

    def _on_compaction_end(self, event: Any) -> None:
        result = getattr(event, "result", None)
        if result is None or getattr(event, "aborted", False):
            return
        self._one_shot(
            CompactionItem(
                id=self._mint("compaction"),
                status=ItemStatus.DONE,
                source="agent",
                ts=_now_ms(),
                summary=getattr(result, "summary", "") or "",
                tokens_before=getattr(result, "tokens_before", 0) or 0,
                reason=getattr(event, "reason", None),
            )
        )

    def _on_entry_appended(self, event: Any) -> None:
        """custom 条目（扩展 append_entry）→ CustomItem 一次性。

        custom 条目的真实语义：**可见但模型不读的呈现面**（命令回执、扩展
        自定义卡片）——它不是消息，结构性地不进 LLM 上下文；呈现经此
        通道上线。非 custom 类型（label 等管理条目）不产 item。
        """
        entry = getattr(event, "entry", None)
        if getattr(entry, "type", None) != "custom":
            return
        self._one_shot(
            CustomItem(
                id=entry.id or self._mint("entry"),
                type=getattr(entry, "custom_type", "") or "custom",
                source="agent",
                ts=parse_entry_ts(getattr(entry, "timestamp", "")),
                details=getattr(entry, "data", None),
            )
        )

    def _on_item_emission(self, event: Any) -> None:
        """包级 item 发射承接：类型校验 + 在飞台账 + 转类型化帧。

        core 对载荷不透明——本方法是类型边界：非 NovaItem 子类或缺 id 的
        载荷在上线前丢弃（日志），垃圾永不进入线上契约。completed 无相位：
        定稿走 record 消息路径（``_on_custom_message`` 的 to_item 分支）。
        """
        phase = getattr(event, "phase", "")
        if phase == "started":
            item = getattr(event, "item", None)
            if not isinstance(item, NovaItem) or not item.id:
                logger.warning("丢弃非法 item 发射（started）：%r", type(item))
                return
            self._start(item)
        elif phase == "delta":
            item = self._in_flight.get(getattr(event, "item_id", ""))
            if item is None:
                return
            self._delta(item, getattr(event, "delta", None) or {})

    def _on_agent_end(self) -> None:
        """run 终结清扫：全部在飞 item 按 cancelled 定稿（中断语义）。

        正常路径 item 已由各自 end 事件定稿；这里兜底 abort/异常截断的
        残留（流式块未 end、工具执行未 end）。与具体 end 事件幂等并存
        （已完成的 item 不在在飞台账）。
        """
        for item in list(self._in_flight.values()):
            item.status = ItemStatus.CANCELLED
            self._complete(item)
        self._streams.clear()


__all__ = ["SessionReducer"]
