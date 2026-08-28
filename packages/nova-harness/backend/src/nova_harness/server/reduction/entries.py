"""恢复读：当前分支条目 → item 清单（纯映射，与实时路径同形的纪律）。

输入是**当前分支路径**的条目序列（``SessionManager.get_branch()``），
输出按分支顺序的 item 清单。同形纪律：

- 消息派生 item 的 id 由条目 id 派生（``entry.id`` / ``entry.id:block_key``）；
- ToolCall item 身份 = tool_call_id（与实时路径一致——两路径同身份）；
- 包级消息经 ``to_item()``（id 缺省回退条目 id——bash 的 item_id 随消息
  落盘，恢复出的 item 与实时同 id）；
- **中断语义在此定义**：toolCall 块无配对 ToolResultMessage → cancelled
  （run abort 后中断的调用在恢复时呈现为取消，而非永远 running）；
- 管理/元数据条目（label/model_change/thinking_level_change/session_info/
  custom）不产 item——它们归域通知/树图接口（§4.4）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from nova_agent import AgentToolResult
from nova_ai import ToolCall

from nova_harness.core.types.session.entries import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    SessionEntry,
    SessionMessageEntry,
)
from nova_harness.core.utils.messages import (
    create_custom_message,
    extract_text_from_content,
)
from nova_harness.server.types.items import (
    BranchSummaryItem,
    CompactionItem,
    CustomItem,
    ItemStatus,
    NovaItem,
    ToolCallItem,
)

from .mapping import (
    assistant_blocks,
    block_text,
    make_stream_item,
    message_to_final_item,
    stream_item_status,
    user_message_to_item,
)


def entries_to_items(entries: List[SessionEntry]) -> List[NovaItem]:
    """把当前分支条目序列翻译为 item 清单（syncSession 的转录段）。"""
    # 配对表先行：toolResult 条目按 tool_call_id 收编（顺序上结果在调用之后，
    # 但树导航回返等场景不做顺序假设——先全量收集再按序产出）
    tool_results: Dict[str, Any] = {}
    for entry in entries:
        if isinstance(entry, SessionMessageEntry):
            message = entry.message
            if getattr(message, "role", None) == "toolResult":
                tool_results[message.tool_call_id] = message

    items: List[NovaItem] = []
    for entry in entries:
        if isinstance(entry, SessionMessageEntry):
            items.extend(_message_entry_to_items(entry, tool_results))
        elif isinstance(entry, CompactionEntry):
            items.append(
                CompactionItem(
                    id=entry.id,
                    status=ItemStatus.DONE,
                    source="agent",
                    ts=_entry_ts(entry),
                    summary=entry.summary,
                    tokens_before=entry.tokens_before,
                )
            )
        elif isinstance(entry, BranchSummaryEntry):
            items.append(
                BranchSummaryItem(
                    id=entry.id,
                    status=ItemStatus.DONE,
                    source="agent",
                    ts=_entry_ts(entry),
                    summary=entry.summary,
                    from_id=entry.from_id,
                )
            )
        elif isinstance(entry, CustomMessageEntry):
            # 扩展注入消息（进上下文的那种）：先复原 CustomMessage 再走共享
            # 构造函数——与实时路径同一映射（同形）
            message = create_custom_message(
                entry.custom_type,
                entry.content,
                entry.display,
                entry.details,
                entry.timestamp,
            )
            item = message_to_final_item(message, entry.id)
            if item is not None:
                items.append(item)
        elif isinstance(entry, CustomEntry):
            # custom 条目（扩展 append_entry）：可见但模型不读的呈现面——
            # 与实时路径（entry_appended → CustomItem）同形
            items.append(
                CustomItem(
                    id=entry.id,
                    type=entry.custom_type or "custom",
                    source="agent",
                    ts=_entry_ts(entry),
                    details=entry.data,
                )
            )
        # 其余条目类型（label/model_change/thinking_level_change/session_info）：
        # 管理/元数据层，不产 item
    return items


def _message_entry_to_items(
    entry: SessionMessageEntry, tool_results: Dict[str, Any]
) -> List[NovaItem]:
    message = entry.message
    role = getattr(message, "role", None)
    if role == "user":
        return [user_message_to_item(message, entry.id)]
    if role == "assistant":
        return _assistant_entry_to_items(entry, tool_results)
    if role == "toolResult":
        return []  # 已被 ToolCall 配对吸收
    # 包级消息（bashExecution 等注册类 / opaqueUserTool 降级形态）
    item = message_to_final_item(message, entry.id)
    return [item] if item is not None else []


def _assistant_entry_to_items(
    entry: SessionMessageEntry, tool_results: Dict[str, Any]
) -> List[NovaItem]:
    """助手消息分解：text/thinking 块各成 item + toolCall 块配对该工具结果。"""
    message = entry.message
    ts = getattr(message, "timestamp", 0) or 0
    status = stream_item_status(getattr(message, "stop_reason", None))
    items: List[NovaItem] = []
    for block_key, block in assistant_blocks(message):
        item = make_stream_item(block, f"{entry.id}:{block_key}", ts)
        item.text = block_text(block)
        item.status = status
        items.append(item)
    for block in getattr(message, "content", []) or []:
        if isinstance(block, ToolCall):
            items.append(_tool_call_to_item(block, tool_results.get(block.id), ts))
    return items


def _tool_call_to_item(block: ToolCall, result_message: Any, ts: int) -> ToolCallItem:
    """ToolCall 块 + 配对结果 → ToolCall item（中断语义：无结果 = cancelled）。"""
    if result_message is None:
        return ToolCallItem(
            id=block.id,
            status=ItemStatus.CANCELLED,
            source="agent",
            ts=ts,
            tool=block.name,
            args=block.arguments,
        )
    is_error = bool(getattr(result_message, "is_error", False))
    result = AgentToolResult(
        content=getattr(result_message, "content", []) or [],
        details=getattr(result_message, "details", None),
        added_tool_names=getattr(result_message, "added_tool_names", None),
        is_error=is_error,
    )
    return ToolCallItem(
        id=block.id,
        status=ItemStatus.FAILED if is_error else ItemStatus.DONE,
        source="agent",
        ts=ts,
        tool=block.name,
        args=block.arguments,
        result=result,
        error=extract_text_from_content(result.content) or None if is_error else None,
    )


def _entry_ts(entry: Any) -> int:
    """条目 timestamp（ISO 字符串）→ epoch ms；非法回退 0（对齐既有解析语义）。"""
    try:
        return int(datetime.fromisoformat(str(entry.timestamp)).timestamp() * 1000)
    except ValueError:
        return 0


__all__ = ["entries_to_items"]
