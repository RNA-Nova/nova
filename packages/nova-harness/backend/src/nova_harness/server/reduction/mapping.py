"""归约纯映射（无状态、表格驱动易测）。

消息/条目 → item 的翻译函数集中在本模块；状态编排（在飞 item 状态机）
归 ``orchestrator.py``。恢复读（条目 → item 清单）与实时流共用本模块的
构造函数——同形性（实时产出的 item 清单 == 恢复读产出）由两条路径
共享同一份纯映射保证。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from nova_ai import ImageContent, TextContent, ThinkingContent

from nova_harness.core.types.messages import CustomMessageContent
from nova_harness.server.types.items import (
    AgentMessageItem,
    CustomItem,
    ItemStatus,
    NovaItem,
    ThinkingItem,
    UserMessageItem,
)

# 追加语义的流式文本字段（线上键名）——只有这两名字段做字符串追加，
# 其余一律替换（status 等 str-Enum 字段若按"字符串即追加"会拼出
# "pendingrunning" 这种怪物——追加必须按字段白名单，不按值类型猜）
_DELTA_APPEND_KEYS = frozenset({"text", "output"})


def parse_entry_ts(timestamp: Any) -> int:
    """条目 timestamp（ISO 字符串）→ epoch ms；非法回退 0（对齐既有解析语义）。"""
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(str(timestamp)).timestamp() * 1000)
    except ValueError:
        return 0


def apply_delta(item: NovaItem, delta: Dict[str, Any]) -> None:
    """把一份 delta 合并进 item。

    合并规则（``ItemDeltaNotification`` 线上语义，客户端 store 同款实现）：
    - ``text``/``output`` 字段（流式文本约定名）：字符串追加；
    - 其余字段：替换。

    ``delta`` 键按线上名（camelCase alias）解析；未声明的键落在
    ``CustomItem`` 的 extra 区（包级变体透传）。
    """
    fields = type(item).model_fields
    for key, value in delta.items():
        field_name = key
        for name, field in fields.items():
            if (field.alias or name) == key:
                field_name = name
                break
        current = getattr(item, field_name, None)
        if (
            key in _DELTA_APPEND_KEYS
            and isinstance(current, str)
            and isinstance(value, str)
        ):
            merged: Any = current + value
        else:
            merged = value
        setattr(item, field_name, merged)


def user_message_to_item(message: Any, item_id: str) -> UserMessageItem:
    """用户消息 → UserMessageItem（content 归一为内容块列表）。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        blocks: List[CustomMessageContent] = (
            [TextContent(text=content)] if content else []
        )
    else:
        blocks = [
            block for block in content if isinstance(block, (TextContent, ImageContent))
        ]
    return UserMessageItem(
        id=item_id,
        source="user",
        ts=getattr(message, "timestamp", 0) or 0,
        content=blocks,
    )


def custom_message_to_item(message: Any, item_id: str) -> Optional[CustomItem]:
    """扩展注入消息（CustomMessage）→ CustomItem；不展示的返回 None。

    item ``type`` 取消息的 ``custom_type``（前端 ``entry:<type>`` 槽同名
    消费）；content/details 收进 details 负载。
    """
    if not getattr(message, "display", True):
        return None
    content = getattr(message, "content", "")
    if isinstance(content, list):
        content_payload: Any = [
            block.dump_wire() if hasattr(block, "dump_wire") else block
            for block in content
        ]
    else:
        content_payload = content
    return CustomItem(
        id=item_id,
        type=getattr(message, "custom_type", "") or "custom",
        source="agent",
        ts=getattr(message, "timestamp", 0) or 0,
        details={
            "content": content_payload,
            "details": getattr(message, "details", None),
        },
    )


def opaque_message_to_item(message: Any, item_id: str) -> CustomItem:
    """无 ``to_item`` 支持的包级消息 → CustomItem 兜底（全字段透传）。

    第三方用户工具不实现 item 协议时的优雅降级：渲染退化为通用
    ``entry:<role>`` 槽消费 details 负载。包缺席降级形态
    （OpaqueUserToolMessage）经 ``original_role`` 找回原始 type——
    包没装，旧卡片仍按原槽位渲染（数据不丢、呈现不降级）。
    """
    dump = message.dump_wire() if hasattr(message, "dump_wire") else {}
    item_type = (
        getattr(message, "original_role", None)
        or getattr(message, "role", "")
        or "custom"
    )
    return CustomItem(
        id=item_id,
        type=item_type,
        source="agent",
        ts=getattr(message, "timestamp", 0) or 0,
        details=dump,
    )


def message_to_final_item(message: Any, fallback_id: str) -> Optional[NovaItem]:
    """消息 → item 终态构造（实时定稿与恢复读共用，保证两路径同形）。

    - ``CustomMessage``（扩展注入消息）→ CustomItem；``display=False`` 不进
      转录返回 None；
    - 压缩/分支摘要消息是恢复路径的上下文构造（不落盘不成条目），实时/恢复
      都不经本函数产 item（压缩走 compaction_end/CompactionEntry，分支摘要
      走 BranchSummaryEntry）——防御性返回 None；
    - 包级消息优先 ``to_item()`` 协议；不支持的降级 CustomItem 兜底；
    - item id 缺省回退 ``fallback_id``（实时=归约器补铸，恢复=条目 id）。
    """
    from nova_harness.core.types.messages import (
        BranchSummaryMessage,
        CompactionSummaryMessage,
        CustomMessage,
    )

    if isinstance(message, (CompactionSummaryMessage, BranchSummaryMessage)):
        return None
    if isinstance(message, CustomMessage):
        return custom_message_to_item(message, fallback_id)
    to_item = getattr(message, "to_item", None)
    if callable(to_item):
        item = to_item()
        if not isinstance(item, NovaItem):
            return opaque_message_to_item(message, fallback_id)
        if not item.id:
            item.id = fallback_id
        return item
    return opaque_message_to_item(message, fallback_id)


def assistant_blocks(
    message: Any,
) -> List[Tuple[str, Union[TextContent, ThinkingContent]]]:
    """助手消息的可呈现内容块序列：``[(block_key, block)]``。

    block_key 稳定于块序号与种类（``t0``/``th1``）——实时流式 diff 与
    恢复读分解用同一套键，同一块在两路径下归属同一 item。
    ToolCall 块不产出 item（工具执行事件单独覆盖）。
    """
    blocks: List[Tuple[str, Union[TextContent, ThinkingContent]]] = []
    for index, block in enumerate(getattr(message, "content", []) or []):
        if isinstance(block, TextContent):
            blocks.append((f"t{index}", block))
        elif isinstance(block, ThinkingContent):
            blocks.append((f"th{index}", block))
    return blocks


def block_text(block: Union[TextContent, ThinkingContent]) -> str:
    """取内容块的文本载荷（text/thinking 字段归一）。"""
    if isinstance(block, ThinkingContent):
        return block.thinking
    return block.text


def make_stream_item(
    block: Union[TextContent, ThinkingContent], item_id: str, ts: int
) -> NovaItem:
    """为流式内容块建 item（started 初始态：空文本 + running）。"""
    if isinstance(block, ThinkingContent):
        return ThinkingItem(
            id=item_id, status=ItemStatus.RUNNING, source="agent", ts=ts, text=""
        )
    return AgentMessageItem(
        id=item_id, status=ItemStatus.RUNNING, source="agent", ts=ts, text=""
    )


def stream_item_status(stop_reason: Any) -> ItemStatus:
    """assistant 消息 stop_reason → 流式 item 终态（中断语义：aborted→cancelled）。"""
    if stop_reason == "aborted":
        return ItemStatus.CANCELLED
    if stop_reason == "error":
        return ItemStatus.FAILED
    return ItemStatus.DONE


__all__ = [
    "apply_delta",
    "user_message_to_item",
    "custom_message_to_item",
    "opaque_message_to_item",
    "message_to_final_item",
    "assistant_blocks",
    "block_text",
    "make_stream_item",
    "stream_item_status",
    "parse_entry_ts",
]
