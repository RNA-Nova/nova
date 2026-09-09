"""SessionStorage 后端一致性套件（对齐 TS ``harness/session/testing/conformance.ts``）。

任何 ``SessionRepo`` 实现（内存 / JSONL / SQLite…）都跑同一组用例：套件放在
src 侧，新后端直接复用。每个用例经 fixture 工厂自建自清仓库。

Python 适配：TS 用例里的 ``undefined`` 载荷在 Python 是合法 JSON ``null``，
非 JSON 值集合换成 set / bytes / datetime / 任意对象 / 非字符串键 / 循环引用。
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List

from ..types import (
    BranchBounds,
    EntryCursor,
    EntryQuery,
    ForkOptions,
    LogOptions,
    RecordQuery,
    SessionCreateOptions,
    SessionError,
    SessionErrorCode,
)

FixtureFactory = Callable[[], AbstractAsyncContextManager[Any]]
"""工厂返回异步上下文管理器，``__aenter__`` 产出 ``SessionRepo``。"""


@dataclass(frozen=True)
class ConformanceCase:
    group: str
    name: str
    run: Callable[[], Any]
    """无参异步入口——repository 经 fixture 闭包注入。"""


def create_user_message(text: str) -> Dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}], "timestamp": 1}


def create_assistant_message(text: str) -> Dict[str, Any]:
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "api": "anthropic-messages",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "usage": {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
            "total_tokens": 0,
            "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
        },
        "stop_reason": "stop",
        "timestamp": 1,
    }


def operation_started(
    record_id: str, lane: str, kind: str
) -> Dict[str, Any]:
    """构造 operation_started 留痕（三种 intent）。"""
    if kind == "run":
        intent: Dict[str, Any] = {"kind": kind, "original_prompt": [], "initial_messages": []}
    elif kind == "compaction":
        intent = {"kind": kind, "result_entry_id": f"{record_id}-result"}
    elif kind == "navigation":
        intent = {"kind": kind, "target_id": None, "summarize": False}
    else:  # pragma: no cover — 测试辅助
        raise ValueError(f"Unknown operation kind: {kind}")
    return {
        "type": "operation_started",
        "id": record_id,
        "lane": lane,
        "source_leaf_id": None,
        "intent": intent,
    }


async def entry_ids(entries: Any) -> List[str]:
    return [entry["id"] for entry in await entries]


def _expected_code(operation: Any, code: SessionErrorCode) -> Any:
    async def run() -> None:
        try:
            await operation
        except SessionError as error:
            assert error.code == code, f"Expected SessionError[{code}], got [{error.code}]: {error}"
        else:
            raise AssertionError(f"Expected SessionError with code {code}")

    return run()


def create_session_backend_conformance(factory: FixtureFactory) -> List[ConformanceCase]:
    """构建后端一致性用例。每个用例自建自清 fixture。"""

    def case(group: str, name: str, test: Callable[[Any], Any]) -> ConformanceCase:
        async def run() -> None:
            async with factory() as repository:
                await test(repository)

        return ConformanceCase(group=group, name=name, run=run)


    return [
        case(
            "entries and lanes",
            "assigns parents and one sequence across every mutation",
            _assigns_parents_and_one_sequence,
        ),
        case(
            "records and log",
            "commits records and lane moves as separate mutations",
            _commits_records_and_lane_moves,
        ),
        case(
            "entries and lanes",
            "rejects duplicate ids without changing state",
            _rejects_duplicate_ids,
        ),
        case(
            "entries and lanes",
            "isolates lanes while sharing the tree",
            _isolates_lanes,
        ),
        case(
            "queries and facts",
            "rejects invalid queries before empty reads",
            _rejects_invalid_queries,
        ),
        case(
            "queries and facts",
            "supports bounded filtered and cursor-based queries",
            _supports_bounded_queries,
        ),
        case(
            "records and log",
            "keeps lane names permanent with their recovery records",
            _keeps_lane_names_permanent,
        ),
        case(
            "records and log",
            "persists queue cancellation without consuming its target",
            _persists_queue_cancellation,
        ),
        case(
            "records and log",
            "filters records by lane type run sequence and order",
            _filters_records,
        ),
        case(
            "records and log",
            "filters operation starts by operation kind",
            _filters_operation_kinds,
        ),
        case(
            "records and log",
            "tracks and enforces one open operation per lane",
            _enforces_one_open_operation,
        ),
        case(
            "records and log",
            "does not let an earlier finish close a later start",
            _earlier_finish_does_not_close_later_start,
        ),
        case(
            "records and log",
            "scopes open operations by lane and limit",
            _scopes_open_operations,
        ),
        case(
            "validation and immutability",
            "returns immutable open-operation records",
            _returns_immutable_open_operations,
        ),
        case(
            "queries and facts",
            "keeps latest-value facts and computes ledger statistics across lanes",
            _computes_ledger_statistics,
        ),
        case(
            "queries and facts",
            "clears session names durably",
            _clears_session_names,
        ),
        case(
            "validation and immutability",
            "returns immutable copies from reads",
            _returns_immutable_copies,
        ),
        case(
            "entries and lanes",
            "validates lane lifecycle and targets",
            _validates_lane_lifecycle,
        ),
        case(
            "entries and lanes",
            "binds lane views without caching leaves",
            _binds_lane_views,
        ),
        case(
            "entries and lanes",
            "appends provisioned entries with their existing ids",
            _appends_provisioned_entries,
        ),
        case(
            "entries and lanes",
            "persists tool-result termination decisions",
            _persists_termination_decisions,
        ),
        case(
            "validation and immutability",
            "rejects non-JSON entries before storage mutation",
            _rejects_non_json_entries,
        ),
        case(
            "validation and immutability",
            "rejects non-JSON records before storage mutation",
            _rejects_non_json_records,
        ),
        case(
            "entries and lanes",
            "linearizes concurrent writes across two lanes",
            _linearizes_concurrent_writes,
        ),
        case(
            "repository and forks",
            "creates lists and opens sessions",
            _creates_lists_and_opens,
        ),
        case(
            "repository and forks",
            "deletes sessions idempotently",
            _deletes_idempotently,
        ),
        case(
            "repository and forks",
            "forks one branch with selected facts and no records",
            _forks_branch,
        ),
        case(
            "repository and forks",
            "forks a complete tree with lanes and facts",
            _forks_tree,
        ),
        case(
            "repository and forks",
            "forks before an entry without modifying the source",
            _forks_before_entry,
        ),
        case(
            "repository and forks",
            "validates the default fork target",
            _validates_default_fork_target,
        ),
    ]


# ---------------------------------------------------------------------------
# entries and lanes
# ---------------------------------------------------------------------------


async def _assigns_parents_and_one_sequence(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    root = await session.append_entry(
        {"type": "message", "id": "root", "message": create_user_message("root")}, "main"
    )
    await session.create_lane("thread", root["id"])
    child = await session.append_entry(
        {"type": "custom", "id": "child", "custom_type": "note", "data": {"value": 1}}, "thread"
    )
    record = await session.append_record(operation_started("run", "thread", "run"))
    await session.set_name("Example")
    await session.set_label("root", "checkpoint")
    await session.move_lane("main", "child")

    assert {"parent_id": root["parent_id"], "seq": root["seq"]} == {"parent_id": None, "seq": 1}
    assert {"parent_id": child["parent_id"], "seq": child["seq"]} == {"parent_id": "root", "seq": 3}
    assert record["seq"] == 4
    for timestamp in (root["timestamp"], child["timestamp"], record["timestamp"]):
        assert isinstance(timestamp, int) and timestamp >= 0, "timestamps must be Unix ms"
    assert [[item["kind"], item["seq"]] for item in await session.get_log()] == [
        ["entry", 1],
        ["lane", 2],
        ["entry", 3],
        ["record", 4],
        ["fact", 5],
        ["fact", 6],
        ["lane", 7],
    ]
    assert await session.get_lanes() == [
        {"lane": "main", "leaf_id": "child"},
        {"lane": "thread", "leaf_id": "child"},
    ]


async def _rejects_duplicate_ids(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    await session.append_entry(
        {"type": "message", "id": "shared", "message": create_user_message("root")}, "main"
    )
    await _expected_code(
        session.append_record(operation_started("shared", "main", "run")), "already_exists"
    )
    await session.append_record(operation_started("run", "main", "run"))
    await _expected_code(
        session.append_entry({"type": "custom", "id": "run", "custom_type": "note"}, "main"),
        "already_exists",
    )
    assert [item["seq"] for item in await session.get_log()] == [1, 2]


async def _isolates_lanes(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    await session.append_entry(
        {"type": "message", "id": "root", "message": create_user_message("root")}, "main"
    )
    await session.create_lane("thread", "root")
    await session.append_entry(
        {"type": "message", "id": "main-child", "message": create_user_message("main")}, "main"
    )
    await session.append_entry(
        {"type": "message", "id": "thread-child", "message": create_user_message("thread")}, "thread"
    )

    assert await session.get_lanes() == [
        {"lane": "main", "leaf_id": "main-child"},
        {"lane": "thread", "leaf_id": "thread-child"},
    ]
    assert await entry_ids(
        session.find_entries_on_branch(EntryQuery(order="oldestFirst"), BranchBounds(start="main-child"))
    ) == ["root", "main-child"]
    assert await entry_ids(
        session.find_entries_on_branch(
            EntryQuery(order="oldestFirst"), BranchBounds(start="thread-child")
        )
    ) == ["root", "thread-child"]


async def _validates_lane_lifecycle(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    await _expected_code(session.create_lane("main", None), "already_exists")
    await _expected_code(session.create_lane("thread", "missing"), "not_found")
    await _expected_code(session.move_lane("missing", None), "invalid_lane")


async def _binds_lane_views(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    root = await session.append_message(create_user_message("root"))
    await session.create_lane("thread", root)
    thread = session.view("thread")
    main_child, thread_child = await _gather(
        session.append_message(create_user_message("main")),
        thread.append_message(create_user_message("thread")),
    )

    assert await session.get_leaf_id() == main_child
    assert await thread.get_leaf_id() == thread_child
    assert await entry_ids(session.find_entries_on_branch(EntryQuery(order="oldestFirst"))) == [
        root,
        main_child,
    ]
    assert await entry_ids(thread.find_entries_on_branch(EntryQuery(order="oldestFirst"))) == [
        root,
        thread_child,
    ]
    empty = await repository.create(SessionCreateOptions(id="empty"))
    assert await empty.find_entries_on_branch() == []


async def _appends_provisioned_entries(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    entry = await session.append_entry(
        {"type": "custom", "id": "provisioned", "custom_type": "note", "data": {"value": 1}}, "main"
    )

    assert entry["custom_type"] == "note"
    assert {"id": entry["id"], "parent_id": entry["parent_id"], "seq": entry["seq"]} == {
        "id": "provisioned",
        "parent_id": None,
        "seq": 1,
    }
    assert await session.get_leaf_id() == "provisioned"


async def _persists_termination_decisions(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    entry = await session.append_entry(
        {
            "type": "message",
            "id": "tool-result",
            "message": {
                "role": "toolResult",
                "tool_call_id": "call-1",
                "tool_name": "example",
                "content": [{"type": "text", "text": "done"}],
                "is_error": False,
                "timestamp": 1,
            },
            "terminate": True,
        },
        "main",
    )

    assert entry["terminate"] is True
    stored = await session.get_entry(entry["id"])
    assert stored is not None and stored["type"] == "message"
    assert stored["terminate"] is True
    assert await session.find_entries() == [entry]
    assert await session.get_log() == [{"kind": "entry", "seq": entry["seq"], "entry": entry}]


async def _linearizes_concurrent_writes(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    await session.append_entry(
        {"type": "message", "id": "root", "message": create_user_message("root")}, "main"
    )
    await session.create_lane("thread", "root")
    completion_order: List[str] = []

    async def write(entry_id: str, lane: str) -> Any:
        entry = await session.append_entry(
            {"type": "custom", "id": entry_id, "custom_type": "note"}, lane
        )
        completion_order.append(entry["id"])
        return entry

    entries = await _gather(
        write("main-1", "main"),
        write("thread-1", "thread"),
        write("main-2", "main"),
        write("thread-2", "thread"),
    )
    commit_order = [entry["id"] for entry in sorted(entries, key=lambda item: item["seq"])]

    assert len({entry["seq"] for entry in entries}) == len(entries)
    assert completion_order == commit_order
    concurrent_ids = {entry["id"] for entry in entries}
    assert [
        item["entry"]["id"]
        for item in await session.get_log()
        if item["kind"] == "entry" and item["entry"]["id"] in concurrent_ids
    ] == commit_order
    sequences = [item["seq"] for item in await session.get_log()]
    assert sequences == sorted(sequences)


# ---------------------------------------------------------------------------
# records and log
# ---------------------------------------------------------------------------


async def _commits_records_and_lane_moves(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    root = await session.append_entry(
        {"type": "message", "id": "root", "message": create_user_message("root")}, "main"
    )
    finished = await session.append_record(
        {
            "type": "operation_finished",
            "id": "finish",
            "lane": "main",
            "run_id": "run",
            "outcome": "completed",
        }
    )

    assert finished["seq"] == 2
    assert await session.get_lanes() == [{"lane": "main", "leaf_id": "root"}]
    await session.move_lane("main", None)
    assert await session.get_lanes() == [{"lane": "main", "leaf_id": None}]
    assert await session.get_log() == [
        {"kind": "entry", "seq": 1, "entry": root},
        {"kind": "record", "seq": 2, "record": finished},
        {"kind": "lane", "seq": 3, "lane": "main", "leaf_id": None},
    ]

    await _expected_code(session.move_lane("main", "missing"), "not_found")
    assert len(await session.find_records()) == 1
    assert [item["seq"] for item in await session.get_log()] == [1, 2, 3]


async def _keeps_lane_names_permanent(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    await session.create_lane("thread", None)
    await session.append_record(operation_started("old-run", "thread", "run"))
    await session.append_record(
        {
            "type": "queue_enqueued",
            "id": "old-next-run",
            "lane": "thread",
            "queue": "nextRun",
            "target": {"type": "message", "id": "queued-message", "message": create_user_message("queued")},
        }
    )

    assert [record["id"] for record in await session.find_records(RecordQuery(lane="thread"))] == [
        "old-next-run",
        "old-run",
    ]
    assert [
        item["record"]["id"] for item in await session.get_log() if item["kind"] == "record"
    ] == ["old-run", "old-next-run"]
    await _expected_code(session.create_lane("thread", None), "already_exists")


async def _persists_queue_cancellation(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    enqueued = await session.append_record(
        {
            "type": "queue_enqueued",
            "id": "enqueue",
            "lane": "main",
            "queue": "nextRun",
            "target": {"type": "message", "id": "queued-message", "message": create_user_message("queued")},
        }
    )
    cancelled = await session.append_record(
        {"type": "queue_cancelled", "id": "cancel", "lane": "main", "entry_id": "queued-message"}
    )
    assert {"seq": cancelled["seq"], "entry_id": cancelled["entry_id"]} == {
        "seq": 2,
        "entry_id": "queued-message",
    }
    assert "run_id" not in cancelled
    assert await session.get_entry("queued-message") is None
    cancellations = await session.find_records(RecordQuery(type="queue_cancelled"))
    assert cancellations[0]["entry_id"] == "queued-message"
    assert cancellations == [cancelled]
    assert await session.get_log() == [
        {"kind": "record", "seq": enqueued["seq"], "record": enqueued},
        {"kind": "record", "seq": cancelled["seq"], "record": cancelled},
    ]


async def _filters_records(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    await session.append_record(operation_started("run-1", "main", "run"))
    await session.append_record(
        {
            "type": "step_attempt",
            "id": "attempt-1",
            "lane": "main",
            "run_id": "run-1",
            "step": "assistant",
            "attempt": 1,
            "result_entry_id": "assistant-1",
        }
    )
    await session.create_lane("thread", None)
    await session.append_record(operation_started("run-2", "thread", "run"))
    await session.append_record(
        {
            "type": "step_attempt",
            "id": "attempt-2",
            "lane": "thread",
            "run_id": "run-2",
            "step": "assistant",
            "attempt": 1,
            "result_entry_id": "assistant-2",
        }
    )

    assert [record["id"] for record in await session.find_records(RecordQuery(lane="thread"))] == [
        "attempt-2",
        "run-2",
    ]
    assert [
        record["id"]
        for record in await session.find_records(
            RecordQuery(type="step_attempt", order="oldestFirst")
        )
    ] == ["attempt-1", "attempt-2"]
    assert [
        record["id"]
        for record in await session.find_records(RecordQuery(run_id="run-1", after_seq=1))
    ] == ["attempt-1"]
    assert [record["id"] for record in await session.find_records(RecordQuery(limit=1))] == [
        "attempt-2"
    ]


async def _filters_operation_kinds(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    for record_id, kind in [
        ("run-old", "run"),
        ("compaction", "compaction"),
        ("navigation", "navigation"),
        ("run-new", "run"),
    ]:
        await session.append_record(operation_started(record_id, "main", kind))
        await session.append_record(
            {
                "type": "operation_finished",
                "id": f"{record_id}-finished",
                "lane": "main",
                "run_id": record_id,
                "outcome": "completed",
            }
        )

    assert [
        record["id"]
        for record in await session.find_records(
            RecordQuery(type="operation_started", operation_kind="run", order="oldestFirst")
        )
    ] == ["run-old", "run-new"]
    assert [
        record["id"]
        for record in await session.find_records(
            RecordQuery(type="operation_started", operation_kind="compaction")
        )
    ] == ["compaction"]
    assert [
        record["id"]
        for record in await session.find_records(
            RecordQuery(type="operation_started", operation_kind="navigation")
        )
    ] == ["navigation"]
    assert [
        record["id"]
        for record in await session.find_records(
            RecordQuery(type="operation_started", operation_kind="run", limit=1)
        )
    ] == ["run-new"]


async def _enforces_one_open_operation(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    assert await session.find_open_operations("main", limit=2) == []

    first = await session.append_record(operation_started("first", "main", "run"))
    assert await session.find_open_operations("main", limit=2) == [first]
    await _expected_code(
        session.append_record(operation_started("second", "main", "run")), "storage"
    )
    assert await session.find_open_operations("main", limit=2) == [first]

    await session.append_record(
        {
            "type": "operation_finished",
            "id": "finish-first",
            "lane": "main",
            "run_id": first["id"],
            "outcome": "completed",
        }
    )
    assert await session.find_open_operations("main", limit=2) == []


async def _earlier_finish_does_not_close_later_start(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    await session.append_record(
        {
            "type": "operation_finished",
            "id": "finish-before-start",
            "lane": "main",
            "run_id": "run",
            "outcome": "completed",
        }
    )
    started = await session.append_record(operation_started("run", "main", "run"))
    assert await session.find_open_operations("main", limit=2) == [started]


async def _scopes_open_operations(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    await session.create_lane("thread", None)
    main_run = await session.append_record(operation_started("main-run", "main", "run"))
    thread_navigation = await session.append_record(
        operation_started("thread-navigation", "thread", "navigation")
    )

    assert await session.find_open_operations("main") == [main_run]
    assert await session.find_open_operations("main", limit=1) == [main_run]
    assert await session.find_open_operations("thread", limit=2) == [thread_navigation]


async def _returns_immutable_open_operations(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    committed = await session.append_record(operation_started("run", "main", "run"))
    (read,) = await session.find_open_operations("main")
    assert read["intent"]["kind"] == "run"
    read["intent"]["original_prompt"].append(create_user_message("mutated"))

    assert await session.find_open_operations("main") == [committed]


# ---------------------------------------------------------------------------
# queries and facts
# ---------------------------------------------------------------------------


async def _rejects_invalid_queries(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="invalid-queries"))
    await session.create_lane("thread", None)
    thread = session.view("thread")

    await _expected_code(session.find_entries(EntryQuery(limit=0)), "invalid_query")
    await _expected_code(session.find_entry(EntryQuery(limit=0)), "invalid_query")
    await _expected_code(
        session.find_entries_on_branch(EntryQuery(limit=0)), "invalid_query"
    )
    await _expected_code(
        thread.find_entries_on_branch(EntryQuery(cursor=_cursor(-1))), "invalid_query"
    )
    await _expected_code(
        thread.find_entry_on_branch(EntryQuery(limit=0)), "invalid_query"
    )
    await _expected_code(session.find_records(RecordQuery(limit=0)), "invalid_query")
    await _expected_code(
        session.find_records(RecordQuery(operation_kind="run")), "invalid_query"
    )
    await _expected_code(
        session.find_records(RecordQuery(type="step_attempt", operation_kind="run")),
        "invalid_query",
    )
    await _expected_code(session.find_open_operations("main", limit=0), "invalid_query")
    await _expected_code(session.find_open_operations("main", limit=-1), "invalid_query")
    await _expected_code(session.get_log(LogOptions(after_seq=-1)), "invalid_query")


async def _supports_bounded_queries(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    await session.append_entry(
        {"type": "message", "id": "root", "message": create_user_message("root")}, "main"
    )
    await session.append_entry({"type": "custom", "id": "old-note", "custom_type": "note", "data": 1}, "main")
    await session.append_entry(
        {"type": "compaction", "id": "compact", "summary": "summary", "retained_tail": [], "tokens_before": 10},
        "main",
    )
    await session.append_entry({"type": "custom", "id": "new-note", "custom_type": "note", "data": 2}, "main")
    await session.append_entry(
        {"type": "message", "id": "tail", "message": create_assistant_message("tail")}, "main"
    )

    assert await entry_ids(session.find_entries()) == [
        "tail",
        "new-note",
        "compact",
        "old-note",
        "root",
    ]
    assert await entry_ids(
        session.find_entries(EntryQuery(order="oldestFirst", cursor=_cursor(2), limit=2))
    ) == ["compact", "new-note"]
    assert await entry_ids(session.find_entries(EntryQuery(custom_type="note"))) == [
        "new-note",
        "old-note",
    ]
    assert await entry_ids(
        session.find_entries_on_branch(
            EntryQuery(custom_type="note", limit=1), BranchBounds(start="tail")
        )
    ) == ["new-note"]
    assert await entry_ids(
        session.find_entries_on_branch(
            EntryQuery(type="message"), BranchBounds(start="tail", stop_at_type="compaction")
        )
    ) == ["tail"]
    assert await entry_ids(
        session.find_entries_on_branch(
            EntryQuery(type="custom"), BranchBounds(start="tail", stop_at_id="tail")
        )
    ) == []
    assert await entry_ids(
        session.find_entries_on_branch(
            EntryQuery(order="oldestFirst"), BranchBounds(start="tail", stop_at_type="custom")
        )
    ) == ["root", "old-note"]
    await _expected_code(session.find_entries(EntryQuery(limit=0)), "invalid_query")
    await _expected_code(
        session.find_entries_on_branch(bounds=BranchBounds(start="missing")), "not_found"
    )


async def _computes_ledger_statistics(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    assistant = create_assistant_message("answer")
    assistant["usage"] = {
        "input": 10,
        "output": 5,
        "cache_read": 3,
        "cache_write": 2,
        "total_tokens": 20,
        "cost": {"input": 1, "output": 2, "cache_read": 3, "cache_write": 4, "total": 10},
    }
    await session.append_entry(
        {"type": "message", "id": "user", "message": create_user_message("question")}, "main"
    )
    await session.append_entry({"type": "message", "id": "assistant", "message": assistant}, "main")
    await session.append_record(
        {
            "type": "usage",
            "id": "assistant-usage",
            "lane": "main",
            "cause": "assistant",
            "run_id": "run",
            "entry_id": "assistant",
            "attempt": 1,
            "stop_reason": "stop",
            "usage": assistant["usage"],
        }
    )
    await session.append_record(
        {
            "type": "usage",
            "id": "deferred-usage",
            "lane": "main",
            "cause": "deferred_fetch",
            "run_id": "run",
            "entry_id": "deferred-result",
            "attempt": 1,
            "stop_reason": "deferred",
            "usage": {
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "total_tokens": 0,
                "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
            },
        }
    )
    await session.create_lane("thread", "assistant")
    await session.append_record(
        {
            "type": "usage",
            "id": "correction",
            "lane": "thread",
            "cause": "adjustment",
            "details": {"reason": "provider correction"},
            "usage": {
                "input": -2,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "total_tokens": -2,
                "cost": {"input": -0.5, "output": 0, "cache_read": 0, "cache_write": 0, "total": -0.5},
            },
        }
    )
    await session.set_name("First")
    await session.set_name("Second")
    await session.set_label("user", "keep")
    await session.set_label("user", None)
    await _expected_code(session.set_label("missing", "checkpoint"), "not_found")

    assert await session.get_name() == "Second"
    assert await session.get_label("user") is None
    usage_records = await session.find_records(RecordQuery(type="usage", order="oldestFirst"))
    assert [record["cause"] for record in usage_records] == [
        "assistant",
        "deferred_fetch",
        "adjustment",
    ]
    deferred_usage = next(record for record in usage_records if record["cause"] == "deferred_fetch")
    assert deferred_usage["stop_reason"] == "deferred"
    stats = await session.get_stats()
    assert _stats_dict(stats) == {
        "message_count": 2,
        "cached_tokens": 3,
        "uncached_tokens": 10,
        "total_tokens": 18,
        "cost_total": 9.5,
    }


async def _clears_session_names(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    await session.set_name("Temporary")
    await session.set_name(None)

    assert await session.get_name() is None
    assert await session.get_log() == [
        {"kind": "fact", "seq": 1, "fact": "name", "name": "Temporary"},
        {"kind": "fact", "seq": 2, "fact": "name", "name": None},
    ]

    metadata = await session.get_metadata()
    reopened = await repository.open(metadata)
    assert await reopened.get_name() is None
    assert await reopened.get_log() == [
        {"kind": "fact", "seq": 1, "fact": "name", "name": "Temporary"},
        {"kind": "fact", "seq": 2, "fact": "name", "name": None},
    ]

    fork = await repository.fork(metadata, ForkOptions(id="fork"))
    assert await fork.get_name() is None


# ---------------------------------------------------------------------------
# validation and immutability
# ---------------------------------------------------------------------------


async def _returns_immutable_copies(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="immutable"))
    metadata = await session.get_metadata()
    data = {"nested": {"value": 1}}
    await session.append_entry({"type": "custom", "id": "custom", "custom_type": "note", "data": data}, "main")
    data["nested"]["value"] = 50
    read = await session.get_entry("custom")
    assert read is not None and read["type"] == "custom"
    read["data"]["nested"]["value"] = 99
    read_metadata = await session.get_metadata()
    read_metadata["id"] = "changed"
    log = await session.get_log()
    assert log[0]["kind"] == "entry"
    log[0]["entry"]["data"]["nested"]["value"] = 100

    assert await session.get_metadata() == metadata
    assert await session.get_entry("custom") == {
        "type": "custom",
        "id": "custom",
        "custom_type": "note",
        "data": {"nested": {"value": 1}},
        "parent_id": None,
        "seq": 1,
        "timestamp": read["timestamp"],
    }


async def _rejects_non_json_entries(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    cyclic: Dict[str, Any] = {}
    cyclic["self"] = cyclic

    for data in [
        {"value": {1, 2}},
        {"value": b"bytes"},
        {"value": datetime.now()},
        {"value": object()},
        {"value": float("nan")},
        {1: "non-string key"},
        cyclic,
    ]:
        await _expected_code(session.append_custom_entry("invalid", data), "invalid_payload")

    assert await session.get_leaf_id() is None
    assert await session.find_entries() == []
    assert await session.get_log() == []
    valid_id = await session.append_custom_entry("valid", {"value": 1})
    assert (await session.get_entry(valid_id))["seq"] == 1


async def _rejects_non_json_records(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="session"))
    for record_id, value in [
        ("set-record", {1, 2}),
        ("datetime-record", datetime.now()),
    ]:
        await _expected_code(
            session.append_record(
                {
                    "type": "tool_started",
                    "id": record_id,
                    "lane": "main",
                    "run_id": "run",
                    "assistant_entry_id": "assistant",
                    "tool_index": 0,
                    "tool_call_id": "call",
                    "tool_name": "example",
                    "effective_args": {"value": value},
                    "result_entry_id": "result",
                    "replay": "never",
                }
            ),
            "invalid_payload",
        )

    assert await session.find_records() == []
    assert await session.get_log() == []
    assert (await session.append_record(operation_started("valid-record", "main", "run")))[
        "seq"
    ] == 1


# ---------------------------------------------------------------------------
# repository and forks
# ---------------------------------------------------------------------------


async def _creates_lists_and_opens(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="one"))
    entry_id = await session.append_message(create_user_message("persisted"))
    metadata = await session.get_metadata()

    listed = await repository.list()
    assert len(listed) == 1
    assert listed[0]["id"] == metadata["id"]
    assert listed[0]["created_at"] == metadata["created_at"]
    assert listed[0].get("parent_session_id") == metadata.get("parent_session_id")
    assert await entry_ids((await repository.open(metadata)).find_entries()) == [entry_id]
    await _expected_code(repository.create(SessionCreateOptions(id="one")), "already_exists")


async def _deletes_idempotently(repository: Any) -> None:
    session = await repository.create(SessionCreateOptions(id="one"))
    metadata = await session.get_metadata()

    await repository.delete(metadata)
    await _expected_code(repository.open(metadata), "not_found")
    await repository.delete(metadata)


async def _forks_branch(repository: Any) -> None:
    source = await repository.create(SessionCreateOptions(id="source"))
    root = await source.append_message(create_user_message("root"))
    shared = await source.append_message(create_assistant_message("shared"))
    await source.create_lane("thread", shared)
    thread_child = await source.view("thread").append_message(create_user_message("thread"))
    main_child = await source.append_message(create_user_message("main"))
    await source.set_name("Source")
    await source.set_label(shared, "copied")
    await source.set_label(thread_child, "excluded")
    await source.append_record(operation_started("run", "main", "run"))
    await source.append_record(
        {
            "type": "usage",
            "id": "source-usage",
            "lane": "main",
            "cause": "adjustment",
            "usage": {
                "input": 10,
                "output": 5,
                "cache_read": 3,
                "cache_write": 2,
                "total_tokens": 20,
                "cost": {"input": 1, "output": 2, "cache_read": 3, "cache_write": 4, "total": 10},
            },
        }
    )

    fork = await repository.fork(
        await source.get_metadata(),
        ForkOptions(scope="branch", entry_id=main_child, position="at", id="branch-fork"),
    )

    assert await entry_ids(fork.find_entries(EntryQuery(order="oldestFirst"))) == [
        root,
        shared,
        main_child,
    ]
    assert await fork.get_lanes() == [{"lane": "main", "leaf_id": main_child}]
    assert await fork.get_name() == "Source"
    assert await fork.get_label(shared) == "copied"
    assert await fork.get_label(thread_child) is None
    assert await fork.find_records() == []
    assert _stats_dict(await fork.get_stats()) == {
        "message_count": 3,
        "cached_tokens": 0,
        "uncached_tokens": 0,
        "total_tokens": 0,
        "cost_total": 0,
    }
    await fork.append_message(create_user_message("after fork"))
    assert (await fork.get_stats()).message_count == 4
    metadata = await fork.get_metadata()
    assert {"id": metadata["id"], "parent_session_id": metadata["parent_session_id"]} == {
        "id": "branch-fork",
        "parent_session_id": "source",
    }


async def _forks_tree(repository: Any) -> None:
    source = await repository.create(SessionCreateOptions(id="source"))
    root = await source.append_message(create_user_message("root"))
    await source.create_lane("thread", root)
    main_child = await source.append_message(create_user_message("main"))
    thread_child = await source.view("thread").append_message(create_user_message("thread"))
    await source.set_label(thread_child, "thread-tip")

    fork = await repository.fork(await source.get_metadata(), ForkOptions(scope="tree", id="tree-fork"))
    assert await entry_ids(fork.find_entries(EntryQuery(order="oldestFirst"))) == [
        root,
        main_child,
        thread_child,
    ]
    assert await fork.get_lanes() == [
        {"lane": "main", "leaf_id": main_child},
        {"lane": "thread", "leaf_id": thread_child},
    ]
    assert await fork.get_label(thread_child) == "thread-tip"
    assert (await fork.get_stats()).message_count == 3
    assert [
        item for item in await fork.get_log() if item["kind"] == "lane"
    ] == [
        {"kind": "lane", "seq": 4, "lane": "main", "leaf_id": main_child},
        {"kind": "lane", "seq": 5, "lane": "thread", "leaf_id": thread_child},
    ]


async def _forks_before_entry(repository: Any) -> None:
    source = await repository.create(SessionCreateOptions(id="source"))
    root = await source.append_message(create_user_message("root"))
    tail = await source.append_message(create_user_message("tail"))
    fork = await repository.fork(await source.get_metadata(), ForkOptions(entry_id=tail, id="fork"))

    assert await entry_ids(fork.find_entries(EntryQuery(order="oldestFirst"))) == [root]
    assert await fork.get_leaf_id() == root
    assert await source.get_leaf_id() == tail
    before_default_target = await repository.fork(
        await source.get_metadata(), ForkOptions(position="before", id="before-default-target")
    )
    assert await entry_ids(before_default_target.find_entries(EntryQuery(order="oldestFirst"))) == [root]
    assert await before_default_target.get_leaf_id() == root

    at_default_target = await repository.fork(
        await source.get_metadata(), ForkOptions(position="at", id="at-default-target")
    )
    assert await entry_ids(at_default_target.find_entries(EntryQuery(order="oldestFirst"))) == [
        root,
        tail,
    ]
    assert await at_default_target.get_leaf_id() == tail
    await _expected_code(
        repository.fork(await source.get_metadata(), ForkOptions(entry_id="missing")),
        "invalid_fork_target",
    )


async def _validates_default_fork_target(repository: Any) -> None:
    source = await repository.create(SessionCreateOptions(id="source-with-custom-leaf"))
    await source.append_custom_entry("not-a-message")

    await _expected_code(
        repository.fork(await source.get_metadata(), ForkOptions(id="fork")),
        "invalid_fork_target",
    )


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _cursor(after_seq: int) -> EntryCursor:
    return EntryCursor(after_seq=after_seq)


def _stats_dict(stats: Any) -> Dict[str, Any]:
    return asdict(stats) if not isinstance(stats, dict) else dict(stats)


async def _gather(*awaitables: Any) -> Any:
    return await asyncio.gather(*awaitables)
