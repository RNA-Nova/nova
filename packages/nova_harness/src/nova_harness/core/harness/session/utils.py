"""
Utility functions for session management
"""

import json
import os
import secrets
import time
from datetime import datetime
from typing import Dict, List, Optional, Set

from nova_ai import Message, TextContent

from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.types.session import (
    ActiveToolsChangeEntry,
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    FileEntry,
    LabelEntry,
    LeafEntry,
    ModelChangeEntry,
    SessionContext,
    SessionEntry,
    SessionHeader,
    SessionInfoEntry,
    SessionMessageEntry,
    ThinkingLevelChangeEntry,
)
from nova_harness.core.utils.messages import (
    create_branch_summary_message,
    create_compaction_summary_message,
    create_custom_message,
)

# TS 风格的 generate_session_id：时间戳 + 随机 + 序列号
_last_session_id_timestamp = 0
_session_id_sequence = 0


def _format_uuid(bytes_data: bytes) -> str:
    hex_str = bytes_data.hex()
    return (
        f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-"
        f"{hex_str[16:20]}-{hex_str[20:]}"
    )


def generate_session_id() -> str:
    """生成与 TS 参考实现语义一致的会话 ID 字符串。"""
    global _last_session_id_timestamp, _session_id_sequence

    random = secrets.token_bytes(16)
    timestamp = int(time.time() * 1000)

    if timestamp > _last_session_id_timestamp:
        _session_id_sequence = (
            random[6] * 0x1000000 + random[7] * 0x10000 + random[8] * 0x100 + random[9]
        )
        _last_session_id_timestamp = timestamp
    else:
        _session_id_sequence = (_session_id_sequence + 1) & 0xFFFFFFFF
        if _session_id_sequence == 0:
            _last_session_id_timestamp += 1

    seq = _session_id_sequence
    ts = _last_session_id_timestamp

    bytes_data = bytearray(16)
    bytes_data[0] = (ts // 0x10000000000) & 0xFF
    bytes_data[1] = (ts // 0x100000000) & 0xFF
    bytes_data[2] = (ts // 0x1000000) & 0xFF
    bytes_data[3] = (ts // 0x10000) & 0xFF
    bytes_data[4] = (ts // 0x100) & 0xFF
    bytes_data[5] = ts & 0xFF
    bytes_data[6] = 0x70 | ((seq >> 28) & 0x0F)
    bytes_data[7] = (seq >> 20) & 0xFF
    bytes_data[8] = 0x80 | ((seq >> 14) & 0x3F)
    bytes_data[9] = (seq >> 6) & 0xFF
    bytes_data[10] = ((seq & 0x3F) << 2) | (random[10] & 0x03)
    bytes_data[11] = random[11]
    bytes_data[12] = random[12]
    bytes_data[13] = random[13]
    bytes_data[14] = random[14]
    bytes_data[15] = random[15]

    return _format_uuid(bytes(bytes_data))


def generate_id(by_id: Set[str]) -> str:
    """生成唯一的短ID（TS 风格：generate_session_id 前 8 个 hex 字符）。"""
    for _ in range(100):
        id_ = generate_session_id()[:8]
        if id_ not in by_id:
            return id_
    # 回退到完整 UUID
    return generate_session_id()


def parse_session_entries(content: str) -> List[FileEntry]:
    """解析会话条目"""
    entries = []
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            # 根据type创建对应的dataclass
            if data.get("type") == "session":
                entries.append(SessionHeader.model_validate(data))
            elif data.get("type") == "message":
                entries.append(SessionMessageEntry.model_validate(data))
            elif data.get("type") == "thinking_level_change":
                entries.append(ThinkingLevelChangeEntry.model_validate(data))
            elif data.get("type") == "model_change":
                entries.append(ModelChangeEntry.model_validate(data))
            elif data.get("type") == "active_tools_change":
                entries.append(ActiveToolsChangeEntry.model_validate(data))
            elif data.get("type") == "compaction":
                entries.append(CompactionEntry.model_validate(data))
            elif data.get("type") == "branch_summary":
                entries.append(BranchSummaryEntry.model_validate(data))
            elif data.get("type") == "custom":
                entries.append(CustomEntry.model_validate(data))
            elif data.get("type") == "leaf":
                entries.append(LeafEntry.model_validate(data))
            elif data.get("type") == "label":
                entries.append(LabelEntry.model_validate(data))
            elif data.get("type") == "session_info":
                entries.append(SessionInfoEntry.model_validate(data))
            elif data.get("type") == "custom_message":
                entries.append(CustomMessageEntry.model_validate(data))
        except (json.JSONDecodeError, TypeError):
            continue
    return entries


def get_latest_compaction_entry(
    entries: List[SessionEntry],
) -> Optional[CompactionEntry]:
    """获取最新的压缩条目"""
    for entry in reversed(entries):
        if entry.type == "compaction":
            return entry
    return None


def build_session_context(
    entries: List[SessionEntry],
    leaf_id: Optional[str] = None,
    by_id: Optional[Dict[str, SessionEntry]] = None,
) -> SessionContext:
    """构建会话上下文"""
    if by_id is None:
        by_id = {e.id: e for e in entries}

    # 查找叶子节点
    leaf = None
    if leaf_id is None:
        leaf = entries[-1] if entries else None
    elif leaf_id is not None:
        leaf = by_id.get(leaf_id)

    if leaf is None:
        return SessionContext()

    # 收集路径
    path = []
    current = leaf
    while current:
        path.insert(0, current)
        current = by_id.get(current.parent_id) if current.parent_id else None

    # 提取设置
    thinking_level = None
    model = None
    active_tool_names = None
    compaction = None

    for entry in path:
        if entry.type == "thinking_level_change":
            thinking_level = entry.thinking_level
        elif entry.type == "model_change":
            model = (entry.provider, entry.model_id)
        elif entry.type == "active_tools_change":
            active_tool_names = list(entry.active_tool_names)
        elif entry.type == "message" and hasattr(entry.message, "role"):
            if entry.message.role == "assistant":
                model = (entry.message.provider, entry.message.model)
        elif entry.type == "compaction":
            compaction = entry

    # 构建消息列表
    messages = []

    def append_message(entry: SessionEntry):
        if entry.type == "message":
            messages.append(entry.message)
        elif entry.type == "custom_message":
            messages.append(
                create_custom_message(
                    entry.custom_type,
                    entry.content,
                    entry.display,
                    entry.details,
                    entry.timestamp,
                )
            )
        elif entry.type == "branch_summary" and entry.summary:
            messages.append(
                create_branch_summary_message(
                    entry.summary, entry.from_id, entry.timestamp
                )
            )

    if compaction:
        # 先添加摘要
        messages.append(
            create_compaction_summary_message(
                compaction.summary, compaction.tokens_before, compaction.timestamp
            )
        )

        # 找到compaction在路径中的位置
        compaction_idx = next(
            (
                i
                for i, e in enumerate(path)
                if e.type == "compaction" and e.id == compaction.id
            ),
            -1,
        )

        # 添加保留的消息
        found_first_kept = False
        for i in range(compaction_idx):
            entry = path[i]
            if entry.id == compaction.first_kept_entry_id:
                found_first_kept = True
            if found_first_kept:
                append_message(entry)

        # 添加compaction之后的消息
        for i in range(compaction_idx + 1, len(path)):
            append_message(path[i])
    else:
        for entry in path:
            append_message(entry)

    return SessionContext(
        messages=messages,
        thinking_level=thinking_level,
        model=model,
        active_tool_names=active_tool_names,
    )


def get_default_session_dir(cwd: str) -> str:
    """获取默认会话目录"""
    cleaned_cwd = cwd.lstrip("/\\").replace("/", "-").replace("\\", "-")
    safe_path = f"--{cleaned_cwd}--"
    session_dir = os.path.join(get_agent_dir(), "sessions", safe_path)
    if not os.path.exists(session_dir):
        os.makedirs(session_dir, exist_ok=True)
    return session_dir


def load_entries_from_file(file_path: str) -> List[FileEntry]:
    """从文件加载条目"""
    if not os.path.exists(file_path):
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    entries = parse_session_entries(content)

    # 验证会话头部
    if not entries:
        return entries
    header = entries[0]
    if not isinstance(header, SessionHeader) or not header.id:
        return []

    return entries


def is_valid_session_file(file_path: str) -> bool:
    """检查是否为有效的会话文件"""
    try:
        with open(file_path, "rb") as f:
            first_line = f.readline().decode("utf-8").strip()
            if not first_line:
                return False
            data = json.loads(first_line)
            return data.get("type") == "session" and "id" in data
    except Exception:
        return False


def find_most_recent_session(session_dir: str) -> Optional[str]:
    """查找最近的会话"""
    try:
        files = []
        for f in os.listdir(session_dir):
            if f.endswith(".jsonl"):
                path = os.path.join(session_dir, f)
                if is_valid_session_file(path):
                    files.append((path, os.path.getmtime(path)))
        files.sort(key=lambda x: x[1], reverse=True)
        return files[0][0] if files else None
    except Exception:
        return None


def extract_text_content(message: Message) -> str:
    """提取文本内容"""
    if isinstance(message.content, str):
        return message.content
    return " ".join(
        block.text for block in message.content if isinstance(block, TextContent)
    )


def get_last_activity_time(entries: List[FileEntry]) -> Optional[float]:
    """获取最后活动时间"""
    last_time = None

    for entry in entries:
        if entry.type != "message":
            continue

        message = entry.message
        if not hasattr(message, "role") or message.role not in ("user", "assistant"):
            continue

        # 尝试从消息中获取时间戳
        if hasattr(message, "timestamp") and isinstance(
            message.timestamp, (int, float)
        ):
            last_time = max(last_time or 0, message.timestamp)
            continue

        # 从条目中获取时间戳
        if isinstance(entry.timestamp, str):
            try:
                t = datetime.fromisoformat(entry.timestamp).timestamp()
                last_time = max(last_time or 0, t)
            except Exception:
                pass

    return last_time


def get_session_modified_date(
    entries: List[FileEntry], header: SessionHeader, stats_mtime: float
) -> datetime:
    """获取会话修改日期"""
    last_activity = get_last_activity_time(entries)
    if last_activity is not None:
        return datetime.fromtimestamp(last_activity)

    try:
        header_time = datetime.fromisoformat(header.timestamp)
        return header_time
    except Exception:
        return datetime.fromtimestamp(stats_mtime)
