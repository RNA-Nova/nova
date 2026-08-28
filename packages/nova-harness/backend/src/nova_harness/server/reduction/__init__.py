"""归约层：会话事件流 → item 通知（server 侧归约）。

- ``mapping``：无状态纯映射（消息/条目 → item 构造、delta 合并规则）；
- ``orchestrator``：``SessionReducer`` 在飞 item 状态机（实时路径）。

恢复读（条目 → item 清单）在同包 ``entries`` 模块（与实时共用 mapping，
同形性由共享纯映射保证）。设计见 ``examples/server-item-layer-design.md``。
"""

from nova_harness.server.reduction.entries import entries_to_items
from nova_harness.server.reduction.mapping import apply_delta
from nova_harness.server.reduction.orchestrator import SessionReducer

__all__ = ["apply_delta", "SessionReducer", "entries_to_items"]
