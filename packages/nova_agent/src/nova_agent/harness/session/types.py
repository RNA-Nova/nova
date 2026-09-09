"""Session 数据模型与存储契约（对齐 TS ``harness/session/types.ts``）。

表示约定（根 AGENTS.md 数据建模规则 10）：durable 形状（entry / record）用真
:class:`typing.TypedDict` 声明——有键声明、零运行时开销、不校验；运行时以
dict 流转，与 TS 的 plain object 纪律一致，重放恢复路径与活路径形状统一。
存储层分配的字段（``seq`` / ``parent_id`` / ``timestamp``）用 ``NotRequired``
标注（provisioned 载荷不含它们）；判别键保持 Literal 字面量稳定，为将来
codec 边界的 ``TypeAdapter`` 校验留位。临时选项（查询 / fork）是不可变值
对象——``dataclass(frozen=True, kw_only=True)``（规则 5）。所有键 snake_case，
即未来 JSONL 的落盘格式。

核心概念：

- **Entry**：写入会话树的内容单元（消息 / 模型切换 / 压缩标记 / 分支摘要 / 自定义），
  带 ``id`` + ``parent_id`` 构成树；``seq`` / ``timestamp`` / ``parent_id`` 由存储层分配。
- **LaneRecord**：操作留痕（operation_started / tool_started / usage 等 9 种），按
  lane 归组，用于恢复、审计与用量归因——不进 LLM 上下文。
- **Lane**：独立叶子指针的分支车道（``"main"`` 为主车道），支持多 agent 并行写各自
  车道。lane 名字永久，随其 recovery record 存续。
- **SessionStorage**：后端必须实现的结构化契约（Protocol）——调用方注入，库层零
  路径假设。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    List,
    Literal,
    NotRequired,
    Optional,
    Protocol,
    TypedDict,
    Union,
)

# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------

SessionErrorCode = Literal[
    "not_found",
    "already_exists",
    "invalid_entry",
    "invalid_payload",
    "invalid_lane",
    "invalid_query",
    "invalid_fork_target",
    "storage",
]


class SessionError(Exception):
    """会话操作的类型化错误（对齐 TS ``SessionError``）。"""

    def __init__(self, code: SessionErrorCode, message: str, cause: Optional[BaseException] = None):
        super().__init__(message)
        self.name = "SessionError"
        self.code: SessionErrorCode = code
        if cause is not None:
            self.__cause__ = cause


# ---------------------------------------------------------------------------
# Usage dict 形状（nova_ai ``Usage`` 模型 dump 后的落盘形状，规则 10 声明）
# ---------------------------------------------------------------------------


class CostData(TypedDict, total=False):
    """``Usage["cost"]`` 的形状。"""

    input: float
    output: float
    cache_read: float
    cache_write: float
    total: float


class UsageData(TypedDict, total=False):
    """usage 记录里 ``usage`` 字段的形状（token 计数 + 嵌套 cost）。"""

    input: int
    output: int
    cache_read: int
    cache_write: int
    cache_write_1h: int
    reasoning: int
    total_tokens: int
    cost: CostData


# ---------------------------------------------------------------------------
# Entry 类型（写入会话树的内容单元）
# ---------------------------------------------------------------------------

EntryType = Literal[
    "message",
    "model_change",
    "thinking_level_change",
    "active_tools_change",
    "compaction",
    "branch_summary",
    "custom",
]


class EntryBase(TypedDict):
    """所有 Entry 共享的存储分配字段。

    ``type`` / ``id`` 由调用方给定；``seq`` / ``parent_id`` / ``timestamp`` 由
    存储层分配（provisioned 载荷不含——``NotRequired``），``seq`` 为全会话共享
    单调序号，读取侧可见。
    """

    type: EntryType
    id: str
    seq: NotRequired[int]
    parent_id: NotRequired[Optional[str]]
    timestamp: NotRequired[int]


class MessageEntry(EntryBase):
    """``type == "message"``。``message`` 为 AgentMessage 的 dict 形式（pydantic
    模型在 ``Session.append_message`` 边界 dump，保证活路径 == 重放路径）。"""

    message: Any
    terminate: NotRequired[bool]


class ModelChangeEntry(EntryBase):
    """``type == "model_change"``。"""

    provider: str
    model_id: str


class ThinkingLevelEntry(EntryBase):
    """``type == "thinking_level_change"``。"""

    thinking_level: str


class ActiveToolsEntry(EntryBase):
    """``type == "active_tools_change"``。"""

    active_tool_names: List[str]


class CompactionEntry(EntryBase):
    """``type == "compaction"``——压缩结果标记。"""

    summary: str
    retained_tail: List[Any]
    tokens_before: int
    details: NotRequired[Any]
    usage: NotRequired[UsageData]


class BranchSummaryEntry(EntryBase):
    """``type == "branch_summary"``——分支导航摘要，同时是 branch walk 的停止边界。"""

    from_id: str
    summary: str
    details: NotRequired[Any]
    usage: NotRequired[UsageData]


class CustomEntry(EntryBase):
    """``type == "custom"``——扩展写入的开放载荷（``custom_type`` + ``data``）。"""

    custom_type: str
    data: NotRequired[Any]


Entry = Union[
    MessageEntry,
    ModelChangeEntry,
    ThinkingLevelEntry,
    ActiveToolsEntry,
    CompactionEntry,
    BranchSummaryEntry,
    CustomEntry,
]


class LanePointer(TypedDict):
    """lane 叶子指针。``leaf_id`` 为 ``None`` 表示空车道。"""

    lane: str
    leaf_id: Optional[str]


# ---------------------------------------------------------------------------
# LaneRecord 类型（操作留痕，不进 LLM 上下文）
# ---------------------------------------------------------------------------


class RecordBase(TypedDict):
    """所有 LaneRecord 共享字段：``id`` / ``lane`` 调用方给定，``seq`` /
    ``timestamp`` 存储层分配（``NotRequired``）。"""

    id: str
    lane: str
    seq: NotRequired[int]
    timestamp: NotRequired[int]


class RunIntent(TypedDict, total=False):
    """``intent["kind"] == "run"``。"""

    kind: Literal["run"]
    """归一化后的调用输入（before_run 前；挂起恢复与 before_resume 需要）。"""
    original_prompt: NotRequired[List[Any]]
    """捕获的 nextRun 条目 → prompt → before_run 注入。"""
    initial_messages: NotRequired[List[Any]]
    system_prompt_override: NotRequired[str]
    resume_data: NotRequired[Dict[str, Any]]


class CompactionIntent(TypedDict, total=False):
    """``intent["kind"] == "compaction"``。"""

    kind: Literal["compaction"]
    custom_instructions: NotRequired[str]
    result_entry_id: NotRequired[str]


class NavigationIntent(TypedDict, total=False):
    """``intent["kind"] == "navigation"``。"""

    kind: Literal["navigation"]
    target_id: NotRequired[Optional[str]]
    summarize: NotRequired[bool]
    custom_instructions: NotRequired[str]
    label: NotRequired[str]
    summary_entry_id: NotRequired[str]


OperationIntent = Union[RunIntent, CompactionIntent, NavigationIntent]


class OperationStartedRecord(RecordBase):
    """``type == "operation_started"``——run / compaction / navigation 的开始留痕，
    ``id`` 即 runId，恢复时经 ``findOpenOperations`` 读取。"""

    type: Literal["operation_started"]
    source_leaf_id: Optional[str]
    intent: OperationIntent


class AbortRequestedRecord(RecordBase):
    """``type == "abort_requested"``。"""

    type: Literal["abort_requested"]
    run_id: str


class OperationFinishedRecord(RecordBase):
    """``type == "operation_finished"``——``outcome``: completed / aborted / failed /
    declined。"""

    type: Literal["operation_finished"]
    run_id: str
    outcome: Literal["completed", "aborted", "failed", "declined"]
    error: NotRequired[Dict[str, str]]
    """``{"code": ..., "message": ...}``。"""


class StepAttemptRecord(RecordBase):
    """``type == "step_attempt"``——单步重试轨迹；compaction 步骤额外携带
    ``compaction_reason``（manual / threshold / overflow），恢复时据此续做同样的工作。"""

    type: Literal["step_attempt"]
    run_id: str
    step: Literal["assistant", "branch_summary", "compaction"]
    attempt: int
    result_entry_id: str
    compaction_reason: NotRequired[Literal["manual", "threshold", "overflow"]]


class ToolStartedRecord(RecordBase):
    """``type == "tool_started"``——工具调用留痕，``replay``: never / safe。"""

    type: Literal["tool_started"]
    run_id: str
    assistant_entry_id: str
    tool_index: int
    tool_call_id: str
    tool_name: str
    effective_args: Dict[str, Any]
    result_entry_id: str
    replay: Literal["never", "safe"]


class QueueEnqueuedRecord(RecordBase):
    """``type == "queue_enqueued"``——``queue``: steer / followUp（带 run_id）或
    nextRun（不带）。``target`` 为 provisioned entry，入队不消费。"""

    type: Literal["queue_enqueued"]
    queue: Literal["steer", "followUp", "nextRun"]
    run_id: NotRequired[str]
    target: Dict[str, Any]


class QueueCancelledRecord(RecordBase):
    """``type == "queue_cancelled"``。"""

    type: Literal["queue_cancelled"]
    run_id: NotRequired[str]
    entry_id: str


class WriteDeferredRecord(RecordBase):
    """``type == "write_deferred"``——延迟写入的暂存。"""

    type: Literal["write_deferred"]
    run_id: str
    target: Dict[str, Any]


class UsageRecord(RecordBase):
    """``type == "usage"``——用量台账。``cause`` 区分归因来源（assistant / tool /
    hook / compaction / branch_summary / deferred_fetch / adjustment），按 cause
    各自携带可选字段。"""

    type: Literal["usage"]
    usage: UsageData
    cause: Literal[
        "assistant",
        "compaction",
        "branch_summary",
        "deferred_fetch",
        "tool",
        "hook",
        "adjustment",
    ]
    run_id: NotRequired[str]
    entry_id: NotRequired[str]
    attempt: NotRequired[int]
    stop_reason: NotRequired[str]
    tool_call_id: NotRequired[str]
    details: NotRequired[Any]


LaneRecord = Union[
    OperationStartedRecord,
    AbortRequestedRecord,
    OperationFinishedRecord,
    StepAttemptRecord,
    ToolStartedRecord,
    QueueEnqueuedRecord,
    QueueCancelledRecord,
    WriteDeferredRecord,
    UsageRecord,
]


# ---------------------------------------------------------------------------
# 查询与游标（不可变值对象——规则 5：frozen + kw_only）
# ---------------------------------------------------------------------------

EntryOrder = Literal["newestFirst", "oldestFirst"]


@dataclass(frozen=True, kw_only=True)
class EntryCursor:
    """排他游标：``after_seq`` 之前（newestFirst）或之后（oldestFirst）的条目。"""

    after_seq: int


@dataclass(frozen=True, kw_only=True)
class EntryQuery:
    """全会话（跨分支、跨 lane）条目查询。默认 newestFirst。"""

    type: Optional[str] = None
    custom_type: Optional[str] = None
    """仅 ``type == "custom"`` 时生效。"""
    order: Optional[EntryOrder] = None
    limit: Optional[int] = None
    cursor: Optional[EntryCursor] = None


@dataclass(frozen=True, kw_only=True)
class BranchBounds:
    """分支扫描边界。默认：从视图 lane 的叶子走到根。"""

    start: Optional[str] = None
    stop_at_type: Optional[str] = None
    """首个匹配处停（含该条目）。"""
    stop_at_id: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class BranchEntryQuery(EntryQuery):
    """存储层分支查询入参（对齐 TS ``EntryQuery & BranchBounds & { start: string }``
    交叉类型）——``start`` 在此层必填（无默认，空串缺省的假语义已移除），
    lane 叶子缺省是视图层糖。"""

    start: str
    stop_at_type: Optional[str] = None
    stop_at_id: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class RecordQuery:
    """LaneRecord 查询。``operation_kind`` 仅对 ``type == "operation_started"`` 合法。"""

    lane: Optional[str] = None
    type: Optional[str] = None
    run_id: Optional[str] = None
    operation_kind: Optional[str] = None
    after_seq: Optional[int] = None
    order: Optional[EntryOrder] = None
    limit: Optional[int] = None


@dataclass(frozen=True, kw_only=True)
class LogOptions:
    """mutation 日志读取选项（增量订阅的游标即 ``after_seq``）。"""

    after_seq: Optional[int] = None
    limit: Optional[int] = None


# ---------------------------------------------------------------------------
# 会话元数据与统计
# ---------------------------------------------------------------------------


class SessionMetadata(TypedDict):
    """``id`` / ``created_at`` 必填；fork 时 ``parent_session_id`` 指向源会话。"""

    id: str
    created_at: int
    parent_session_id: NotRequired[Optional[str]]


@dataclass
class SessionStats:
    """台账统计（usage record 增量累计）——可变运行时容器（规则 1），不冻结。"""

    message_count: int = 0
    cached_tokens: int = 0
    uncached_tokens: int = 0
    total_tokens: int = 0
    cost_total: float = 0.0


# ---------------------------------------------------------------------------
# 创建与 fork 选项
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class SessionCreateOptions:
    id: Optional[str] = None
    parent_session_id: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class ForkOptions:
    """fork 选项（对齐 TS ``ForkOptions & SessionCreateOptions`` 交叉）。

    - ``scope == "branch"``（默认）：从 ``entry_id``（缺省 main 叶子）复制分支；
      ``position``: ``"at"`` 复制到该消息本身，``"before"`` 复制到其父。
    - ``scope == "tree"``：整树复制（含全部 lane 与 label）。
    """

    scope: Literal["branch", "tree"] = "branch"
    entry_id: Optional[str] = None
    position: Optional[Literal["before", "at"]] = None
    id: Optional[str] = None
    parent_session_id: Optional[str] = None


# ---------------------------------------------------------------------------
# mutation 日志
# ---------------------------------------------------------------------------


class EntryLogItem(TypedDict):
    """``kind == "entry"``。"""

    kind: Literal["entry"]
    seq: int
    entry: Entry


class RecordLogItem(TypedDict):
    """``kind == "record"``。"""

    kind: Literal["record"]
    seq: int
    record: LaneRecord


class LaneLogItem(TypedDict):
    """``kind == "lane"``。"""

    kind: Literal["lane"]
    seq: int
    lane: str
    leaf_id: Optional[str]


class NameFactLogItem(TypedDict):
    """``kind == "fact"`` / ``fact == "name"``。"""

    kind: Literal["fact"]
    seq: int
    fact: Literal["name"]
    name: Optional[str]


class LabelFactLogItem(TypedDict):
    """``kind == "fact"`` / ``fact == "label"``。"""

    kind: Literal["fact"]
    seq: int
    fact: Literal["label"]
    target_id: str
    label: Optional[str]


LogItem = Union[EntryLogItem, RecordLogItem, LaneLogItem, NameFactLogItem, LabelFactLogItem]


# ---------------------------------------------------------------------------
# id 生成
# ---------------------------------------------------------------------------


class IdGenerator(Protocol):
    """entry id 生成器（默认 uuidv7——时间有序，落盘后天然可排序）。"""

    def next(self) -> str: ...


# ---------------------------------------------------------------------------
# 存储契约（Protocol——后端必须实现）
# ---------------------------------------------------------------------------


class SessionStorage(Protocol):
    """后端必须实现的结构化契约（对齐 TS ``SessionStorage`` 接口）。

    库层零路径假设——调用方注入具体后端（内存 / JSONL / SQLite…）。所有读取
    必须返回深拷贝，调用方不得穿透修改内部状态。
    """

    async def get_metadata(self) -> SessionMetadata: ...

    # Lanes
    async def get_lanes(self) -> List[LanePointer]: ...
    async def create_lane(self, lane: str, at: Optional[str]) -> None: ...
    async def move_lane(self, lane: str, to: Optional[str]) -> None: ...

    # Entries and records
    async def append_entry(self, entry: Entry, lane: str) -> Entry: ...
    """``entry`` 为 provisioned 载荷（缺 ``seq`` / ``parent_id`` / ``timestamp``），
    返回存储层补全后的完整形状。"""
    async def append_record(self, record: LaneRecord) -> LaneRecord: ...

    # Reads
    async def get_entry(self, entry_id: str) -> Optional[Entry]: ...
    async def find_entries(self, query: Optional[EntryQuery] = None) -> List[Entry]: ...
    async def find_entries_on_branch(self, query: BranchEntryQuery) -> List[Entry]: ...
    async def find_records(self, query: Optional[RecordQuery] = None) -> List[LaneRecord]: ...
    async def find_open_operations(
        self, lane: str, limit: Optional[int] = None
    ) -> List[OperationStartedRecord]: ...
    async def get_log(self, options: Optional[LogOptions] = None) -> List[LogItem]: ...

    # Global facts
    async def get_name(self) -> Optional[str]: ...
    async def set_name(self, name: Optional[str]) -> None: ...
    async def get_label(self, target_id: str) -> Optional[str]: ...
    async def set_label(self, target_id: str, label: Optional[str]) -> None: ...
    async def get_stats(self) -> SessionStats: ...


class SessionTree(Protocol):
    """树视图契约（对齐 TS ``SessionTree`` 接口）——``Session`` 与 lane 视图共同实现。"""

    async def get_leaf_id(self) -> Optional[str]: ...
    async def get_entry(self, entry_id: str) -> Optional[Entry]: ...
    async def get_stats(self) -> SessionStats: ...

    # Global facts. Latest wins; not branch-scoped. "set", not "append":
    # append vocabulary is reserved for tree writes.
    async def get_name(self) -> Optional[str]: ...
    async def set_name(self, name: Optional[str]) -> None: ...
    async def get_label(self, target_id: str) -> Optional[str]: ...
    async def set_label(self, target_id: str, label: Optional[str]) -> None: ...

    async def find_entries(self, query: Optional[EntryQuery] = None) -> List[Entry]: ...
    async def find_entry(self, query: Optional[EntryQuery] = None) -> Optional[Entry]: ...
    async def find_entries_on_branch(
        self, query: Optional[EntryQuery] = None, bounds: Optional[BranchBounds] = None
    ) -> List[Entry]: ...
    async def find_entry_on_branch(
        self, query: Optional[EntryQuery] = None, bounds: Optional[BranchBounds] = None
    ) -> Optional[Entry]: ...

    # Writes. Resolve on durable acceptance; the returned id is the entry's
    # id (provisioned when the write defers).
    async def append_message(self, message: Any) -> str: ...
    async def append_custom_entry(self, custom_type: str, data: Optional[Any] = None) -> str: ...


class SessionRepo(Protocol):
    """会话仓库契约（create / open / list / delete / fork）。

    ``open`` 负责获取后端写者声明；``list`` 不打开会话、不取写者声明。
    """

    async def create(self, options: Optional[SessionCreateOptions] = None) -> Any: ...
    async def open(self, metadata: SessionMetadata) -> Any: ...
    async def list(self) -> List[SessionMetadata]: ...
    async def delete(self, metadata: SessionMetadata) -> None: ...
    async def fork(self, source: SessionMetadata, options: Optional[ForkOptions] = None) -> Any: ...


__all__ = [
    "AbortRequestedRecord",
    "ActiveToolsEntry",
    "BranchBounds",
    "BranchEntryQuery",
    "BranchSummaryEntry",
    "CompactionEntry",
    "CompactionIntent",
    "CostData",
    "CustomEntry",
    "Entry",
    "EntryBase",
    "EntryCursor",
    "EntryLogItem",
    "EntryOrder",
    "EntryQuery",
    "EntryType",
    "ForkOptions",
    "IdGenerator",
    "LabelFactLogItem",
    "LaneLogItem",
    "LanePointer",
    "LaneRecord",
    "LogItem",
    "LogOptions",
    "MessageEntry",
    "ModelChangeEntry",
    "NameFactLogItem",
    "NavigationIntent",
    "OperationFinishedRecord",
    "OperationIntent",
    "OperationStartedRecord",
    "QueueCancelledRecord",
    "QueueEnqueuedRecord",
    "RecordBase",
    "RecordLogItem",
    "RecordQuery",
    "RunIntent",
    "SessionCreateOptions",
    "SessionError",
    "SessionErrorCode",
    "SessionMetadata",
    "SessionRepo",
    "SessionStats",
    "SessionStorage",
    "SessionTree",
    "StepAttemptRecord",
    "ThinkingLevelEntry",
    "ToolStartedRecord",
    "UsageData",
    "UsageRecord",
    "WriteDeferredRecord",
]
