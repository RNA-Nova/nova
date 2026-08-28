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
    """构建会话树结构（迭代实现，避免深树递归溢出，对齐 TS getTree）。"""
    node_map = {}
    roots = []

    # 创建节点
    for entry in entries:
        node_map[entry.id] = SessionTreeNode(
            entry=entry,
            children=[],
            label=labels_by_id.get(entry.id),
            label_timestamp=(
                label_timestamps_by_id.get(entry.id) if label_timestamps_by_id else None
            ),
        )

    # 构建树：orphan（父链断裂）与自引用条目作为根处理
    for entry in entries:
        node = node_map[entry.id]
        if entry.parent_id is None or entry.parent_id == entry.id:
            roots.append(node)
        else:
            parent = node_map.get(entry.parent_id)
            if parent:
                parent.children.append(node)
            else:
                roots.append(node)

    # 按时间戳排序（迭代，对齐 TS 的栈实现）
    stack = list(roots)
    while stack:
        node = stack.pop()
        node.children.sort(key=lambda n: n.entry.timestamp)
        stack.extend(node.children)

    return roots


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
