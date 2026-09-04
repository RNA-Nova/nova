"""
Builder functions for session management
"""

from typing import Dict, List, Optional, Tuple

from nova_harness.core.harness.session.utils import generate_id, generate_session_id
from nova_harness.core.types.session import (
    CURRENT_SESSION_VERSION,
    FileEntry,
    LabelEntry,
    SessionEntry,
    SessionHeader,
    SessionTreeNode,
)


def build_session_tree(
    entries: List[SessionEntry],
    labels_by_id: Dict[str, str],
    label_timestamps_by_id: Optional[Dict[str, str]] = None,
) -> List[SessionTreeNode]:
    """构建会话树结构（迭代实现，避免深树递归溢出，对齐 TS getTree）。

    父子关系的累积/排序在纯 dict 中进行——可变构建容器不进 Pydantic
    （规则 1）；节点在关系定型后一次性物化（迭代后序，children 引用的
    是已定型节点）。
    """
    by_id = {entry.id: entry for entry in entries}

    # 第一遍：累积父子关系——orphan（父链断裂）与自引用条目作为根
    children_of: Dict[str, List[str]] = {entry.id: [] for entry in entries}
    root_ids: List[str] = []
    for entry in entries:
        if (
            entry.parent_id is None
            or entry.parent_id == entry.id
            or entry.parent_id not in by_id
        ):
            root_ids.append(entry.id)
        else:
            children_of[entry.parent_id].append(entry.id)

    # 按时间戳排序子节点（根列表保持条目序，对齐 TS）
    for ids in children_of.values():
        ids.sort(key=lambda entry_id: by_id[entry_id].timestamp)

    # 第二遍：迭代后序物化，children 一次性定型
    node_by_id: Dict[str, SessionTreeNode] = {}
    for root_id in root_ids:
        stack: List[Tuple[str, bool]] = [(root_id, False)]
        while stack:
            entry_id, children_done = stack.pop()
            if not children_done:
                stack.append((entry_id, True))
                for child_id in children_of[entry_id]:
                    stack.append((child_id, False))
                continue
            entry = by_id[entry_id]
            node_by_id[entry_id] = SessionTreeNode(
                entry=entry,
                children=[node_by_id[c] for c in children_of[entry_id]],
                label=labels_by_id.get(entry_id),
                label_timestamp=(
                    label_timestamps_by_id.get(entry_id)
                    if label_timestamps_by_id
                    else None
                ),
            )

    return [node_by_id[root_id] for root_id in root_ids]


def create_branched_session_entries(
    cwd: str,
    previous_session_file: Optional[str],
    path: List[SessionEntry],
    labels_to_write: List[Tuple[str, str, str]],
    timestamp: str,
) -> Tuple[List[FileEntry], str]:
    """创建分支会话的条目列表。

    过滤路径中的 label 条目并**重链 parent_id**（被过滤的 label 会造成
    parent 悬空，对齐 TS createBranchedSession 的 re-chaining）；labels 以
    新的 LabelEntry 重建在路径末尾，保留原时间戳。

    Args:
        cwd: 新会话 header 的 cwd
        previous_session_file: 源会话文件路径（作为 parent_session 记录）
        path: 完整分支路径（含 label 条目）
        labels_to_write: (target_id, label, timestamp) 三元组
    """
    # 过滤 label 条目并重链 parent_id
    path_without_labels: List[SessionEntry] = []
    path_parent_id: Optional[str] = None
    for entry in path:
        if entry.type == "label":
            continue
        path_without_labels.append(
            entry.model_copy(update={"parent_id": path_parent_id})
        )
        path_parent_id = entry.id

    new_session_id = generate_session_id()
    header = SessionHeader(
        type="session",
        version=CURRENT_SESSION_VERSION,
        id=new_session_id,
        timestamp=timestamp,
        cwd=cwd,
        parent_session=previous_session_file,
    )

    # 重建 label 条目（保留原时间戳）
    path_entry_ids = {e.id for e in path_without_labels}
    parent_id = path_without_labels[-1].id if path_without_labels else None
    label_entries: List[LabelEntry] = []
    for target_id, label, timestamp in labels_to_write:
        label_entry = LabelEntry(
            type="label",
            id=generate_id(path_entry_ids),
            parent_id=parent_id,
            timestamp=timestamp,
            target_id=target_id,
            label=label,
        )
        path_entry_ids.add(label_entry.id)
        label_entries.append(label_entry)
        parent_id = label_entry.id

    file_entries: List[FileEntry] = [header] + path_without_labels + label_entries
    return file_entries, new_session_id
