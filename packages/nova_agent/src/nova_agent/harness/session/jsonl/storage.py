"""JSONL 会话存储（对齐 TS ``session/jsonl/storage.ts``）。

追加写 + 启动重放：写路径经 ``encode_mutation`` 追加一行；读路径（``load``）
重放全部 mutation 重建 :class:`SessionState`。纪律：

- **原子发布**（``_publish_file_atomically``）：写完整兄弟临时文件后 rename——
  崩溃最多留下一个被忽略的 ``.tmp``；同一目标的发布必须由调用方串行化。
- **撕裂尾修复**：``load`` 遇到**最后一行**的 syntax 错误 = 未获确认的半截
  追加（进程中断），原子重写有效前缀；其余行错误一律 ``invalid_entry``。
- **写串行**（``_lock``）：同实例的 mutation 先追加落盘、后应用内存状态。
"""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from dataclasses import replace
from typing import Awaitable, Callable, List, Optional, TypeVar, cast

from ..state import (
    EntryMutation,
    LabelFactMutation,
    LaneMutation,
    NameFactMutation,
    RecordMutation,
    SessionMutation,
    SessionState,
)
from ..types import (
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
    SessionError,
    SessionStats,
)
from .codec import (
    encode_header,
    encode_mutation,
    metadata_from_header,
    parse_header,
    parse_mutation,
)
from .errors import JsonlDecodeError, file_result, invalid_file
from .types import JsonlFileSystem, JsonlSessionMetadata, JsonlV4Header

__all__ = ["JsonlSessionStorage"]

T = TypeVar("T")


async def _publish_file_atomically(
    fs: JsonlFileSystem,
    destination_path: str,
    populate: Callable[[str], Awaitable[None]],
) -> None:
    """写完整临时文件后原子 rename 覆盖目标（对齐 TS ``publishFileAtomically``）。"""
    temp_path = f"{destination_path}.tmp"
    try:
        await populate(temp_path)
        await file_result(
            fs.rename_file(temp_path, destination_path),
            f"Failed to publish staged file {destination_path}",
        )
    except BaseException:
        try:
            await fs.remove(temp_path, force=True)
        except OSError:
            pass  # 临时文件清理尽力而为，保留原始错误
        raise


class JsonlSessionStorage:
    """JSONL 文件版 SessionStorage（每个实例对应一个 ``*.jsonl`` 会话文件）。"""

    def __init__(self, fs: JsonlFileSystem, metadata: JsonlSessionMetadata) -> None:
        self._fs = fs
        self._metadata: JsonlSessionMetadata = deepcopy(metadata)
        self._state = SessionState()
        self._lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        fs: JsonlFileSystem,
        path: str,
        header: JsonlV4Header,
    ) -> "JsonlSessionStorage":
        await file_result(
            fs.write_file(path, encode_header(header)),
            f"Failed to initialize session {path}",
        )
        info = await file_result(fs.file_info(path), f"Failed to read session metadata {path}")
        return cls(fs, metadata_from_header(header, path, info["mtime_ms"]))  # type: ignore[arg-type]

    @classmethod
    async def load(cls, fs: JsonlFileSystem, path: str) -> "JsonlSessionStorage":
        content = await file_result(fs.read_text_file(path), f"Failed to read session {path}")
        physical_lines = content.split("\n")
        if physical_lines and physical_lines[-1] == "":
            physical_lines.pop()
        if not physical_lines or not physical_lines[0]:
            raise invalid_file(path, 1, JsonlDecodeError("schema", "is missing a header"))
        ok, header_or_error = parse_header(physical_lines[0])
        if not ok:
            raise invalid_file(path, 1, header_or_error)
        info = await file_result(fs.file_info(path), f"Failed to read session metadata {path}")
        storage = cls(fs, metadata_from_header(header_or_error, path, info["mtime_ms"]))  # type: ignore[arg-type]
        for index in range(1, len(physical_lines)):
            line = physical_lines[index]
            ok, mutation_or_error = parse_mutation(line)
            if not ok:
                error: JsonlDecodeError = mutation_or_error
                is_torn_tail = index == len(physical_lines) - 1 and error.kind == "syntax"
                if is_torn_tail:
                    # 丢弃未获确认的半截追加：原子重写有效前缀
                    valid_prefix = "\n".join(physical_lines[:index]) + "\n"

                    async def _stage(temp_path: str, prefix: str = valid_prefix) -> None:
                        await file_result(
                            fs.write_file(temp_path, prefix),
                            f"Failed to stage torn-tail repair {path}",
                        )

                    await _publish_file_atomically(fs, path, _stage)
                    return storage
                raise invalid_file(path, index + 1, error)
            try:
                storage._apply_mutation(mutation_or_error)
            except SessionError as exc:
                if exc.code == "invalid_entry":
                    raise invalid_file(path, index + 1, exc) from exc
                raise
        if not content.endswith("\n"):
            await file_result(
                fs.append_file(path, "\n"),
                f"Failed to repair unterminated session tail {path}",
            )
        return storage

    async def fork(
        self, path: str, header: JsonlV4Header, options: ForkOptions
    ) -> "JsonlSessionStorage":
        mutations = self._state.create_fork_mutations(options)

        async def _populate(temp_path: str) -> None:
            target = await JsonlSessionStorage.create(self._fs, temp_path, header)
            for mutation in mutations:
                await target._append_mutation(mutation)
                target._apply_mutation(mutation)

        await _publish_file_atomically(self._fs, path, _populate)
        return await JsonlSessionStorage.load(self._fs, path)

    async def drain(self) -> None:
        """等待全部排队中的落盘操作完成（对齐 TS ``drain``；锁公平队列）。"""
        async with self._lock:
            return None

    async def get_metadata(self) -> JsonlSessionMetadata:
        return deepcopy(self._metadata)

    async def get_lanes(self) -> List[LanePointer]:
        return self._state.get_lanes()

    async def create_lane(self, lane: str, at: Optional[str]) -> None:
        async with self._lock:
            self._state.validate_new_lane(lane)
            self._state.validate_target(at)
            mutation: SessionMutation = LaneMutation(
                seq=self._state.next_sequence, lane=lane, leaf_id=at
            )
            await self._append_mutation(mutation)
            self._apply_mutation(mutation)

    async def move_lane(self, lane: str, to: Optional[str]) -> None:
        async with self._lock:
            self._state.require_lane(lane)
            self._state.validate_target(to)
            mutation: SessionMutation = LaneMutation(
                seq=self._state.next_sequence, lane=lane, leaf_id=to
            )
            await self._append_mutation(mutation)
            self._apply_mutation(mutation)

    async def append_entry(self, entry: Entry, lane: str) -> Entry:
        async with self._lock:
            parent_id = self._state.require_lane(lane)
            self._state.validate_unused_id(entry["id"])
            committed = cast(
                Entry,
                {
                    **deepcopy(dict(entry)),
                    "parent_id": parent_id,
                    "seq": self._state.next_sequence,
                    "timestamp": int(time.time() * 1000),
                },
            )
            mutation: SessionMutation = EntryMutation(entry=committed, lane=lane)
            await self._append_mutation(mutation)
            self._apply_mutation(mutation)
            return deepcopy(committed)

    async def append_record(self, record: LaneRecord) -> LaneRecord:
        async with self._lock:
            lane = record.get("lane")
            if not isinstance(lane, str):
                raise SessionError("invalid_payload", "Durable record requires a string lane")
            self._state.require_lane(lane)
            self._state.validate_unused_id(record["id"])
            open_operation = self._state.find_open_operations(lane, limit=1)
            if record.get("type") == "operation_started" and open_operation:
                raise SessionError(
                    "storage",
                    f"Lane {lane} already has an open operation {open_operation[0]['id']}",
                )
            committed = cast(
                LaneRecord,
                {
                    **deepcopy(dict(record)),
                    "seq": self._state.next_sequence,
                    "timestamp": int(time.time() * 1000),
                },
            )
            mutation: SessionMutation = RecordMutation(record=committed)
            await self._append_mutation(mutation)
            self._apply_mutation(mutation)
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
        async with self._lock:
            mutation: SessionMutation = NameFactMutation(
                seq=self._state.next_sequence, name=name
            )
            await self._append_mutation(mutation)
            self._apply_mutation(mutation)

    async def get_label(self, target_id: str) -> Optional[str]:
        return self._state.get_label(target_id)

    async def set_label(self, target_id: str, label: Optional[str]) -> None:
        async with self._lock:
            self._state.validate_target(target_id)
            mutation: SessionMutation = LabelFactMutation(
                seq=self._state.next_sequence, target_id=target_id, label=label
            )
            await self._append_mutation(mutation)
            self._apply_mutation(mutation)

    async def get_stats(self) -> SessionStats:
        return replace(self._state.get_stats())

    async def _append_mutation(self, mutation: SessionMutation) -> None:
        await file_result(
            self._fs.append_file(self._metadata["path"], encode_mutation(mutation)),
            f"Failed to append session {self._metadata['path']}",
        )

    def _apply_mutation(self, mutation: SessionMutation) -> None:
        self._state.apply_mutation(mutation)

