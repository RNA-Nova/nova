"""
Builder functions for session management
"""

from datetime import datetime
from typing import Dict, List, Optional

from nova_harness.core.harness.session.utils import generate_id, generate_session_id
from nova_harness.core.types.session import (
    CURRENT_SESSION_VERSION,
    LabelEntry,
    SessionEntry,
    SessionHeader,
    SessionTreeNode,
)


def build_session_tree(
    entries: List[SessionEntry], labels_by_id: Dict[str, str]
) -> List[SessionTreeNode]:
    """构建会话树结构"""
    node_map = {}
    roots = []

    # 创建节点
    for entry in entries:
        label = labels_by_id.get(entry.id)
        node_map[entry.id] = SessionTreeNode(entry=entry, children=[], label=label)

    # 构建树
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

    # 按时间戳排序
    def sort_children(node: SessionTreeNode):
        node.children.sort(key=lambda n: n.entry.timestamp)
        for child in node.children:
            sort_children(child)

    for root in roots:
        sort_children(root)

    return roots


def create_branched_session_entries(
    cwd: str,
    previous_session_file: Optional[str],
    path_without_labels: List[SessionEntry],
    labels_to_write: List[tuple],
    persist: bool,
) -> tuple:
    """创建分支会话的条目列表"""
    new_session_id = generate_session_id()
    timestamp = datetime.now().isoformat()

    header = SessionHeader(
        type="session",
        version=CURRENT_SESSION_VERSION,
        id=new_session_id,
        timestamp=timestamp,
        cwd=cwd,
        parent_session=previous_session_file if persist else None,
    )

    # 收集标签
    path_entry_ids = {e.id for e in path_without_labels}

    # 构建标签条目
    last_entry_id = path_without_labels[-1].id if path_without_labels else None
    parent_id = last_entry_id
    label_entries = []
    for target_id, label in labels_to_write:
        label_entry = LabelEntry(
            type="label",
            id=generate_id(path_entry_ids),
            parent_id=parent_id,
            timestamp=datetime.now().isoformat(),
            target_id=target_id,
            label=label,
        )
        path_entry_ids.add(label_entry.id)
        label_entries.append(label_entry)
        parent_id = label_entry.id

    file_entries = [header] + path_without_labels + label_entries
    return file_entries, new_session_id
