"""Nova harness 共享词汇层。

跨域契约类型的集中居所（对位 codex 的 ``codex_protocol`` crate）：

- `events/`：事件类型与事件名字符串常量（顶层 ``nova_harness.events``）
- `messages.py`：自定义消息类型与摘要前后缀常量
- `protocols.py`：核心服务依赖的 Protocol 契约（AgentSession / 资源加载器 /
  包解析器…）

领域私有类型就近放在各域（``core/domains/<域>/types``、``sessions/types``、
``server/ui``、``model/types`` …），不在此聚合。

导入纪律（沿用原约定）：走完整子模块路径
（``from nova_harness.types.messages import X``），不在包级 ``__init__``
做 re-export——避免 ``__init__`` 链形成循环 import。
"""
