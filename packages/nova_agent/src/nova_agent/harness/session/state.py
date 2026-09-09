"""SessionState——纯 reducer（对齐 TS ``harness/session/state.ts``）。

不可变纪律：所有状态变更都通过 :meth:`SessionState.apply_mutation` 进入，每个
mutation 携带严格递增的 ``seq``（entry / record 的 ``seq`` 在载荷内，lane / fact
的在 mutation 顶层——与 TS 判别联合一致）。reducer 负责校验（连续性 / 唯一性 /
引用完整性）并更新全部索引。此设计让 fork = 重放 mutation 列表，让恢复 = 重放
JSONL。

``None`` 语义注意：TS 以 ``undefined``（缺键）与 ``null``（空叶子）区分"lane 不
存在"与"lane 存在但无条目"，Python dict 两者同为 ``None``——一切 lane 判存必须
用键存在性（``in``），禁止 ``dict.get`` 后判 ``None``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Union, cast

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
    SessionError,
    SessionStats,
)


@dataclass(frozen=True)
class EntryMutation:
    """追加内容条目；``lane`` 非 ``None`` 时校验父子链并推进该 lane 叶子。"""

    entry: Entry
    lane: Optional[str] = None


@dataclass(frozen=True)
class RecordMutation:
    """追加操作留痕。"""

    record: LaneRecord


@dataclass(frozen=True)
class LaneMutation:
    """创建（``validate_new_lane`` 先行）或移动 lane 叶子指针。"""

    seq: int
    lane: str
    leaf_id: Optional[str]


@dataclass(frozen=True)
class NameFactMutation:
    """设置会话名（latest-wins 全局事实）。"""

    seq: int
    name: Optional[str]


@dataclass(frozen=True)
class LabelFactMutation:
    """设置 / 清除条目标签（``label=None`` 清除）。"""

    seq: int
    target_id: str
    label: Optional[str]


SessionMutation = Union[EntryMutation, RecordMutation, LaneMutation, NameFactMutation, LabelFactMutation]
"""判别联合：entry / record / lane / name fact / label fact 五种状态变更。"""

_INVALID_MUTATION_CALLBACK = Callable[[str], None]


def _invalid_mutation(message: str) -> None:
    raise SessionError("invalid_entry", f"Invalid session mutation: {message}")


def _mutation_seq(mutation: SessionMutation) -> int:
    if isinstance(mutation, EntryMutation):
        return int(mutation.entry.get("seq", 0))
    if isinstance(mutation, RecordMutation):
        return int(mutation.record.get("seq", 0))
    return mutation.seq


def _assert_valid_limit(limit: Optional[int]) -> None:
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise SessionError("invalid_query", "limit must be a positive integer")


def _assert_valid_cursor(after_seq: Optional[int]) -> None:
    if after_seq is not None and (not isinstance(after_seq, int) or isinstance(after_seq, bool) or after_seq < 0):
        raise SessionError("invalid_query", "cursor sequence must be a non-negative integer")


def _ordered(items: List[Any], order: Optional[str]) -> Iterator[Any]:
    if order == "oldestFirst":
        return iter(items)
    return reversed(items)


class SessionState:
    """内存中的会话状态机（对齐 TS ``SessionState``）。"""

    def __init__(self) -> None:
        self._sequence: int = 0
        self._used_ids: Set[str] = set()
        self._entries: List[Entry] = []
        self._entries_by_id: Dict[str, Entry] = {}
        self._records: List[LaneRecord] = []
        self._open_operations_by_lane: Dict[str, Dict[str, OperationStartedRecord]] = {}
        self._lanes: Dict[str, Optional[str]] = {"main": None}
        self._log: List[LogItem] = []
        self._stats = SessionStats()
        self._name: Optional[str] = None
        self._labels: Dict[str, str] = {}

    @property
    def next_sequence(self) -> int:
        return self._sequence + 1

    def get_lanes(self) -> List[LanePointer]:
        return [{"lane": lane, "leaf_id": leaf_id} for lane, leaf_id in self._lanes.items()]

    def require_lane(self, lane: str) -> Optional[str]:
        """返回 lane 当前叶子（空车道为 ``None``）；lane 不存在时抛错。"""
        if lane not in self._lanes:
            raise SessionError("invalid_lane", f"Lane not found: {lane}")
        return self._lanes[lane]

    def validate_new_lane(self, lane: str) -> None:
        if lane in self._lanes:
            raise SessionError("already_exists", f"Lane already exists: {lane}")

    def validate_target(self, target_id: Optional[str]) -> None:
        if target_id is not None and target_id not in self._entries_by_id:
            raise SessionError("not_found", f"Entry not found: {target_id}")

    def validate_unused_id(self, entry_id: str) -> None:
        if entry_id in self._used_ids:
            raise SessionError("already_exists", f"Session id already exists: {entry_id}")

    # ------------------------------------------------------------------
    # Mutation reducer
    # ------------------------------------------------------------------

    def apply_mutation(
        self,
        mutation: SessionMutation,
        invalid: Optional[_INVALID_MUTATION_CALLBACK] = None,
    ) -> None:
        """校验并应用一次状态变更（唯一的状态修改入口）。

        ``invalid`` 允许重放侧（JSONL codec）把无效行归为 payload 错误而非直接抛出。
        """
        on_invalid = invalid or _invalid_mutation
        seq = _mutation_seq(mutation)
        if seq != self._sequence + 1:
            on_invalid(f"has non-consecutive seq {seq}")
            return

        if isinstance(mutation, EntryMutation):
            self._apply_entry(mutation, on_invalid)
        elif isinstance(mutation, RecordMutation):
            self._apply_record(mutation, on_invalid)
        elif isinstance(mutation, LaneMutation):
            self._apply_lane(mutation, on_invalid)
        elif isinstance(mutation, NameFactMutation):
            self._apply_name_fact(mutation, on_invalid)
        elif isinstance(mutation, LabelFactMutation):
            self._apply_label_fact(mutation, on_invalid)
        else:  # pragma: no cover — SessionMutation 类型已穷尽
            on_invalid(f"has unknown mutation kind {type(mutation).__name__}")

    def _apply_entry(self, mutation: EntryMutation, on_invalid: _INVALID_MUTATION_CALLBACK) -> None:
        entry = mutation.entry
        entry_id = entry.get("id")
        if entry_id in self._used_ids:
            on_invalid(f"contains duplicate id {entry_id}")
            return
        if mutation.lane is not None:
            if mutation.lane not in self._lanes:
                on_invalid(f"references missing lane {mutation.lane}")
                return
            if entry.get("parent_id") != self._lanes[mutation.lane]:
                on_invalid("does not chain to the lane leaf")
                return
        parent_id = entry.get("parent_id")
        if parent_id is not None and parent_id not in self._entries_by_id:
            on_invalid(f"references missing parent {parent_id}")
            return

        seq = int(entry.get("seq", 0))
        self._sequence = seq
        self._used_ids.add(entry_id)
        self._entries.append(entry)
        self._entries_by_id[entry_id] = entry
        if mutation.lane is not None:
            self._lanes[mutation.lane] = entry_id
        self._log.append({"kind": "entry", "seq": seq, "entry": entry})
        if entry.get("type") == "message":
            self._stats.message_count += 1

    def _apply_record(self, mutation: RecordMutation, on_invalid: _INVALID_MUTATION_CALLBACK) -> None:
        record = mutation.record
        lane = record.get("lane")
        if not isinstance(lane, str) or lane not in self._lanes:
            on_invalid(f"references missing lane {lane}")
            return
        record_id = record.get("id")
        if record_id in self._used_ids:
            on_invalid(f"contains duplicate id {record_id}")
            return

        seq = int(record.get("seq", 0))
        self._sequence = seq
        self._used_ids.add(record_id)
        self._records.append(record)

        if record.get("type") == "operation_started":
            open_operations = self._open_operations_by_lane.setdefault(lane, {})
            open_operations[record_id] = cast(OperationStartedRecord, record)
        elif record.get("type") == "operation_finished":
            open_operations = self._open_operations_by_lane.get(lane)
            run_id = record.get("run_id")
            if open_operations is not None and isinstance(run_id, str):
                open_operations.pop(run_id, None)

        self._log.append({"kind": "record", "seq": seq, "record": record})
        if record.get("type") == "usage":
            usage = record.get("usage") or {}
            self._stats.cached_tokens += usage.get("cache_read", 0)
            self._stats.uncached_tokens += usage.get("input", 0) + usage.get("cache_write", 0)
            self._stats.total_tokens += usage.get("total_tokens", 0)
            self._stats.cost_total += usage.get("cost", {}).get("total", 0)

    def _apply_lane(self, mutation: LaneMutation, on_invalid: _INVALID_MUTATION_CALLBACK) -> None:
        leaf_id = mutation.leaf_id
        if leaf_id is not None and leaf_id not in self._entries_by_id:
            on_invalid(f"references missing lane target {leaf_id}")
            return
        self._sequence = mutation.seq
        self._lanes[mutation.lane] = leaf_id
        self._log.append(
            {"kind": "lane", "seq": mutation.seq, "lane": mutation.lane, "leaf_id": leaf_id}
        )

    def _apply_name_fact(self, mutation: NameFactMutation, on_invalid: _INVALID_MUTATION_CALLBACK) -> None:
        self._sequence = mutation.seq
        self._name = mutation.name
        self._log.append({"kind": "fact", "seq": mutation.seq, "fact": "name", "name": mutation.name})

    def _apply_label_fact(self, mutation: LabelFactMutation, on_invalid: _INVALID_MUTATION_CALLBACK) -> None:
        if mutation.target_id not in self._entries_by_id:
            on_invalid(f"references missing label target {mutation.target_id}")
            return
        self._sequence = mutation.seq
        if mutation.label is None:
            self._labels.pop(mutation.target_id, None)
        else:
            self._labels[mutation.target_id] = mutation.label
        self._log.append(
            {
                "kind": "fact",
                "seq": mutation.seq,
                "fact": "label",
                "target_id": mutation.target_id,
                "label": mutation.label,
            }
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_entry(self, entry_id: str) -> Optional[Entry]:
        return self._entries_by_id.get(entry_id)

    def find_entries(self, query: Optional[EntryQuery] = None) -> List[Entry]:
        q = query or EntryQuery()
        _assert_valid_limit(q.limit)
        _assert_valid_cursor(q.cursor.after_seq if q.cursor is not None else None)
        results: List[Entry] = []
        for entry in _ordered(self._entries, q.order):
            if not self._matches_entry_query(entry, q):
                continue
            results.append(entry)
            if q.limit is not None and len(results) >= q.limit:
                break
        return results

    def find_entries_on_branch(self, query: BranchEntryQuery) -> List[Entry]:
        _assert_valid_limit(query.limit)
        _assert_valid_cursor(query.cursor.after_seq if query.cursor is not None else None)
        results: List[Entry] = []
        if query.order == "oldestFirst":
            # 对齐 TS：oldestFirst 走完整路径，stop 边界在根→叶迭代时应用——
            # 若在叶→根 walk 中早停，根侧前缀会丢失。
            branch = list(self._walk_to_root(query.start))
            branch.reverse()
            for entry in branch:
                reached_bound = entry["id"] == query.stop_at_id or entry.get("type") == query.stop_at_type
                if self._matches_entry_query(entry, query):
                    results.append(entry)
                if reached_bound or (query.limit is not None and len(results) >= query.limit):
                    break
        else:
            for entry in self._walk_to_root(query.start, query.stop_at_id, query.stop_at_type):
                if self._matches_entry_query(entry, query):
                    results.append(entry)
                    if query.limit is not None and len(results) >= query.limit:
                        break
        return results

    def find_records(self, query: Optional[RecordQuery] = None) -> List[LaneRecord]:
        q = query or RecordQuery()
        _assert_valid_limit(q.limit)
        _assert_valid_cursor(q.after_seq)
        results: List[LaneRecord] = []
        for record in _ordered(self._records, q.order):
            if not self._matches_record_query(record, q):
                continue
            results.append(record)
            if q.limit is not None and len(results) >= q.limit:
                break
        return results

    def find_open_operations(
        self, lane: str, limit: Optional[int] = None
    ) -> List[OperationStartedRecord]:
        """返回未完结的 operation start（newest first）。

        恢复侧用 ``limit=2``：0 个 = 空闲，1 个 = 挂起，≥2 个 = 损坏。
        """
        _assert_valid_limit(limit)
        operations_by_id = self._open_operations_by_lane.get(lane)
        operations = list(operations_by_id.values())[::-1] if operations_by_id else []
        return operations if limit is None else operations[:limit]

    def get_log(self, options: Optional[LogOptions] = None) -> List[LogItem]:
        opts = options or LogOptions()
        _assert_valid_limit(opts.limit)
        _assert_valid_cursor(opts.after_seq)
        results: List[LogItem] = []
        for item in self._log:
            if opts.after_seq is not None and item["seq"] <= opts.after_seq:
                continue
            results.append(item)
            if opts.limit is not None and len(results) >= opts.limit:
                break
        return results

    def get_name(self) -> Optional[str]:
        return self._name

    def get_label(self, target_id: str) -> Optional[str]:
        return self._labels.get(target_id)

    def get_stats(self) -> SessionStats:
        return self._stats

    # ------------------------------------------------------------------
    # Fork 支持
    # ------------------------------------------------------------------

    def create_fork_mutations(self, options: ForkOptions) -> List[Any]:
        """生成 fork 所需的 mutation 列表（对齐 TS ``createForkMutations``）。

        顺序：entries（重编 seq）→ lane 指针 → name → 已复制条目的 label。
        record 一律不复制——留痕属于源会话。
        """
        if options.scope == "tree":
            copied_entries = list(self._entries)
            fork_lanes = self.get_lanes()
        else:
            selected_entry_id = options.entry_id if options.entry_id is not None else self.require_lane("main")
            target_id: Optional[str] = None
            if selected_entry_id is not None:
                entry = self._entries_by_id.get(selected_entry_id)
                if entry is None or entry.get("type") != "message":
                    raise SessionError(
                        "invalid_fork_target", f"Fork target is not a message entry: {selected_entry_id}"
                    )
                position = options.position or ("at" if options.entry_id is None else "before")
                target_id = entry["id"] if position == "at" else entry.get("parent_id")
            if target_id is None:
                copied_entries = []
            else:
                branch = list(self._walk_to_root(target_id))
                branch.reverse()
                copied_entries = branch
            fork_lanes: List[LanePointer] = [{"lane": "main", "leaf_id": target_id}]

        mutations: List[Any] = []
        sequence = 1
        for source_entry in copied_entries:
            clone: Dict[str, Any] = dict(source_entry)
            clone["seq"] = sequence
            sequence += 1
            mutations.append(EntryMutation(entry=cast(Entry, clone)))
        for pointer in fork_lanes:
            mutations.append(
                LaneMutation(seq=sequence, lane=pointer["lane"], leaf_id=pointer["leaf_id"])
            )
            sequence += 1
        if self._name is not None:
            mutations.append(NameFactMutation(seq=sequence, name=self._name))
            sequence += 1
        for entry in copied_entries:
            label = self._labels.get(entry["id"])
            if label is not None:
                mutations.append(LabelFactMutation(seq=sequence, target_id=entry["id"], label=label))
                sequence += 1
        return mutations

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _walk_to_root(
        self,
        start: Optional[str],
        stop_at_id: Optional[str] = None,
        stop_at_type: Optional[str] = None,
    ) -> Iterator[Entry]:
        if start is None:
            return
        visited: Set[str] = set()
        current = self._entries_by_id.get(start)
        if current is None:
            raise SessionError("not_found", f"Entry not found: {start}")
        while current:
            if current["id"] in visited:
                raise SessionError(
                    "invalid_entry", f"Session branch contains a cycle at {current['id']}"
                )
            visited.add(current["id"])
            yield current
            if current["id"] == stop_at_id or current.get("type") == stop_at_type:
                break
            parent_id = current.get("parent_id")
            if parent_id is None:
                break
            current = self._entries_by_id.get(parent_id)
            if current is None:
                raise SessionError("invalid_entry", f"Entry not found: {parent_id}")

    @staticmethod
    def _matches_entry_query(entry: Entry, query: EntryQuery) -> bool:
        if query.type is not None and entry.get("type") != query.type:
            return False
        if query.custom_type is not None and (
            entry.get("type") != "custom" or entry.get("custom_type") != query.custom_type
        ):
            return False
        if query.cursor is not None:
            seq = entry.get("seq", 0)
            if query.order == "oldestFirst":
                if seq <= query.cursor.after_seq:
                    return False
            else:
                if seq >= query.cursor.after_seq:
                    return False
        return True

    @staticmethod
    def _matches_record_query(record: LaneRecord, query: RecordQuery) -> bool:
        if query.lane is not None and record.get("lane") != query.lane:
            return False
        if query.type is not None and record.get("type") != query.type:
            return False
        if query.run_id is not None:
            if record.get("type") == "operation_started":
                if record.get("id") != query.run_id:
                    return False
            elif record.get("run_id") != query.run_id:
                return False
        if query.operation_kind is not None and (
            record.get("type") != "operation_started"
            or record.get("intent", {}).get("kind") != query.operation_kind
        ):
            return False
        if query.after_seq is not None and record.get("seq", 0) <= query.after_seq:
            return False
        return True
