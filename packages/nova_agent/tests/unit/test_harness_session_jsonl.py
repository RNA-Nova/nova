"""JSONL 持久化行为测试（对齐 pi ``jsonl.test.ts`` / ``jsonl-storage.test.ts``）。

覆盖 conformance 之外的 JSONL 特有行为：落盘行格式、撕裂尾修复、header 嗅探
list、id 校验、create/fork 互斥与故障注入、重放校验矩阵。
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest
from nova_agent.harness.session.jsonl import (
    JsonlForkOptions,
    JsonlSessionRepo,
    LocalJsonlFileSystem,
)
from nova_agent.harness.session.types import SessionError


def _repo(root: str) -> JsonlSessionRepo:
    return JsonlSessionRepo({"fs": LocalJsonlFileSystem(), "sessions_root": root})


class _FlakyFs(LocalJsonlFileSystem):
    """按方法注入第 N 次失败的文件系统（vi.spyOn 对位）。"""

    def __init__(self) -> None:
        self._pending: Dict[str, int] = {"write_file": 0, "append_file": 0, "rename_file": 0}

    def fail_next(self, method: str) -> None:
        self._pending[method] += 1

    def _gate(self, name: str) -> None:
        if self._pending.get(name, 0) > 0:
            self._pending[name] -= 1
            raise OSError("injected failure")

    async def write_file(self, path: str, content: str) -> None:
        self._gate("write_file")
        return await super().write_file(path, content)

    async def append_file(self, path: str, content: str) -> None:
        self._gate("append_file")
        return await super().append_file(path, content)

    async def rename_file(self, src: str, dst: str) -> None:
        self._gate("rename_file")
        return await super().rename_file(src, dst)


def _user_message(text: str) -> Dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}], "timestamp": 1}


def _read_lines(path: str) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text("utf-8").rstrip("\n").split("\n")]


def _write_raw_session(root: str, name: str, mutations: List[Dict[str, Any]]) -> Dict[str, Any]:
    path = Path(root) / f"session-{name}.jsonl"
    header = {"kind": "header", "version": 4, "id": name, "created_at": 1, "cwd": root}
    content = json.dumps(header) + "\n" + "".join(json.dumps(m) + "\n" for m in mutations)
    path.write_text(content, encoding="utf-8")
    stat = path.stat()
    return {
        "id": name,
        "created_at": 1,
        "path": str(path),
        "cwd": root,
        "modified_at": stat.st_mtime * 1000,
        "source_format": 4,
    }


# ---------------------------------------------------------------------------
# 元数据 / list
# ---------------------------------------------------------------------------


async def test_metadata_contract(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    cwd = str(tmp_path / "workspace" / "project")
    session = await repository.create(
        {"id": "metadata", "cwd": cwd, "parent_session_id": "parent", "metadata": {"owner": "agent"}}
    )
    metadata = await session.get_metadata()

    assert metadata["id"] == "metadata"
    assert metadata["parent_session_id"] == "parent"
    assert metadata["cwd"] == cwd
    assert metadata["source_format"] == 4
    assert metadata["metadata"] == {"owner": "agent"}
    assert metadata["path"].endswith(".jsonl") and metadata["id"] in metadata["path"]
    # 目录编码 = 绝对 cwd 剥前导斜杠后路径分隔符转 dash（对齐 TS 编码规则）
    expected_dir = "--" + cwd[1:].replace("/", "-") + "--"
    assert metadata["path"].startswith(str(Path(root) / expected_dir))

    listed = await repository.list({"cwd": cwd})
    assert len(listed) == 1 and listed[0]["id"] == "metadata"
    assert await repository.list({"cwd": str(tmp_path / "other")}) == []


async def test_malformed_header_rejects_open_and_skips_list(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    await repository.create({"id": "valid", "cwd": root})
    session = await repository.create({"id": "bad", "cwd": root})
    metadata = await session.get_metadata()
    Path(metadata["path"]).write_text("not json\n", encoding="utf-8")

    with pytest.raises(SessionError) as exc_info:
        await repository.open(metadata)
    assert exc_info.value.code == "invalid_entry"
    assert [m["id"] for m in await repository.list({"cwd": root})] == ["valid"]
    assert Path(metadata["path"]).read_text("utf-8") == "not json\n"


async def test_non_object_header_metadata_rejected(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    await repository.create({"id": "valid", "cwd": root})
    session = await repository.create({"id": "bad-meta", "cwd": root})
    metadata = await session.get_metadata()
    broken = {
        "kind": "header",
        "version": 4,
        "id": metadata["id"],
        "created_at": metadata["created_at"],
        "cwd": metadata["cwd"],
        "metadata": "invalid",
    }
    Path(metadata["path"]).write_text(json.dumps(broken) + "\n", encoding="utf-8")

    with pytest.raises(SessionError) as exc_info:
        await repository.open(metadata)
    assert exc_info.value.code == "invalid_entry"
    assert [m["id"] for m in await repository.list({"cwd": root})] == ["valid"]


async def test_rejects_session_ids_unsafe_for_filenames(tmp_path: Path) -> None:
    repository = _repo(str(tmp_path))
    with pytest.raises(SessionError) as exc_info:
        await repository.create({"id": "../escape", "cwd": str(tmp_path)})
    assert exc_info.value.code == "invalid_payload"


async def test_same_explicit_id_in_different_cwds(tmp_path: Path) -> None:
    repository = _repo(str(tmp_path))
    first_cwd = str(tmp_path / "workspaces" / "first")
    second_cwd = str(tmp_path / "workspaces" / "second")

    first = await repository.create({"id": "shared", "cwd": first_cwd})
    second = await repository.create({"id": "shared", "cwd": second_cwd})

    assert (await first.get_metadata())["cwd"] == first_cwd
    assert (await second.get_metadata())["cwd"] == second_cwd
    assert len(await repository.list()) == 2


async def test_list_sorted_by_modification_time(tmp_path: Path) -> None:
    repository = _repo(str(tmp_path))
    newest_cwd = str(tmp_path / "workspaces" / "newest")
    oldest_cwd = str(tmp_path / "workspaces" / "oldest")
    newest = await (repository.create({"id": "newest", "cwd": newest_cwd}))
    newest_metadata = await newest.get_metadata()
    oldest = await repository.create({"id": "oldest", "cwd": oldest_cwd})
    oldest_metadata = await oldest.get_metadata()
    os.utime(newest_metadata["path"], (1_700_000_002, 1_700_000_002))
    os.utime(oldest_metadata["path"], (1_700_000_001, 1_700_000_001))

    listed = await repository.list()
    assert [m["id"] for m in listed] == ["newest", "oldest"]
    assert [m["id"] for m in await repository.list({"cwd": newest_cwd})] == ["newest"]


# ---------------------------------------------------------------------------
# create/fork 互斥与故障注入
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first_kind", "second_kind"),
    [("create", "create"), ("create", "fork"), ("fork", "fork")],
)
async def test_concurrent_same_destination_single_winner(
    tmp_path: Path, first_kind: str, second_kind: str
) -> None:
    repository = _repo(str(tmp_path))
    cwd = str(tmp_path / "workspace")
    source = await repository.create({"id": "source", "cwd": cwd})
    source_metadata = await source.get_metadata()

    def run(kind: str) -> Any:
        if kind == "create":
            return repository.create({"id": "same", "cwd": cwd})
        return repository.fork(source_metadata, JsonlForkOptions(id="same", cwd=cwd))

    results = await asyncio.gather(run(first_kind), run(second_kind), return_exceptions=True)
    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], SessionError) and failures[0].code == "already_exists"
    listed = [m for m in await repository.list({"cwd": cwd}) if m["id"] == "same"]
    assert len(listed) == 1


@pytest.mark.parametrize("kind", ["create", "fork"])
async def test_destination_reservation_released_after_failure(tmp_path: Path, kind: str) -> None:
    root = str(tmp_path)
    fs = _FlakyFs()
    repository = JsonlSessionRepo({"fs": fs, "sessions_root": root})
    cwd = str(tmp_path / "workspace")
    source = await repository.create({"id": "source", "cwd": cwd})
    source_metadata = await source.get_metadata()

    async def run() -> Any:
        if kind == "create":
            return await repository.create({"id": "retry", "cwd": cwd})
        return await repository.fork(source_metadata, JsonlForkOptions(id="retry", cwd=cwd))

    fs.fail_next("write_file" if kind == "create" else "rename_file")
    with pytest.raises(SessionError) as exc_info:
        await run()
    assert exc_info.value.code == "storage"

    retry = await run()
    assert retry is not None
    listed = [m for m in await repository.list({"cwd": cwd}) if m["id"] == "retry"]
    assert len(listed) == 1


async def test_no_partial_fork_when_staging_fails(tmp_path: Path) -> None:
    root = str(tmp_path)
    fs = _FlakyFs()
    repository = JsonlSessionRepo({"fs": fs, "sessions_root": root})
    source = await repository.create({"id": "source", "cwd": root})
    await source.append_message(_user_message("one"))
    await source.append_message(_user_message("two"))
    source_metadata = await source.get_metadata()

    fs.fail_next("append_file")  # staging 阶段第 1 次 mutation 追加失败
    with pytest.raises(SessionError) as exc_info:
        await repository.fork(source_metadata, JsonlForkOptions(id="fork", cwd=root))
    assert exc_info.value.code == "storage"

    assert [m["id"] for m in await repository.list()] == ["source"]
    assert not list(Path(root).rglob("*.tmp"))


async def test_no_fork_when_rename_fails(tmp_path: Path) -> None:
    root = str(tmp_path)
    fs = _FlakyFs()
    repository = JsonlSessionRepo({"fs": fs, "sessions_root": root})
    source = await repository.create({"id": "source", "cwd": root})
    await source.append_message(_user_message("one"))
    source_metadata = await source.get_metadata()

    fs.fail_next("rename_file")
    with pytest.raises(SessionError) as exc_info:
        await repository.fork(source_metadata, JsonlForkOptions(id="fork", cwd=root))
    assert exc_info.value.code == "storage"

    assert [m["id"] for m in await repository.list()] == ["source"]
    assert not list(Path(root).rglob("*.tmp"))


# ---------------------------------------------------------------------------
# 落盘行格式与重放
# ---------------------------------------------------------------------------


async def test_one_line_per_mutation_restores_shared_sequence(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    session = await repository.create({"id": "session", "cwd": root})
    metadata = await session.get_metadata()
    entry_id = await session.append_custom_entry("note", {"value": 1})
    await session.create_lane("thread", entry_id)
    await session.append_record(
        {
            "type": "operation_started",
            "id": "run",
            "lane": "thread",
            "source_leaf_id": None,
            "intent": {"kind": "run", "original_prompt": [], "initial_messages": []},
        }
    )
    await session.set_name("Example")
    await session.set_label(entry_id, "checkpoint")
    await session.move_lane("main", None)

    lines = _read_lines(metadata["path"])
    assert [line["kind"] for line in lines] == [
        "header", "entry", "lane", "record", "fact", "fact", "lane",
    ]
    assert [line["seq"] for line in lines[1:]] == [1, 2, 3, 4, 5, 6]

    reopened = await _repo(root).open(metadata)
    assert await reopened.get_lanes() == [
        {"lane": "main", "leaf_id": None},
        {"lane": "thread", "leaf_id": entry_id},
    ]
    assert await reopened.get_name() == "Example"
    assert await reopened.get_label(entry_id) == "checkpoint"
    assert [r["id"] for r in await reopened.find_records()] == ["run"]
    assert [
        r["id"]
        for r in await reopened.find_records(
            _rq(type="operation_started", operation_kind="run")
        )
    ] == ["run"]
    assert [r["id"] for r in await reopened.find_open_operations("thread", limit=2)] == ["run"]
    assert [item["seq"] for item in await reopened.get_log()] == [1, 2, 3, 4, 5, 6]

    finished = await reopened.append_record(
        {
            "type": "operation_finished",
            "id": "finish",
            "lane": "thread",
            "run_id": "run",
            "outcome": "completed",
        }
    )
    assert finished["seq"] == 7
    assert await reopened.find_open_operations("thread", limit=2) == []


def _rq(**kwargs: Any) -> Any:
    from nova_agent.harness.session.types import RecordQuery

    return RecordQuery(**kwargs)


async def test_fork_message_counts_recomputed_on_reopen(tmp_path: Path) -> None:
    repository = _repo(str(tmp_path))
    source = await repository.create({"id": "source", "cwd": str(tmp_path)})
    await source.append_message(_user_message("one"))
    await source.append_message(_user_message("two"))
    fork = await repository.fork(await source.get_metadata(), JsonlForkOptions(id="fork", cwd=str(tmp_path)))
    metadata = await fork.get_metadata()

    reopened = await _repo(str(tmp_path)).open(metadata)
    assert (await reopened.get_stats()).message_count == 2
    await reopened.append_message(_user_message("three"))
    assert (await reopened.get_stats()).message_count == 3

    verified = await _repo(str(tmp_path)).open(metadata)
    assert (await verified.get_stats()).message_count == 3


async def test_tree_fork_reopens_with_lanes_and_facts(tmp_path: Path) -> None:
    repository = _repo(str(tmp_path))
    source = await repository.create({"id": "source", "cwd": str(tmp_path)})
    root_id = await source.append_custom_entry("root")
    await source.create_lane("thread", root_id)
    main_id = await source.append_custom_entry("main")
    await source.append_entry(
        {"type": "custom", "id": "thread", "custom_type": "thread"}, "thread"
    )
    await source.set_name("Source")
    await source.set_label("thread", "tip")
    fork = await repository.fork(
        await source.get_metadata(), JsonlForkOptions(scope="tree", id="fork", cwd=str(tmp_path))
    )
    metadata = await fork.get_metadata()

    imported_entry_lines = [line for line in _read_lines(metadata["path"]) if line["kind"] == "entry"]
    assert ["lane" in line for line in imported_entry_lines] == [False, False, False]

    reopened = await _repo(str(tmp_path)).open(metadata)
    assert [e["id"] for e in await reopened.find_entries(_eq(order="oldestFirst"))] == [
        root_id,
        main_id,
        "thread",
    ]
    assert await reopened.get_lanes() == [
        {"lane": "main", "leaf_id": main_id},
        {"lane": "thread", "leaf_id": "thread"},
    ]
    assert await reopened.get_name() == "Source"
    assert await reopened.get_label("thread") == "tip"
    assert await reopened.find_records() == []


def _eq(**kwargs: Any) -> Any:
    from nova_agent.harness.session.types import EntryQuery

    return EntryQuery(**kwargs)


def _bb(**kwargs: Any) -> Any:
    from nova_agent.harness.session.types import BranchBounds

    return BranchBounds(**kwargs)


def _cursor(after_seq: int) -> Any:
    from nova_agent.harness.session.types import EntryCursor

    return EntryCursor(after_seq=after_seq)


# ---------------------------------------------------------------------------
# 尾部修复与损坏矩阵
# ---------------------------------------------------------------------------


async def test_repairs_valid_final_line_missing_newline(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    session = await repository.create({"id": "session", "cwd": root})
    metadata = await session.get_metadata()
    first_id = await session.append_custom_entry("first")
    path = Path(metadata["path"])
    unterminated = path.read_text("utf-8").rstrip("\n")
    path.write_text(unterminated, encoding="utf-8")

    reopened = await _repo(root).open(metadata)
    assert path.read_text("utf-8") == f"{unterminated}\n"
    second_id = await reopened.append_custom_entry("second")

    verified = await _repo(root).open(metadata)
    assert [e["id"] for e in await verified.find_entries(_eq(order="oldestFirst"))] == [
        first_id,
        second_id,
    ]


async def test_fails_open_when_repairing_newline_fails(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    session = await repository.create({"id": "session", "cwd": root})
    metadata = await session.get_metadata()
    await session.append_custom_entry("first")
    path = Path(metadata["path"])
    path.write_text(path.read_text("utf-8").rstrip("\n"), encoding="utf-8")

    fs = _FlakyFs()
    fs.fail_next("append_file")
    failing = JsonlSessionRepo({"fs": fs, "sessions_root": root})
    with pytest.raises(SessionError) as exc_info:
        await failing.open(metadata)
    assert exc_info.value.code == "storage"


async def test_truncates_malformed_final_line(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    session = await repository.create({"id": "session", "cwd": root})
    metadata = await session.get_metadata()
    await session.append_custom_entry("note", {"value": "kept"})
    path = Path(metadata["path"])
    valid_prefix = path.read_text("utf-8")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"kind":"entry"')

    reopened = await _repo(root).open(metadata)
    assert len(await reopened.find_entries()) == 1
    assert path.read_text("utf-8") == valid_prefix
    appended_id = await reopened.append_custom_entry("after-recovery")
    assert (await reopened.get_entry(appended_id))["seq"] == 2


async def test_preserves_session_when_torn_tail_repair_fails(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    session = await repository.create({"id": "repair-failure", "cwd": root})
    metadata = await session.get_metadata()
    await session.append_custom_entry("kept")
    path = Path(metadata["path"])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"kind":"entry"')
    original = path.read_text("utf-8")

    fs = _FlakyFs()
    fs.fail_next("write_file")
    failing = JsonlSessionRepo({"fs": fs, "sessions_root": root})
    with pytest.raises(SessionError) as exc_info:
        await failing.open(metadata)
    assert exc_info.value.code == "storage"
    assert path.read_text("utf-8") == original
    assert not Path(f"{metadata['path']}.tmp").exists()


async def test_preserves_session_when_repair_rename_fails(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    session = await repository.create({"id": "repair-rename", "cwd": root})
    metadata = await session.get_metadata()
    await session.append_custom_entry("kept")
    path = Path(metadata["path"])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"kind":"entry"')
    original = path.read_text("utf-8")

    fs = _FlakyFs()
    fs.fail_next("rename_file")
    failing = JsonlSessionRepo({"fs": fs, "sessions_root": root})
    with pytest.raises(SessionError) as exc_info:
        await failing.open(metadata)
    assert exc_info.value.code == "storage"
    assert path.read_text("utf-8") == original
    assert not Path(f"{metadata['path']}.tmp").exists()


async def test_rejects_complete_invalid_final_mutation_without_modification(tmp_path: Path) -> None:
    root = str(tmp_path)
    metadata = _write_raw_session(root, "invalid-final", [{"kind": "unknown", "seq": 1}])
    corrupted = Path(metadata["path"]).read_text("utf-8")

    with pytest.raises(SessionError) as exc_info:
        await _repo(root).open(metadata)
    assert exc_info.value.code == "invalid_entry"
    assert Path(metadata["path"]).read_text("utf-8") == corrupted


async def test_rejects_malformed_middle_line_without_modification(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    session = await repository.create({"id": "session", "cwd": root})
    metadata = await session.get_metadata()
    await session.append_custom_entry("first")
    await session.append_custom_entry("second")
    path = Path(metadata["path"])
    lines = path.read_text("utf-8").rstrip("\n").split("\n")
    corrupted = f"{lines[0]}\n{lines[1]}\nnot-json\n{lines[2]}\n"
    path.write_text(corrupted, encoding="utf-8")

    with pytest.raises(SessionError) as exc_info:
        await _repo(root).open(metadata)
    assert exc_info.value.code == "invalid_entry"
    assert path.read_text("utf-8") == corrupted


async def test_rejects_imported_entry_missing_parent(tmp_path: Path) -> None:
    root = str(tmp_path)
    metadata = _write_raw_session(
        root,
        "missing-parent",
        [
            {
                "kind": "entry",
                "type": "custom",
                "id": "orphan",
                "custom_type": "note",
                "parent_id": "missing",
                "seq": 1,
                "timestamp": 1,
            }
        ],
    )
    with pytest.raises(SessionError) as exc_info:
        await _repo(root).open(metadata)
    assert exc_info.value.code == "invalid_entry"
    assert "references missing parent missing" in str(exc_info.value)


async def test_rejects_lane_bound_entry_not_chaining_to_leaf(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    session = await repository.create({"id": "session", "cwd": root})
    metadata = await session.get_metadata()
    await session.append_custom_entry("first")
    await session.append_custom_entry("second")

    path = Path(metadata["path"])
    lines = _read_lines(metadata["path"])
    lines[2]["parent_id"] = None
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    with pytest.raises(SessionError) as exc_info:
        await _repo(root).open(metadata)
    assert exc_info.value.code == "invalid_entry"
    assert "does not chain to the lane leaf" in str(exc_info.value)


async def test_imported_entry_without_lane_does_not_move_lane(tmp_path: Path) -> None:
    root = str(tmp_path)
    metadata = _write_raw_session(
        root,
        "import",
        [
            {
                "kind": "entry",
                "type": "custom",
                "id": "imported",
                "custom_type": "note",
                "parent_id": None,
                "seq": 1,
                "timestamp": 1,
            }
        ],
    )
    imported = await _repo(root).open(metadata)
    assert await imported.get_leaf_id() is None
    assert [e["id"] for e in await imported.find_entries()] == ["imported"]

    path = Path(metadata["path"])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"kind": "lane", "seq": 2, "lane": "main", "leaf_id": "imported"}) + "\n"
        )
    moved = await _repo(root).open(metadata)
    assert await moved.get_leaf_id() == "imported"


@pytest.mark.parametrize(
    ("message", "mutations"),
    [
        (
            "non-consecutive seq",
            [{"kind": "entry", "type": "custom", "id": "entry", "custom_type": "note", "parent_id": None, "seq": 2, "timestamp": 1}],
        ),
        (
            "duplicate id",
            [
                {"kind": "entry", "type": "custom", "id": "duplicate", "custom_type": "note", "parent_id": None, "seq": 1, "timestamp": 1},
                {"kind": "record", "type": "operation_started", "id": "duplicate", "lane": "main", "seq": 2, "timestamp": 2, "source_leaf_id": None, "intent": {"kind": "run", "original_prompt": [], "initial_messages": []}},
            ],
        ),
        (
            "references missing parent missing",
            [{"kind": "entry", "type": "custom", "id": "entry", "custom_type": "note", "parent_id": "missing", "seq": 1, "timestamp": 1}],
        ),
        (
            "references missing lane thread",
            [{"kind": "entry", "lane": "thread", "type": "custom", "id": "entry", "custom_type": "note", "parent_id": None, "seq": 1, "timestamp": 1}],
        ),
        (
            "references missing lane thread",
            [{"kind": "record", "type": "operation_started", "id": "run", "lane": "thread", "seq": 1, "timestamp": 1, "source_leaf_id": None, "intent": {"kind": "run", "original_prompt": [], "initial_messages": []}}],
        ),
        (
            "references missing lane target missing",
            [{"kind": "lane", "lane": "thread", "leaf_id": "missing", "seq": 1}],
        ),
        (
            "references missing label target missing",
            [{"kind": "fact", "fact": "label", "target_id": "missing", "label": "checkpoint", "seq": 1}],
        ),
    ],
)
async def test_rejects_corrupt_replay_matrices(
    tmp_path: Path, message: str, mutations: List[Dict[str, Any]]
) -> None:
    root = str(tmp_path)
    metadata = _write_raw_session(root, "corrupt", mutations)
    with pytest.raises(SessionError) as exc_info:
        await _repo(root).open(metadata)
    assert exc_info.value.code == "invalid_entry"
    assert message in str(exc_info.value)


async def test_rejects_malformed_interior_mutation_without_modification(tmp_path: Path) -> None:
    root = str(tmp_path)
    metadata = _write_raw_session(
        root,
        "malformed-interior",
        [
            {
                "kind": "record",
                "type": "operation_started",
                "id": "run",
                "lane": "main",
                "seq": 1,
                "timestamp": 1,
                "source_leaf_id": None,
            },
            {"kind": "fact", "fact": "name", "name": "after", "seq": 2},
        ],
    )
    corrupted = Path(metadata["path"]).read_text("utf-8")

    with pytest.raises(SessionError) as exc_info:
        await _repo(root).open(metadata)
    assert exc_info.value.code == "invalid_entry"
    assert Path(metadata["path"]).read_text("utf-8") == corrupted


# ---------------------------------------------------------------------------
# 全类型落盘往返（对齐 jsonl-storage.test.ts）
# ---------------------------------------------------------------------------


def _usage(multiplier: int) -> Dict[str, Any]:
    return {
        "input": multiplier,
        "output": multiplier * 2,
        "cache_read": multiplier * 3,
        "cache_write": multiplier * 4,
        "total_tokens": multiplier * 10,
        "cost": {
            "input": multiplier * 0.1,
            "output": multiplier * 0.2,
            "cache_read": multiplier * 0.3,
            "cache_write": multiplier * 0.4,
            "total": multiplier,
        },
    }


async def test_round_trips_every_entry_type_and_bounded_queries(tmp_path: Path) -> None:
    root = str(tmp_path)
    session = await _repo(root).create({"id": "entries", "cwd": root})
    committed = []
    committed.append(
        await session.append_entry({"type": "message", "id": "message", "message": _user_message("question")}, "main")
    )
    committed.append(
        await session.append_entry(
            {
                "type": "message",
                "id": "assistant-tool-call",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I'll inspect it."},
                        {"type": "toolCall", "id": "call-1", "name": "read", "arguments": {"path": "README.md"}},
                    ],
                    "stop_reason": "toolUse",
                    "usage": _usage(1),
                    "timestamp": 2,
                },
            },
            "main",
        )
    )
    committed.append(
        await session.append_entry(
            {
                "type": "message",
                "id": "tool-result",
                "message": {
                    "role": "toolResult",
                    "tool_call_id": "call-1",
                    "tool_name": "read",
                    "content": [{"type": "text", "text": "contents"}],
                    "details": {"path": "README.md"},
                    "usage": _usage(2),
                    "is_error": False,
                    "timestamp": 3,
                },
                "terminate": True,
            },
            "main",
        )
    )
    committed.append(
        await session.append_entry(
            {"type": "model_change", "id": "model", "provider": "anthropic", "model_id": "claude-sonnet-4-5"},
            "main",
        )
    )
    committed.append(
        await session.append_entry({"type": "thinking_level_change", "id": "thinking", "thinking_level": "high"}, "main")
    )
    committed.append(
        await session.append_entry({"type": "active_tools_change", "id": "tools", "active_tool_names": ["read", "bash"]}, "main")
    )
    committed.append(
        await session.append_entry(
            {
                "type": "compaction",
                "id": "compaction",
                "summary": "summary",
                "retained_tail": [_user_message("retained")],
                "tokens_before": 123,
                "details": {"source": "test"},
                "usage": _usage(1),
            },
            "main",
        )
    )
    committed.append(
        await session.append_entry(
            {
                "type": "branch_summary",
                "id": "branch-summary",
                "from_id": "message",
                "summary": "branch",
                "details": {"reason": "navigation"},
                "usage": _usage(2),
            },
            "main",
        )
    )
    committed.append(
        await session.append_entry(
            {"type": "custom", "id": "custom", "custom_type": "note", "data": {"nested": {"value": 1}}}, "main"
        )
    )

    reopened = await _repo(root).open(await session.get_metadata())
    assert await reopened.find_entries(_eq(order="oldestFirst")) == committed
    assert [
        e["id"] for e in await reopened.find_entries_on_branch(bounds=_bb(stop_at_type="compaction"))
    ] == ["custom", "branch-summary", "compaction"]
    assert [
        e["id"]
        for e in await reopened.find_entries(
            _eq(order="oldestFirst", cursor=_cursor(committed[5]["seq"]), limit=2)
        )
    ] == ["compaction", "branch-summary"]
    assert [e["id"] for e in await reopened.find_entries(_eq(custom_type="note"))] == ["custom"]

    # 隔离：改读到的深拷贝不影响库内状态与后续读取
    custom = await reopened.get_entry("custom")
    custom["data"]["nested"]["value"] = 99
    assert (await reopened.get_entry("custom")) == committed[-1]
    assert await reopened.find_entries(_eq(order="oldestFirst")) == committed


async def test_round_trips_records_and_ledger_after_reopen(tmp_path: Path) -> None:
    root = str(tmp_path)
    session = await _repo(root).create({"id": "records", "cwd": root})
    await session.append_entry({"type": "message", "id": "assistant", "message": {"role": "assistant", "content": []}}, "main")
    run = await session.append_record(
        {
            "type": "operation_started",
            "id": "run",
            "lane": "main",
            "source_leaf_id": None,
            "intent": {"kind": "run", "original_prompt": [], "initial_messages": []},
        }
    )
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
            "usage": _usage(1),
        }
    )

    reopened = await _repo(root).open(await session.get_metadata())
    assert await reopened.find_open_operations("main", limit=2) == [run]
    stats = await reopened.get_stats()
    assert stats.cached_tokens == 3 and stats.uncached_tokens == 5 and stats.total_tokens == 10
    assert stats.cost_total == pytest.approx(1.0)

    await reopened.append_record(
        {
            "type": "usage",
            "id": "correction",
            "lane": "main",
            "cause": "adjustment",
            "usage": {
                "input": -2,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "total_tokens": -2,
                "cost": {"total": -0.5},
            },
        }
    )
    stats = await reopened.get_stats()
    assert stats.total_tokens == 8 and stats.cost_total == pytest.approx(0.5)

    verified = await _repo(root).open(await session.get_metadata())
    assert (await verified.get_stats()).total_tokens == 8


async def test_concurrent_cross_lane_writes_shared_sequence_order(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    session = await repository.create({"id": "session", "cwd": root})
    await session.append_entry({"type": "message", "id": "root", "message": _user_message("root")}, "main")
    await session.create_lane("thread", "root")

    entries = await asyncio.gather(
        session.append_entry({"type": "custom", "id": "main-1", "custom_type": "note"}, "main"),
        session.append_entry({"type": "custom", "id": "thread-1", "custom_type": "note"}, "thread"),
        session.append_entry({"type": "custom", "id": "main-2", "custom_type": "note"}, "main"),
        session.append_entry({"type": "custom", "id": "thread-2", "custom_type": "note"}, "thread"),
    )
    seqs = [e["seq"] for e in entries]
    assert len(set(seqs)) == 4

    reopened = await _repo(root).open(await session.get_metadata())
    lines = _read_lines((await reopened.get_metadata())["path"])
    mutation_seqs = [line["seq"] for line in lines[1:]]
    assert mutation_seqs == sorted(mutation_seqs)


async def test_rejects_non_json_payload_without_durable_change(tmp_path: Path) -> None:
    root = str(tmp_path)
    repository = _repo(root)
    session = await repository.create({"id": "session", "cwd": root})
    metadata = await session.get_metadata()
    path = Path(metadata["path"])
    before = path.read_text("utf-8")

    with pytest.raises(SessionError) as exc_info:
        await session.append_custom_entry("invalid", {"value": {1, 2}})
    assert exc_info.value.code == "invalid_payload"
    assert path.read_text("utf-8") == before

    # 失败不污染写入队列：后续合法追加正常
    valid_id = await session.append_custom_entry("valid", {"value": 1})
    assert (await session.get_entry(valid_id))["seq"] == 1
    reopened = await _repo(root).open(metadata)
    assert [e["id"] for e in await reopened.find_entries()] == [valid_id]
