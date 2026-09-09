"""Session——树视图（对齐 TS ``harness/session/session.ts``）。

包装 ``SessionStorage`` 提供高级 API，负责两件输入侧纪律：

- **durable payload 校验**（:func:`assert_json_serializable`）——在存储变更前拒绝
  不可 JSON 化的载荷，保证任何后端（含未来 JSONL）都能落盘；
- **id 分配**——默认 uuidv7，可通过 ``IdGenerator`` 注入。

Python 适配：TS 的 ``AgentMessage`` 是 plain object，nova 的 ``AgentMessage`` 是
pydantic 模型——``append_message`` 在边界处 ``model_dump(mode="json")``，使活路径
与重放路径（重新加载后为 dict）形状统一。``find_entries_on_branch`` 的
``EntryQuery & BranchBounds`` 交叉类型拆为两个参数（Python 无交叉类型）。
"""

from __future__ import annotations

import math
from typing import Any, List, Optional, Set

from pydantic import BaseModel

from ._ids import uuidv7
from .types import (
    BranchBounds,
    BranchEntryQuery,
    CustomEntry,
    Entry,
    EntryQuery,
    IdGenerator,
    LanePointer,
    LaneRecord,
    LogItem,
    LogOptions,
    OperationStartedRecord,
    RecordQuery,
    SessionError,
    SessionMetadata,
    SessionStats,
    SessionStorage,
    SessionTree,
)


def _invalid_payload(reason: str) -> None:
    raise SessionError("invalid_payload", f"Durable payload {reason}")


def assert_valid_limit(limit: Optional[int]) -> None:
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise SessionError("invalid_query", "limit must be a positive integer")


def assert_valid_cursor(after_seq: Optional[int]) -> None:
    if after_seq is not None and (
        not isinstance(after_seq, int) or isinstance(after_seq, bool) or after_seq < 0
    ):
        raise SessionError("invalid_query", "cursor sequence must be a non-negative integer")


def assert_json_serializable(value: Any) -> None:
    """校验载荷可安全落盘：仅 dict / list / str / int / float / bool / None。

    拒绝循环引用、非有限浮点数、非字符串 dict 键与其他任意类型（pydantic 模型
    须先在边界 dump）。环判定是带回溯的真 DFS（TS 对位用 ``{exit}`` 帧在子树
    收尾时出集合）——同一对象的**多次引用**（菱形/DAG）合法，只有真正的环
    （对象仍是自己的祖先）才拒绝。
    """
    active: Set[int] = set()
    # 帧二元组：(False, 值) 待访问；(True, 容器) 子树收尾出集合
    stack: List[Any] = [(False, value)]
    while stack:
        is_exit, candidate = stack.pop()
        if is_exit:
            active.discard(id(candidate))
            continue
        if candidate is None or isinstance(candidate, (str, bool, int)):
            continue
        if isinstance(candidate, float):
            if not math.isfinite(candidate):
                _invalid_payload("contains a non-finite number")
            continue
        if isinstance(candidate, dict):
            if id(candidate) in active:
                _invalid_payload("contains a cycle")
            active.add(id(candidate))
            stack.append((True, candidate))
            for key, item in candidate.items():
                if not isinstance(key, str):
                    _invalid_payload("contains a non-string key")
                stack.append((False, item))
            continue
        if isinstance(candidate, list):
            if id(candidate) in active:
                _invalid_payload("contains a cycle")
            active.add(id(candidate))
            stack.append((True, candidate))
            stack.extend((False, item) for item in candidate)
            continue
        _invalid_payload(f"contains {type(candidate).__name__}")


class _DefaultIdGenerator:
    """默认 id 生成器（uuidv7）。"""

    def next(self) -> str:
        return uuidv7()


class Session:
    """会话树视图：包装存储后端，暴露树级读写与查询 API（``"main"`` lane）。"""

    def __init__(self, storage: SessionStorage, id_generator: Optional[IdGenerator] = None) -> None:
        self._storage = storage
        self.id_generator: IdGenerator = id_generator or _DefaultIdGenerator()

    async def get_metadata(self) -> SessionMetadata:
        return await self._storage.get_metadata()

    def view(self, lane: str) -> SessionTree:
        """返回绑定到指定 lane 的视图（``"main"`` 返回自身）。"""
        if lane == "main":
            return self  # type: ignore[return-value]
        return _LaneView(self, lane)

    async def get_leaf_id(self) -> Optional[str]:
        return await self._get_leaf_id_for_lane("main")

    async def get_entry(self, entry_id: str) -> Optional[Entry]:
        return await self._storage.get_entry(entry_id)

    async def get_stats(self) -> SessionStats:
        return await self._storage.get_stats()

    async def get_name(self) -> Optional[str]:
        return await self._storage.get_name()

    async def set_name(self, name: Optional[str]) -> None:
        await self._storage.set_name(name)

    async def get_label(self, target_id: str) -> Optional[str]:
        return await self._storage.get_label(target_id)

    async def set_label(self, target_id: str, label: Optional[str]) -> None:
        await self._storage.set_label(target_id, label)

    async def find_entries(self, query: Optional[EntryQuery] = None) -> List[Entry]:
        return await self._query_entries(query or EntryQuery())

    async def find_entry(self, query: Optional[EntryQuery] = None) -> Optional[Entry]:
        results = await self._query_entries(query or EntryQuery(), result_limit=1)
        return results[0] if results else None

    async def find_entries_on_branch(
        self, query: Optional[EntryQuery] = None, bounds: Optional[BranchBounds] = None
    ) -> List[Entry]:
        return await self._query_branch_entries("main", query or EntryQuery(), bounds)

    async def find_entry_on_branch(
        self, query: Optional[EntryQuery] = None, bounds: Optional[BranchBounds] = None
    ) -> Optional[Entry]:
        results = await self._query_branch_entries(
            "main", query or EntryQuery(), bounds, result_limit=1
        )
        return results[0] if results else None

    async def append_message(self, message: Any) -> str:
        entry = await self._commit_entry(
            {"type": "message", "id": self.id_generator.next(), "message": _dump_message(message)},
            "main",
        )
        return entry["id"]

    async def append_custom_entry(self, custom_type: str, data: Optional[Any] = None) -> str:
        entry: CustomEntry = {
            "type": "custom",
            "id": self.id_generator.next(),
            "custom_type": custom_type,
        }
        if data is not None:
            entry["data"] = data
        committed = await self._commit_entry(entry, "main")
        return committed["id"]

    async def get_lanes(self) -> List[LanePointer]:
        return await self._storage.get_lanes()

    async def create_lane(self, lane: str, at: Optional[str]) -> None:
        await self._storage.create_lane(lane, at)

    async def move_lane(self, lane: str, to: Optional[str]) -> None:
        await self._storage.move_lane(lane, to)

    async def append_entry(self, entry: Entry, lane: str) -> Entry:
        """追加 provisioned entry（调用方给定 ``type`` 与 ``id``）。"""
        return await self._commit_entry(entry, lane)

    async def append_record(self, record: LaneRecord) -> LaneRecord:
        """追加操作留痕；存储层补 ``seq`` / ``timestamp``。"""
        assert_json_serializable(record)
        return await self._storage.append_record(record)

    async def find_records(self, query: Optional[RecordQuery] = None) -> List[LaneRecord]:
        return await self._query_records(query or RecordQuery())

    async def find_open_operations(
        self, lane: str, limit: Optional[int] = None
    ) -> List[OperationStartedRecord]:
        assert_valid_limit(limit)
        return await self._storage.find_open_operations(lane, limit)

    async def get_log(self, options: Optional[LogOptions] = None) -> List[LogItem]:
        opts = options or LogOptions()
        assert_valid_limit(opts.limit)
        assert_valid_cursor(opts.after_seq)
        return await self._storage.get_log(opts)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _get_leaf_id_for_lane(self, lane: str) -> Optional[str]:
        for pointer in await self.get_lanes():
            if pointer["lane"] == lane:
                return pointer["leaf_id"]
        raise SessionError("invalid_lane", f"Lane not found: {lane}")

    async def _query_entries(
        self, query: EntryQuery, result_limit: Optional[int] = None
    ) -> List[Entry]:
        assert_valid_limit(query.limit)
        assert_valid_cursor(query.cursor.after_seq if query.cursor is not None else None)
        return await self._storage.find_entries(_with_result_limit(query, result_limit))

    async def _query_branch_entries(
        self,
        default_lane: str,
        query: EntryQuery,
        bounds: Optional[BranchBounds] = None,
        result_limit: Optional[int] = None,
    ) -> List[Entry]:
        """从 ``bounds.start``（缺省 lane 当前叶子）向根查询。"""
        assert_valid_limit(query.limit)
        assert_valid_cursor(query.cursor.after_seq if query.cursor is not None else None)
        start = (bounds.start if bounds is not None else None) or await self._get_leaf_id_for_lane(
            default_lane
        )
        if start is None:
            return []
        storage_query = _with_result_limit(query, result_limit)
        return await self._storage.find_entries_on_branch(
            BranchEntryQuery(
                type=storage_query.type,
                custom_type=storage_query.custom_type,
                order=storage_query.order,
                limit=storage_query.limit,
                cursor=storage_query.cursor,
                start=start,
                stop_at_type=bounds.stop_at_type if bounds is not None else None,
                stop_at_id=bounds.stop_at_id if bounds is not None else None,
            )
        )

    async def _query_records(self, query: RecordQuery) -> List[LaneRecord]:
        assert_valid_limit(query.limit)
        assert_valid_cursor(query.after_seq)
        if query.operation_kind is not None and query.type != "operation_started":
            raise SessionError("invalid_query", 'operationKind requires type "operation_started"')
        return await self._storage.find_records(query)

    async def _commit_entry(self, entry: Entry, lane: str) -> Entry:
        assert_json_serializable(entry)
        return await self._storage.append_entry(entry, lane)


class _LaneView:
    """绑定到指定 lane 的树视图（对齐 TS ``Session.view`` 的对象字面量）。

    全部委托宿主 :class:`Session`，仅叶子解析与追加落在绑定 lane 上。
    """

    def __init__(self, session: Session, lane: str) -> None:
        self._session = session
        self._lane = lane

    async def get_leaf_id(self) -> Optional[str]:
        return await self._session._get_leaf_id_for_lane(self._lane)

    async def get_entry(self, entry_id: str) -> Optional[Entry]:
        return await self._session.get_entry(entry_id)

    async def get_stats(self) -> SessionStats:
        return await self._session.get_stats()

    async def get_name(self) -> Optional[str]:
        return await self._session.get_name()

    async def set_name(self, name: Optional[str]) -> None:
        await self._session.set_name(name)

    async def get_label(self, target_id: str) -> Optional[str]:
        return await self._session.get_label(target_id)

    async def set_label(self, target_id: str, label: Optional[str]) -> None:
        await self._session.set_label(target_id, label)

    async def find_entries(self, query: Optional[EntryQuery] = None) -> List[Entry]:
        return await self._session._query_entries(query or EntryQuery())

    async def find_entry(self, query: Optional[EntryQuery] = None) -> Optional[Entry]:
        results = await self._session._query_entries(query or EntryQuery(), result_limit=1)
        return results[0] if results else None

    async def find_entries_on_branch(
        self, query: Optional[EntryQuery] = None, bounds: Optional[BranchBounds] = None
    ) -> List[Entry]:
        return await self._session._query_branch_entries(self._lane, query or EntryQuery(), bounds)

    async def find_entry_on_branch(
        self, query: Optional[EntryQuery] = None, bounds: Optional[BranchBounds] = None
    ) -> Optional[Entry]:
        results = await self._session._query_branch_entries(
            self._lane, query or EntryQuery(), bounds, result_limit=1
        )
        return results[0] if results else None

    async def append_message(self, message: Any) -> str:
        entry = await self._session._commit_entry(
            {
                "type": "message",
                "id": self._session.id_generator.next(),
                "message": _dump_message(message),
            },
            self._lane,
        )
        return entry["id"]

    async def append_custom_entry(self, custom_type: str, data: Optional[Any] = None) -> str:
        entry: CustomEntry = {
            "type": "custom",
            "id": self._session.id_generator.next(),
            "custom_type": custom_type,
        }
        if data is not None:
            entry["data"] = data
        committed = await self._session._commit_entry(entry, self._lane)
        return committed["id"]


def _with_result_limit(query: EntryQuery, result_limit: Optional[int]) -> EntryQuery:
    """单条查询把结果上限压到 1，但不改动调用方传入的 query（校验用原值）。"""
    if result_limit is None or result_limit == query.limit:
        return query
    return EntryQuery(
        type=query.type,
        custom_type=query.custom_type,
        order=query.order,
        limit=result_limit,
        cursor=query.cursor,
    )


def _dump_message(message: Any) -> Any:
    """pydantic 消息在边界 dump 为 dict（nova 与 TS plain object 纪律的唯一分歧）。"""
    if isinstance(message, BaseModel):
        return message.model_dump(mode="json")
    return message
