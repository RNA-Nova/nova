"""
Utility functions for session management
"""

import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional, Set, Tuple

import uuid6
from nova_agent import AgentMessage
from nova_harness.core.config.defaults import SESSIONS_DIR_NAME, get_agent_dir
from nova_harness.core.harness.session.message_types import get_session_message_type
from nova_harness.core.types.messages import OpaqueUserToolMessage
from nova_harness.core.types.session import (
    CompactionEntry,
    FileEntry,
    SessionContext,
    SessionEntry,
    SessionHeader,
)
from nova_harness.core.types.session.entries import SessionMessageEntry
from nova_harness.core.utils.messages import (
    create_branch_summary_message,
    create_compaction_summary_message,
    create_custom_message,
)
from pydantic import Field, TypeAdapter

logger = logging.getLogger(__name__)


def now_iso() -> str:
    """当前时间的 ISO 字符串（UTC，对齐 TS ``new Date().toISOString()`` 格式）。"""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def generate_session_id() -> str:
    """生成会话 ID（UUIDv7，时间有序）。"""
    return str(uuid6.uuid7())


def generate_id(by_id: Set[str]) -> str:
    """生成唯一的短 ID（8 个随机 hex 字符，碰撞检查后回退 16 位）。"""
    for _ in range(100):
        id_ = secrets.token_hex(4)
        if id_ not in by_id:
            return id_
    return secrets.token_hex(8)


_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


def assert_valid_session_id(id_: str) -> None:
    """校验自定义会话 ID（对齐 TS assertValidSessionId）。"""
    if not _SESSION_ID_PATTERN.match(id_):
        raise ValueError(
            "Session id must be non-empty, contain only alphanumeric characters, "
            "'-', '_', and '.', and start and end with an alphanumeric character"
        )


# ============================================================================
# 会话文件解析
# ============================================================================

# FileEntry union 的成员均带 Literal["..."] type 字段，直接用 pydantic
# 判别式解析；未知类型与校验失败的行由调用方跳过
_FILE_ENTRY_ADAPTER = TypeAdapter(Annotated[FileEntry, Field(discriminator="type")])

# 静态 union 覆盖的消息 role：标准三类 + 扩展 custom。其余 role 一律视为
# 包级用户工具消息，走注册表复原/降级路径，不进适配器（适配器不认识它们）。
_STATIC_MESSAGE_ROLES = frozenset({"user", "assistant", "toolResult", "custom"})


def _parse_user_tool_message_entry(raw: Dict[str, Any]) -> "SessionMessageEntry":
    """构造包级用户工具消息条目（注册表命中复原，未命中降级不透明）。

    - role 已注册（包已安装并加载）→ 用注册类校验复原；校验失败（包版本
      演进、数据腐坏）同样落到降级路径，保证数据不丢；
    - role 未注册（包缺席）→ 降级为 ``OpaqueUserToolMessage``：原始
      message dict 全量收进 payload，默认不进 LLM 上下文。
    """
    msg = raw["message"]
    role = msg["role"]
    cls = get_session_message_type(role)
    message = None
    if cls is not None:
        try:
            message = cls.model_validate(msg)
        except Exception:
            logger.debug(
                "用户工具消息校验失败，降级为不透明消息（role=%s）",
                role,
                exc_info=True,
            )
    if message is None:
        raw_ts = msg.get("timestamp")
        message = OpaqueUserToolMessage(
            original_role=role,
            payload=msg,
            timestamp=raw_ts if isinstance(raw_ts, int) else 0,
        )
    return SessionMessageEntry(
        id=raw.get("id", ""),
        parent_id=raw.get("parent_id"),
        timestamp=raw.get("timestamp", ""),
        message=message,
    )


def _is_user_tool_message_dict(raw: Any) -> bool:
    """raw 是否为包级用户工具消息条目（type=message 且 role 非静态集合）。"""
    if not isinstance(raw, dict) or raw.get("type") != "message":
        return False
    msg = raw.get("message")
    if not isinstance(msg, dict):
        return False
    role = msg.get("role")
    return isinstance(role, str) and role not in _STATIC_MESSAGE_ROLES


def _repair_null_message_content(raw: Any) -> bool:
    """把 content 为 null 的消息条目修复为空 content（对齐 TS 的容错策略）。

    TS ``sessionEntryToContextMessages`` 对 user/assistant/toolResult 且
    ``content == null`` 的消息修复为 ``content: []`` 并保留条目（旧版本、
    fork、手工编辑的文件可能出现 null content）；Python 在加载关口做等价
    修复，修复后条目可正常通过判别式校验。返回是否发生了修复。
    """
    if not isinstance(raw, dict) or raw.get("type") != "message":
        return False
    message = raw.get("message")
    if not isinstance(message, dict):
        return False
    if message.get("role") not in ("user", "assistant", "toolResult"):
        return False
    if message.get("content") is not None:
        return False
    message["content"] = []
    return True


def parse_session_entry_line(line: str) -> Optional[FileEntry]:
    """解析单行 JSONL 为会话条目；非法 JSON、未知类型、校验失败返回 None。

    包级用户工具消息（role 不在静态集合）在适配器之前拦截——经
    ``session/message_types`` 注册表复原，包缺席时降级为不透明消息
    （数据不丢）。content 为 null 的标准消息按 TS 容错策略修复后保留。
    """
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
    except Exception:
        return None
    if _is_user_tool_message_dict(raw):
        try:
            return _parse_user_tool_message_entry(raw)
        except Exception:
            logger.debug("用户工具消息条目解析失败，跳过该行", exc_info=True)
            return None
    try:
        return _FILE_ENTRY_ADAPTER.validate_python(raw)
    except Exception:
        pass
    # 失败重试路径：仅对 null-content 消息做修复后重校验（正常行零开销）
    if _repair_null_message_content(raw):
        try:
            return _FILE_ENTRY_ADAPTER.validate_python(raw)
        except Exception:
            pass
    return None


def parse_session_entries(content: str) -> List[FileEntry]:
    """解析会话条目（单行解析/校验失败时跳过该行，不影响整体加载）。"""
    entries: List[FileEntry] = []
    for line in content.strip().split("\n"):
        entry = parse_session_entry_line(line)
        if entry is not None:
            entries.append(entry)
    return entries


def get_latest_compaction_entry(
    entries: List[SessionEntry],
) -> Optional[CompactionEntry]:
    """获取最新的压缩条目"""
    for entry in reversed(entries):
        if entry.type == "compaction":
            return entry
    return None


# ============================================================================
# 上下文构建（对齐 TS buildSessionPath / buildContextEntries / buildSessionContext）
# ============================================================================

# leaf_id 的三分语义（对齐 TS ``leafId?: string | null``）：
# 未传（默认）→ 使用最后一条 entry；显式 None → 空上下文；具体 id → 从该 entry 回溯
_USE_LAST_ENTRY = object()


def session_entry_to_context_messages(entry: SessionEntry) -> List[AgentMessage]:
    """把一个会话条目投影为 LLM 上下文消息（0 或 1 条）。

    对齐 TS ``sessionEntryToContextMessages``：plain custom 条目、label、
    thinking/model change 等不产生上下文消息。
    """
    if entry.type == "message":
        return [entry.message]
    if entry.type == "custom_message":
        return [
            create_custom_message(
                entry.custom_type,
                entry.content,
                entry.display,
                entry.details,
                entry.timestamp,
            )
        ]
    if entry.type == "branch_summary" and entry.summary:
        return [
            create_branch_summary_message(entry.summary, entry.from_id, entry.timestamp)
        ]
    if entry.type == "compaction":
        return [
            create_compaction_summary_message(
                entry.summary, entry.tokens_before, entry.timestamp
            )
        ]
    return []


def _build_entry_index(
    entries: List[SessionEntry], by_id: Optional[Dict[str, SessionEntry]]
) -> Dict[str, SessionEntry]:
    if by_id is not None:
        return by_id
    return {e.id: e for e in entries}


def _build_session_path(
    entries: List[SessionEntry],
    leaf_id: Any,
    by_id: Optional[Dict[str, SessionEntry]],
) -> List[SessionEntry]:
    """从 leaf 回溯到根的路径（对齐 TS buildSessionPath）。"""
    index = _build_entry_index(entries, by_id)
    if leaf_id is None:
        return []
    if leaf_id is _USE_LAST_ENTRY:
        leaf = entries[-1] if entries else None
    else:
        leaf = index.get(leaf_id)
        if leaf is None:
            leaf = entries[-1] if entries else None
    if leaf is None:
        return []

    path: List[SessionEntry] = []
    current: Optional[SessionEntry] = leaf
    while current:
        path.insert(0, current)
        current = index.get(current.parent_id) if current.parent_id else None
    return path


def _get_session_context_settings(
    path: List[SessionEntry],
) -> Tuple[Any, Any]:
    """沿路径提取 thinking_level / model（后者覆盖前者，对齐 TS getSessionContextSettings）。"""
    thinking_level = None
    model = None
    for entry in path:
        if entry.type == "thinking_level_change":
            thinking_level = entry.thinking_level
        elif entry.type == "model_change":
            model = (entry.provider, entry.model_id)
        elif entry.type == "message" and entry.message.role == "assistant":
            model = (entry.message.provider, entry.message.model)
    return thinking_level, model


def build_context_entries(
    entries: List[SessionEntry],
    leaf_id: Any = _USE_LAST_ENTRY,
    by_id: Optional[Dict[str, SessionEntry]] = None,
) -> List[SessionEntry]:
    """构建压缩感知的活动条目列表（对齐 TS buildContextEntries）。

    沿当前 leaf 路径，最新压缩点之前的已摘要条目被省略，
    由压缩条目本身 + 保留条目 + 压缩点之后的条目组成。
    """
    path = _build_session_path(entries, leaf_id, by_id)
    compaction: Optional[CompactionEntry] = None
    for entry in path:
        if entry.type == "compaction":
            compaction = entry

    if compaction is None:
        return path

    compaction_idx = next((i for i, e in enumerate(path) if e.id == compaction.id), -1)
    if compaction_idx < 0:
        return path

    context_entries: List[SessionEntry] = [compaction]
    found_first_kept = False
    for i in range(compaction_idx):
        entry = path[i]
        if entry.id == compaction.first_kept_entry_id:
            found_first_kept = True
        if found_first_kept:
            context_entries.append(entry)
    context_entries.extend(path[compaction_idx + 1 :])
    return context_entries


def build_session_context(
    entries: List[SessionEntry],
    leaf_id: Any = _USE_LAST_ENTRY,
    by_id: Optional[Dict[str, SessionEntry]] = None,
) -> SessionContext:
    """构建会话上下文（对齐 TS buildSessionContext）。"""
    path = _build_session_path(entries, leaf_id, by_id)
    thinking_level, model = _get_session_context_settings(path)

    messages: List[AgentMessage] = []
    for entry in build_context_entries(entries, leaf_id, by_id):
        messages.extend(session_entry_to_context_messages(entry))

    return SessionContext(
        messages=messages,
        thinking_level=thinking_level,
        model=model,
    )


# ============================================================================
# 会话目录与文件
# ============================================================================


def get_default_session_dir_path(cwd: str) -> str:
    """计算默认会话目录路径（纯计算，不创建目录，对齐 TS getDefaultSessionDirPath）。"""
    cleaned_cwd = (
        os.path.abspath(os.path.expanduser(cwd))
        .lstrip("/\\")
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
    )
    return os.path.join(get_agent_dir(), SESSIONS_DIR_NAME, f"--{cleaned_cwd}--")


def get_default_session_dir(cwd: str) -> str:
    """获取默认会话目录（不存在则创建）。"""
    session_dir = get_default_session_dir_path(cwd)
    if not os.path.exists(session_dir):
        os.makedirs(session_dir, exist_ok=True)
    return session_dir


def load_entries_from_file(file_path: str) -> List[FileEntry]:
    """从文件流式加载条目（逐行解析，不完整读入内存）。"""
    if not os.path.exists(file_path):
        return []

    entries: List[FileEntry] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            entry = parse_session_entry_line(line)
            if entry is not None:
                entries.append(entry)

    # 验证会话头部
    if not entries:
        return entries
    header = entries[0]
    if not isinstance(header, SessionHeader) or not header.id:
        return []

    return entries


def _read_session_header(file_path: str) -> Optional[Dict[str, Any]]:
    """读取会话文件首行的 header（对齐 TS readSessionHeader）。"""
    try:
        with open(file_path, "rb") as f:
            first_line = f.readline().decode("utf-8").strip()
        if not first_line:
            return None
        header = json.loads(first_line)
        if header.get("type") != "session" or not isinstance(header.get("id"), str):
            return None
        return header
    except Exception:
        return None


def is_valid_session_file(file_path: str) -> bool:
    """检查是否为有效的会话文件"""
    return _read_session_header(file_path) is not None


def find_most_recent_session(
    session_dir: str, cwd: Optional[str] = None
) -> Optional[str]:
    """查找最近的会话；提供 cwd 时只考虑该 cwd 下创建的会话
    （对齐 TS findMostRecentSession 的 cwd 过滤）。"""
    resolved_cwd = os.path.abspath(os.path.expanduser(cwd)) if cwd is not None else None
    try:
        files = []
        for f in os.listdir(session_dir):
            if not f.endswith(".jsonl"):
                continue
            path = os.path.join(session_dir, f)
            header = _read_session_header(path)
            if header is None:
                continue
            if resolved_cwd is not None:
                header_cwd = header.get("cwd")
                if not header_cwd or (
                    os.path.abspath(os.path.expanduser(header_cwd)) != resolved_cwd
                ):
                    continue
            files.append((path, os.path.getmtime(path)))
        files.sort(key=lambda x: x[1], reverse=True)
        return files[0][0] if files else None
    except Exception:
        return None


# ============================================================================
# 会话信息提取（SessionInfo 构建的辅助）
# ============================================================================


def message_activity_time(entry: FileEntry) -> Optional[float]:
    """单条消息条目的活动时间（epoch 毫秒，对齐 TS getMessageActivityTime）。"""
    if entry.type != "message":
        return None
    message = entry.message
    if message.role not in ("user", "assistant"):
        return None

    # 消息自带的时间戳是 epoch 毫秒
    if isinstance(message.timestamp, (int, float)):
        return float(message.timestamp)

    # 条目时间戳是 ISO 字符串，统一转毫秒
    if isinstance(entry.timestamp, str):
        try:
            return datetime.fromisoformat(entry.timestamp).timestamp() * 1000
        except ValueError:
            return None
    return None


def get_last_activity_time(entries: List[FileEntry]) -> Optional[float]:
    """获取最后活动时间（统一为 epoch 毫秒，对齐 TS new Date(ts).getTime()）。"""
    last_time: Optional[float] = None
    for entry in entries:
        t = message_activity_time(entry)
        if t is not None:
            last_time = max(last_time or 0, t)
    return last_time
