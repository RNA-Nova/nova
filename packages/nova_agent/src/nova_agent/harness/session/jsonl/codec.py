"""JSONL 行编解码（对齐 TS ``session/jsonl/codec.ts``）。

行格式（snake_case 方言）：

- header：``{"kind": "header", "version": 4, "id", "created_at", "cwd", ...}``
- entry mutation：``{"kind": "entry", "lane"?, ...entry}``
- record mutation：``{"kind": "record", ...record}``
- lane mutation：``{"kind": "lane", "seq", "lane", "leaf_id"}``
- fact mutation：``{"kind": "fact", "seq", "fact": "name"|"label", ...}``

编码侧语义（对齐 TS ``JSON.stringify`` 丢 ``undefined`` 不丢 ``null``）：
信封可选键（entry 的 ``lane``、fact 的 ``name``/``label``、header 的
``parent_session_id`` 等）为 ``None`` 时**整体省略**；载荷内部的
``parent_id: null`` / ``source_leaf_id: null`` 等是合法值，原样保留。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple, cast

from ..state import (
    EntryMutation,
    LabelFactMutation,
    LaneMutation,
    NameFactMutation,
    RecordMutation,
    SessionMutation,
)
from ..types import Entry, LaneRecord
from .errors import JsonlDecodeError
from .types import JsonlV4Header

__all__ = [
    "encode_header",
    "encode_mutation",
    "metadata_from_header",
    "parse_header",
    "parse_mutation",
]

_ENTRY_TYPES = {
    "message",
    "model_change",
    "thinking_level_change",
    "active_tools_change",
    "compaction",
    "branch_summary",
    "custom",
}
_RECORD_TYPES = {
    "operation_started",
    "abort_requested",
    "operation_finished",
    "step_attempt",
    "tool_started",
    "queue_enqueued",
    "queue_cancelled",
    "write_deferred",
    "usage",
}
_OPERATION_KINDS = {"run", "compaction", "navigation"}


def _is_object(value: Any) -> bool:
    return isinstance(value, dict)


def _parse_object(line: str) -> Dict[str, Any]:
    try:
        value = json.loads(line)
    except ValueError as exc:
        raise JsonlDecodeError("syntax", "is not valid JSON", exc) from exc
    if not _is_object(value):
        raise JsonlDecodeError("schema", "is not a JSON object")
    return value


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise JsonlDecodeError("schema", f"has invalid {field}")
    return value


def _require_sequence(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise JsonlDecodeError("schema", "has invalid seq")
    return value


def _require_timestamp(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise JsonlDecodeError("schema", "has invalid timestamp")
    return value


def _require_nullable_id(value: Any, field: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise JsonlDecodeError("schema", f"has invalid {field}")
    return value


def decode_header(line: str) -> JsonlV4Header:
    value = _parse_object(line)
    if value.get("kind") != "header":
        raise JsonlDecodeError("schema", "is not a header")
    if value.get("version") != 4:
        raise JsonlDecodeError("schema", "has unsupported session version")
    parent_session_id = value.get("parent_session_id")
    if parent_session_id is not None and not isinstance(parent_session_id, str):
        raise JsonlDecodeError("schema", "has invalid parent_session_id")
    legacy = value.get("legacy_parent_session_path")
    if legacy is not None and not isinstance(legacy, str):
        raise JsonlDecodeError("schema", "has invalid legacy_parent_session_path")
    if parent_session_id is not None and legacy is not None:
        raise JsonlDecodeError("schema", "has both parent_session_id and legacy_parent_session_path")
    metadata = value.get("metadata")
    if metadata is not None and not _is_object(metadata):
        raise JsonlDecodeError("schema", "has invalid metadata")
    header: JsonlV4Header = {
        "kind": "header",
        "version": 4,
        "id": _require_string(value.get("id"), "id"),
        "created_at": _require_timestamp(value.get("created_at")),
        "cwd": _require_string(value.get("cwd"), "cwd"),
    }
    if parent_session_id is not None:
        header["parent_session_id"] = parent_session_id
    if legacy is not None:
        header["legacy_parent_session_path"] = legacy
    if metadata is not None:
        header["metadata"] = metadata
    return header


def parse_header(line: str) -> Tuple[bool, Any]:
    """``(ok, header | JsonlDecodeError)``——TS Result 的 Python 形态。"""
    try:
        return True, decode_header(line)
    except JsonlDecodeError as exc:
        return False, exc


def encode_header(header: JsonlV4Header) -> str:
    optional = {k: v for k, v in header.items() if v is not None}
    return f"{json.dumps(optional, ensure_ascii=False)}\n"


def metadata_from_header(header: JsonlV4Header, path: str, modified_at: float) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "id": header["id"],
        "created_at": header["created_at"],
        "cwd": header["cwd"],
        "path": path,
        "modified_at": modified_at,
        "source_format": 4,
    }
    parent_session_id = header.get("parent_session_id")
    if parent_session_id is not None:
        metadata["parent_session_id"] = parent_session_id
    legacy = header.get("legacy_parent_session_path")
    if legacy is not None:
        metadata["legacy_parent_session_path"] = legacy
    header_metadata = header.get("metadata")
    if header_metadata is not None:
        metadata["metadata"] = header_metadata
    return metadata


def _parse_entry_mutation(value: Dict[str, Any], seq: int) -> EntryMutation:
    raw_lane = value.get("lane")
    lane = None if raw_lane is None else _require_string(raw_lane, "lane")
    entry_id = _require_string(value.get("id"), "id")
    entry_type = _require_string(value.get("type"), "entry type")
    if entry_type not in _ENTRY_TYPES:
        raise JsonlDecodeError("schema", f"has unknown entry type {entry_type}")
    parent_id = _require_nullable_id(value.get("parent_id"), "parent_id")
    timestamp = _require_timestamp(value.get("timestamp"))
    if entry_type == "custom":
        _require_string(value.get("custom_type"), "custom_type")
    entry: Dict[str, Any] = {k: v for k, v in value.items() if k not in ("kind", "lane")}
    entry["id"] = entry_id
    entry["type"] = entry_type
    entry["parent_id"] = parent_id
    entry["seq"] = seq
    entry["timestamp"] = timestamp
    return EntryMutation(entry=cast(Entry, entry), lane=lane)


def _parse_record_mutation(value: Dict[str, Any], seq: int) -> RecordMutation:
    record_id = _require_string(value.get("id"), "id")
    lane = _require_string(value.get("lane"), "lane")
    record_type = _require_string(value.get("type"), "record type")
    if record_type not in _RECORD_TYPES:
        raise JsonlDecodeError("schema", f"has unknown record type {record_type}")
    timestamp = _require_timestamp(value.get("timestamp"))
    if record_type == "operation_started":
        intent = value.get("intent")
        if not isinstance(intent, dict):
            raise JsonlDecodeError("schema", "has invalid intent")
        operation_kind = _require_string(intent.get("kind"), "operation kind")
        if operation_kind not in _OPERATION_KINDS:
            raise JsonlDecodeError("schema", f"has unknown operation kind {operation_kind}")
    if record_type == "operation_finished":
        _require_string(value.get("run_id"), "run_id")
    record: Dict[str, Any] = {k: v for k, v in value.items() if k != "kind"}
    record["id"] = record_id
    record["lane"] = lane
    record["type"] = record_type
    record["seq"] = seq
    record["timestamp"] = timestamp
    return RecordMutation(record=cast(LaneRecord, record))


def _parse_lane_mutation(value: Dict[str, Any], seq: int) -> LaneMutation:
    return LaneMutation(
        seq=seq,
        lane=_require_string(value.get("lane"), "lane"),
        leaf_id=_require_nullable_id(value.get("leaf_id"), "leaf_id"),
    )


def _parse_fact_mutation(value: Dict[str, Any], seq: int) -> SessionMutation:
    if value.get("fact") == "name":
        name = value.get("name")
        if name is not None and not isinstance(name, str):
            raise JsonlDecodeError("schema", "has invalid name")
        return NameFactMutation(seq=seq, name=name)
    if value.get("fact") == "label":
        label = value.get("label")
        if label is not None and not isinstance(label, str):
            raise JsonlDecodeError("schema", "has invalid label")
        return LabelFactMutation(
            seq=seq,
            target_id=_require_string(value.get("target_id"), "target_id"),
            label=label,
        )
    raise JsonlDecodeError("schema", "has unknown fact type")


def decode_mutation(line: str) -> SessionMutation:
    value = _parse_object(line)
    seq = _require_sequence(value.get("seq"))
    kind = value.get("kind")
    if kind == "entry":
        return _parse_entry_mutation(value, seq)
    if kind == "record":
        return _parse_record_mutation(value, seq)
    if kind == "lane":
        return _parse_lane_mutation(value, seq)
    if kind == "fact":
        return _parse_fact_mutation(value, seq)
    raise JsonlDecodeError("schema", "has unknown mutation kind")


def parse_mutation(line: str) -> Tuple[bool, Any]:
    """``(ok, mutation | JsonlDecodeError)``。"""
    try:
        return True, decode_mutation(line)
    except JsonlDecodeError as exc:
        return False, exc


def _dumps(value: Dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def encode_mutation(mutation: SessionMutation) -> str:
    """载荷内的 ``null``（parent_id / leaf_id / source_leaf_id 等）是合法值，
    必须保留；只有信封的可选键（entry 的 ``lane``、fact 的 ``name``/``label``）
    在清空时整体省略——对齐 TS ``JSON.stringify`` 只丢 ``undefined`` 不丢
    ``null`` 的语义。"""
    if isinstance(mutation, EntryMutation):
        payload: Dict[str, Any] = {"kind": "entry", **dict(mutation.entry)}
        if mutation.lane is not None:
            payload["lane"] = mutation.lane
        return f"{_dumps(payload)}\n"
    if isinstance(mutation, RecordMutation):
        return f"{_dumps({'kind': 'record', **dict(mutation.record)})}\n"
    if isinstance(mutation, LaneMutation):
        return f"{_dumps({'kind': 'lane', 'seq': mutation.seq, 'lane': mutation.lane, 'leaf_id': mutation.leaf_id})}\n"
    if isinstance(mutation, NameFactMutation):
        payload: Dict[str, Any] = {"kind": "fact", "seq": mutation.seq, "fact": "name"}
        if mutation.name is not None:
            payload["name"] = mutation.name
        return f"{_dumps(payload)}\n"
    if isinstance(mutation, LabelFactMutation):
        payload = {"kind": "fact", "seq": mutation.seq, "fact": "label", "target_id": mutation.target_id}
        if mutation.label is not None:
            payload["label"] = mutation.label
        return f"{_dumps(payload)}\n"
    raise TypeError(f"Unknown mutation type: {type(mutation).__name__}")
