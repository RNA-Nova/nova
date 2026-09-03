"""内存 SessionStorage / SessionRepo 后端（对齐 TS ``harness/session/memory.ts``）。

隔离纪律：对齐 TS ``structuredClone``——所有读取与写入返回
:func:`copy.deepcopy`，调用方拿到的是快照，不得穿透修改内部状态；追加时的入参
同样先深拷贝再入库（调用方事后改自己的 dict 不影响已提交内容）。
"""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import replace
from typing import Dict, List, Optional, cast

from ._ids import uuidv7
from .session import Session
from .state import (
    EntryMutation,
    LabelFactMutation,
    LaneMutation,
    NameFactMutation,
    RecordMutation,
    SessionState,
)
from .types import (
    BranchEntryQuery,
    Entry,
    EntryQuery,
    ForkOptions,
    LanePointer,
    LaneRecord,
    LogItem,
    LogOptions,
    OperationStartedRecord,
    RecordQuery,
    SessionCreateOptions,
    SessionError,
    SessionMetadata,
    SessionStats,
)


class InMemorySessionStorage:
    """内存实现的 SessionStorage（每个实例 = 一个独立会话）。"""

    def __init__(self, metadata: SessionMetadata) -> None:
        self._metadata = deepcopy(metadata)
        self._state = SessionState()

    def fork(self, metadata: SessionMetadata, options: ForkOptions) -> "InMemorySessionStorage":
        storage = InMemorySessionStorage(metadata)
        for mutation in self._state.create_fork_mutations(options):
            storage._state.apply_mutation(mutation)
        return storage

    async def get_metadata(self) -> SessionMetadata:
        return deepcopy(self._metadata)

    async def get_lanes(self) -> List[LanePointer]:
        return self._state.get_lanes()

    async def create_lane(self, lane: str, at: Optional[str]) -> None:
        self._state.validate_new_lane(lane)
        self._state.validate_target(at)
        self._state.apply_mutation(
            LaneMutation(seq=self._state.next_sequence, lane=lane, leaf_id=at)
        )

    async def move_lane(self, lane: str, to: Optional[str]) -> None:
        self._state.require_lane(lane)
        self._state.validate_target(to)
        self._state.apply_mutation(
            LaneMutation(seq=self._state.next_sequence, lane=lane, leaf_id=to)
        )

    async def append_entry(self, entry: Entry, lane: str) -> Entry:
        parent_id = self._state.require_lane(lane)
        self._state.validate_unused_id(entry["id"])
        committed = cast(
            Entry,
            {
                **deepcopy(entry),
                "parent_id": parent_id,
                "seq": self._state.next_sequence,
                "timestamp": int(time.time() * 1000),
            },
        )
        self._state.apply_mutation(EntryMutation(entry=committed, lane=lane))
        return deepcopy(committed)

    async def append_record(self, record: LaneRecord) -> LaneRecord:
        lane = record.get("lane")
        if not isinstance(lane, str):
            raise SessionError("invalid_payload", "Durable record requires a string lane")
        self._state.require_lane(lane)
        self._state.validate_unused_id(record["id"])
        open_operation_id = self._state.find_open_operations(lane, limit=1)
        if record.get("type") == "operation_started" and open_operation_id:
            raise SessionError(
                "storage",
                f"Lane {lane} already has an open operation {open_operation_id[0]['id']}",
            )
        committed = cast(
            LaneRecord,
            {
                **deepcopy(record),
                "seq": self._state.next_sequence,
                "timestamp": int(time.time() * 1000),
            },
        )
        self._state.apply_mutation(RecordMutation(record=committed))
        return deepcopy(committed)

    async def get_entry(self, entry_id: str) -> Optional[Entry]:
        entry = self._state.get_entry(entry_id)
        return None if entry is None else deepcopy(entry)

    async def find_entries(self, query: Optional[EntryQuery] = None) -> List[Entry]:
        return deepcopy(self._state.find_entries(query))

    async def find_entries_on_branch(self, query: BranchEntryQuery) -> List[Entry]:
        return deepcopy(self._state.find_entries_on_branch(query))

    async def find_records(self, query: Optional[RecordQuery] = None) -> List[LaneRecord]:
        return deepcopy(self._state.find_records(query))

    async def find_open_operations(
        self, lane: str, limit: Optional[int] = None
    ) -> List[OperationStartedRecord]:
        return deepcopy(self._state.find_open_operations(lane, limit))

    async def get_log(self, options: Optional[LogOptions] = None) -> List[LogItem]:
        return deepcopy(self._state.get_log(options))

    async def get_name(self) -> Optional[str]:
        return self._state.get_name()

    async def set_name(self, name: Optional[str]) -> None:
        self._state.apply_mutation(NameFactMutation(seq=self._state.next_sequence, name=name))

    async def get_label(self, target_id: str) -> Optional[str]:
        return self._state.get_label(target_id)

    async def set_label(self, target_id: str, label: Optional[str]) -> None:
        self._state.validate_target(target_id)
        self._state.apply_mutation(
            LabelFactMutation(seq=self._state.next_sequence, target_id=target_id, label=label)
        )

    async def get_stats(self) -> SessionStats:
        return replace(self._state.get_stats())


class InMemorySessionRepo:
    """内存会话仓库（create / open / list / delete / fork）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, InMemorySessionStorage] = {}

    async def create(
        self, options: Optional[SessionCreateOptions] = None
    ) -> "Session":
        opts = options or SessionCreateOptions()
        session_id = opts.id if opts.id is not None else uuidv7()
        if session_id in self._sessions:
            raise SessionError("already_exists", f"Session already exists: {session_id}")
        storage = InMemorySessionStorage(
            {
                "id": session_id,
                "created_at": int(time.time() * 1000),
                "parent_session_id": opts.parent_session_id,
            }
        )
        self._sessions[session_id] = storage
        return Session(storage)

    async def open(self, metadata: SessionMetadata) -> "Session":
        return Session(self._require_storage(metadata["id"]))

    async def list(self) -> List[SessionMetadata]:
        return [await storage.get_metadata() for storage in self._sessions.values()]

    async def delete(self, metadata: SessionMetadata) -> None:
        self._sessions.pop(metadata["id"], None)

    async def fork(
        self, source: SessionMetadata, options: Optional[ForkOptions] = None
    ) -> "Session":
        opts = options or ForkOptions()
        source_storage = self._require_storage(source["id"])
        session_id = opts.id if opts.id is not None else uuidv7()
        if session_id in self._sessions:
            raise SessionError("already_exists", f"Session already exists: {session_id}")
        storage = source_storage.fork(
            {
                "id": session_id,
                "created_at": int(time.time() * 1000),
                "parent_session_id": opts.parent_session_id or source["id"],
            },
            opts,
        )
        self._sessions[session_id] = storage
        return Session(storage)

    def _require_storage(self, session_id: str) -> InMemorySessionStorage:
        storage = self._sessions.get(session_id)
        if storage is None:
            raise SessionError("not_found", f"Session not found: {session_id}")
        return storage
