"""item 三态通知（线上帧 payload 类型）。

归约层（``server/reduction/``）把会话事件流归约为 item 三态通知：
``item_started``（创建即真身）→ ``item_delta``（瞬态增量，不落盘）→
``item_completed``（终态定稿）。包级 item 经 ``AgentSession.emit_item_*``
发射（内部总线上的 ``item_emission`` 不透明信封），由 reducer 校验记账后
转为本类型广播——**本模块的类是纯线上词汇，从不上内部总线**。

信封锚点 seq/ts/sessionId 由服务器广播时打戳（与全部域通知同款）。
"""

from __future__ import annotations

from typing import Any, Dict, Literal

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field, SerializeAsAny

from nova_harness.server.types.items import NovaItem


class ItemStartedNotification(NovaBaseModel):
    """item 创建（携带初始状态；一次性内容与本事件 + completed 连发）。"""

    type: Literal["item_started"] = "item_started"
    # SerializeAsAny：包级 item 子类按自身 schema 落线（pydantic 默认按
    # 注解类型序列化会丢子类字段——与 SessionMessageEntry.message 同款）
    item: SerializeAsAny[NovaItem]


class ItemDeltaNotification(NovaBaseModel):
    """item 瞬态增量（不落盘）。

    ``delta`` 键为线上字段名（camelCase）；合并规则前后端同一语义：
    **``text``/``output`` 流式文本字段追加、其余替换**
    （见 ``server/reduction/mapping.apply_delta``——按字段白名单不按值类型，
    str-Enum 状态字段不会被误拼）。
    """

    type: Literal["item_delta"] = "item_delta"
    id: str = ""
    delta: Dict[str, Any] = Field(default_factory=dict)


class ItemCompletedNotification(NovaBaseModel):
    """item 终态定稿（落盘点；携带完整 item，客户端整件替换）。"""

    type: Literal["item_completed"] = "item_completed"
    item: SerializeAsAny[NovaItem]


__all__ = [
    "ItemStartedNotification",
    "ItemDeltaNotification",
    "ItemCompletedNotification",
]
