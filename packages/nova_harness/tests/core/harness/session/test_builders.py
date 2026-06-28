"""
会话构建器单元测试。
"""

from nova_harness.core.harness.session.builders import (
    build_session_tree,
    create_branched_session_entries,
)
from nova_harness.core.types.session import (
    CustomEntry,
    LabelEntry,
    SessionHeader,
)


def _entry(
    entry_id: str, parent_id: str = None, timestamp: str = "2024-01-01T00:00:00"
):
    """构造一个最简单的会话条目用于树构建。"""
    return CustomEntry(
        id=entry_id,
        parent_id=parent_id,
        timestamp=timestamp,
        type="custom",
        custom_type="test",
    )


def test_build_session_tree_single_root():
    e1 = _entry("a")
    roots = build_session_tree([e1], {})
    assert len(roots) == 1
    assert roots[0].entry.id == "a"
    assert roots[0].children == []


def test_build_session_tree_parent_child():
    e1 = _entry("a")
    e2 = _entry("b", parent_id="a")
    roots = build_session_tree([e1, e2], {})
    assert len(roots) == 1
    assert roots[0].entry.id == "a"
    assert len(roots[0].children) == 1
    assert roots[0].children[0].entry.id == "b"


def test_build_session_tree_orphan_becomes_root():
    e1 = _entry("a")
    e2 = _entry("b", parent_id="missing")
    roots = build_session_tree([e1, e2], {})
    assert len(roots) == 2


def test_build_session_tree_self_parent_is_root():
    e1 = _entry("a", parent_id="a")
    roots = build_session_tree([e1], {})
    assert len(roots) == 1


def test_build_session_tree_sorted_by_timestamp():
    e1 = _entry("a")
    e2 = _entry("b", parent_id="a", timestamp="2024-01-01T00:00:02")
    e3 = _entry("c", parent_id="a", timestamp="2024-01-01T00:00:01")
    roots = build_session_tree([e1, e2, e3], {})
    children = roots[0].children
    assert [c.entry.id for c in children] == ["c", "b"]


def test_build_session_tree_nested_sorting():
    e1 = _entry("a")
    e2 = _entry("b", parent_id="a", timestamp="2024-01-01T00:00:01")
    e3 = _entry("c", parent_id="b", timestamp="2024-01-01T00:00:03")
    e4 = _entry("d", parent_id="b", timestamp="2024-01-01T00:00:02")
    roots = build_session_tree([e1, e2, e3, e4], {})
    nested = roots[0].children[0].children
    assert [n.entry.id for n in nested] == ["d", "c"]


def test_build_session_tree_with_labels():
    e1 = _entry("a")
    roots = build_session_tree([e1], {"a": "Label A"})
    assert roots[0].label == "Label A"


def test_create_branched_session_entries():
    e1 = _entry("a")
    file_entries, session_id = create_branched_session_entries(
        cwd="/tmp",
        previous_session_file="/tmp/prev.jsonl",
        path_without_labels=[e1],
        labels_to_write=[("a", "label-a")],
        persist=True,
    )
    assert len(file_entries) == 3
    assert isinstance(file_entries[0], SessionHeader)
    assert file_entries[0].parent_session == "/tmp/prev.jsonl"
    assert file_entries[0].cwd == "/tmp"
    assert isinstance(file_entries[2], LabelEntry)
    assert file_entries[2].target_id == "a"
    assert file_entries[2].label == "label-a"
    assert session_id


def test_create_branched_session_entries_no_persist():
    e1 = _entry("a")
    file_entries, _ = create_branched_session_entries(
        cwd="/tmp",
        previous_session_file="/tmp/prev.jsonl",
        path_without_labels=[e1],
        labels_to_write=[],
        persist=False,
    )
    assert file_entries[0].parent_session is None


def test_create_branched_session_entries_multiple_labels_chain():
    e1 = _entry("a")
    file_entries, _ = create_branched_session_entries(
        cwd="/tmp",
        previous_session_file=None,
        path_without_labels=[e1],
        labels_to_write=[("a", "A"), ("a", "B")],
        persist=False,
    )
    labels = [e for e in file_entries if isinstance(e, LabelEntry)]
    assert len(labels) == 2
    assert labels[0].parent_id == "a"
    assert labels[1].parent_id == labels[0].id
