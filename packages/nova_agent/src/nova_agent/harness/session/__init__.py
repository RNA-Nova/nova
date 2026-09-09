"""库级会话存储抽象（对齐 TS ``harness/session/``）。

分层：

- ``context.py``：从分支条目重建 LLM 上下文（压缩边界 / 状态派生 / 条目投影）
- ``types.py``：durable 形状（TypedDict）/ 查询 dataclass / SessionError / 存储契约
- ``state.py``：SessionState——纯 reducer（mutation → 校验 → 状态转移），fork =
  重放 mutation 列表
- ``session.py``：Session——树视图（包装存储，输入校验 + id 分配）
- ``memory.py``：内存 SessionStorage / SessionRepo 后端
- ``jsonl/``：JSONL 落盘后端（追加写 + 重放 + 撕裂尾修复 + 原子发布）
- ``testing/``：后端一致性 conformance 套件（任何 SessionStorage 实现都跑同一组用例）

pi 侧未移植项：崩溃续跑（findOpenOperations → intent 重放）属于 agent 层 operation 模型，
待 nova_agent 建立该机制后再落地。
"""

from .context import SessionContext, build_session_context
from .memory import InMemorySessionRepo, InMemorySessionStorage
from .session import Session, assert_json_serializable
from .types import (
    BranchBounds,
    BranchEntryQuery,
    Entry,
    EntryCursor,
    EntryQuery,
    ForkOptions,
    IdGenerator,
    LanePointer,
    LaneRecord,
    LogItem,
    LogOptions,
    RecordQuery,
    SessionCreateOptions,
    SessionError,
    SessionMetadata,
    SessionRepo,
    SessionStats,
    SessionStorage,
    SessionTree,
)

__all__ = [
    "BranchBounds",
    "BranchEntryQuery",
    "Entry",
    "EntryCursor",
    "EntryQuery",
    "ForkOptions",
    "IdGenerator",
    "InMemorySessionRepo",
    "InMemorySessionStorage",
    "LanePointer",
    "LaneRecord",
    "LogItem",
    "LogOptions",
    "RecordQuery",
    "Session",
    "SessionCreateOptions",
    "SessionError",
    "SessionMetadata",
    "SessionRepo",
    "SessionStats",
    "SessionContext",
    "SessionStorage",
    "SessionTree",
    "assert_json_serializable",
    "build_session_context",
]
