"""JSONL v4 行编解码测试（对齐 pi ``jsonl-codec.test.ts``，snake_case 方言）。"""

import json

from nova_agent.harness.session.jsonl.codec import (
    encode_header,
    encode_mutation,
    metadata_from_header,
    parse_header,
    parse_mutation,
)
from nova_agent.harness.session.jsonl.errors import JsonlDecodeError
from nova_agent.harness.session.jsonl.types import JsonlV4Header
from nova_agent.harness.session.state import (
    EntryMutation,
    LabelFactMutation,
    LaneMutation,
    NameFactMutation,
    RecordMutation,
)


def _expect_header_round_trip(header: JsonlV4Header) -> None:
    encoded = encode_header(header)
    assert encoded.endswith("\n")
    ok, value = parse_header(encoded.rstrip("\n"))
    assert ok
    assert value == header


def _expect_mutation_round_trip(mutation) -> None:
    encoded = encode_mutation(mutation)
    assert encoded.endswith("\n")
    ok, value = parse_mutation(encoded.rstrip("\n"))
    assert ok
    assert value == mutation


def test_header_round_trip_with_resolved_parent() -> None:
    _expect_header_round_trip(
        {
            "kind": "header",
            "version": 4,
            "id": "session",
            "created_at": 1_700_000_000_000,
            "cwd": "/workspace/project",
            "parent_session_id": "parent",
            "metadata": {"owner": "agent", "nested": {"enabled": True}, "values": [1, None, "two"]},
        }
    )


def test_header_round_trip_with_legacy_parent_path() -> None:
    _expect_header_round_trip(
        {
            "kind": "header",
            "version": 4,
            "id": "legacy-child",
            "created_at": 1_700_000_000_001,
            "cwd": "/workspace/project",
            "legacy_parent_session_path": "/sessions/missing-parent.jsonl",
        }
    )


def test_header_projects_into_metadata() -> None:
    header: JsonlV4Header = {
        "kind": "header",
        "version": 4,
        "id": "session",
        "created_at": 1_700_000_000_000,
        "cwd": "/workspace/project",
        "legacy_parent_session_path": "/sessions/missing-parent.jsonl",
        "metadata": {"owner": "agent"},
    }
    assert metadata_from_header(header, "/sessions/session.jsonl", 1_700_000_000_100.0) == {
        "id": "session",
        "created_at": 1_700_000_000_000,
        "cwd": "/workspace/project",
        "path": "/sessions/session.jsonl",
        "modified_at": 1_700_000_000_100.0,
        "source_format": 4,
        "legacy_parent_session_path": "/sessions/missing-parent.jsonl",
        "metadata": {"owner": "agent"},
    }


def test_mutation_syntax_and_schema_errors() -> None:
    for line, kind in [("{", "syntax"), (json.dumps({"kind": "unknown", "seq": 1}), "schema")]:
        ok, error = parse_mutation(line)
        assert not ok
        assert isinstance(error, JsonlDecodeError)
        assert error.kind == kind


def test_entry_line_round_trip_lane_bound() -> None:
    _expect_mutation_round_trip(
        EntryMutation(
            lane="main",
            entry={
                "type": "custom",
                "id": "entry-1",
                "seq": 1,
                "parent_id": None,
                "timestamp": 100,
                "custom_type": "note",
                "data": {"text": "hello"},
            },
        )
    )


def test_entry_line_round_trip_without_lane() -> None:
    _expect_mutation_round_trip(
        EntryMutation(
            entry={
                "type": "custom",
                "id": "entry-1",
                "seq": 1,
                "parent_id": None,
                "timestamp": 100,
                "custom_type": "note",
            }
        )
    )


def test_record_line_round_trip() -> None:
    _expect_mutation_round_trip(
        RecordMutation(
            record={
                "type": "operation_started",
                "id": "run-1",
                "seq": 1,
                "lane": "main",
                "timestamp": 100,
                "source_leaf_id": None,
                "intent": {"kind": "run", "original_prompt": [], "initial_messages": []},
            }
        )
    )


def test_lane_line_round_trip() -> None:
    _expect_mutation_round_trip(
        LaneMutation(seq=1, lane="thread", leaf_id="entry-1")
    )


def test_fact_lines_round_trip_including_cleared_values() -> None:
    _expect_mutation_round_trip(NameFactMutation(seq=1, name="Example"))
    _expect_mutation_round_trip(NameFactMutation(seq=2, name=None))
    _expect_mutation_round_trip(
        LabelFactMutation(seq=3, target_id="entry-1", label="checkpoint")
    )


def test_rejects_incomplete_required_fields() -> None:
    cases = [
        # custom entry 缺 custom_type
        {"kind": "entry", "type": "custom", "id": "entry", "parent_id": None, "seq": 1, "timestamp": 1},
        # operation_started 缺 intent
        {
            "kind": "record",
            "type": "operation_started",
            "id": "run",
            "lane": "main",
            "seq": 1,
            "timestamp": 1,
            "source_leaf_id": None,
        },
        # operation_finished 缺 run_id
        {
            "kind": "record",
            "type": "operation_finished",
            "id": "finish",
            "lane": "main",
            "seq": 1,
            "timestamp": 1,
            "outcome": "completed",
        },
    ]
    for mutation in cases:
        ok, _error = parse_mutation(json.dumps(mutation))
        assert not ok


def test_encode_drops_cleared_optional_keys() -> None:
    """name=None / 无 lane 的 entry：键整体省略而非写 null（对齐 TS undefined）。"""
    encoded = encode_mutation(NameFactMutation(seq=2, name=None))
    assert "name" not in json.loads(encoded)
    encoded = encode_mutation(
        EntryMutation(
            entry={
                "type": "custom",
                "id": "e",
                "seq": 1,
                "parent_id": None,
                "timestamp": 1,
                "custom_type": "note",
            }
        )
    )
    assert "lane" not in json.loads(encoded)
