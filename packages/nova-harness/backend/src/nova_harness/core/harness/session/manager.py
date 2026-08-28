"""
Session Manager - Core session management class

管理 JSONL 追加式会话树：每个 entry 带 id/parent_id 构成树，
leaf 指针（纯内存）追踪当前位置，branch 通过移动 leaf 实现，
build_session_context 负责把压缩/分支摘要解析为 LLM 上下文。
"""

import asyncio
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from nova_agent import CustomAgentMessage
from nova_ai import ImageContent, Message, ModelThinkingLevel, TextContent
from pydantic import BaseModel

from nova_harness.core.config.defaults import get_sessions_dir
from nova_harness.core.harness.session.listing import (
    MAX_CONCURRENT_SESSION_INFO_LOADS,
    build_session_info,
    list_sessions_from_dir,
)
from nova_harness.core.harness.session.utils import (
    assert_valid_session_id,
    build_context_entries,
    build_session_context,
    find_most_recent_session,
    generate_id,
    generate_session_id,
    get_default_session_dir,
    get_default_session_dir_path,
    load_entries_from_file,
    now_iso,
)
from nova_harness.core.types.session import (
    CURRENT_SESSION_VERSION,
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    FileEntry,
    LabelEntry,
    ModelChangeEntry,
    SessionContext,
    SessionEntry,
    SessionHeader,
    SessionInfo,
    SessionInfoEntry,
    SessionMessageEntry,
    SessionTreeNode,
    ThinkingLevelChangeEntry,
)

# details 字段允许写入的 JSON 原生类型
_JSON_NATIVE_TYPES = (dict, list, str, int, float, bool)


def _normalize_details(details: Any) -> Any:
    """把 entry.details 归一化为 JSON 可序列化表示。

    内存中、JSONL 落盘、会话重载三个环节的 details 统一为 dict/基本类型，
    读取方无需再兼容 pydantic 实例与 dict 两种形态：

    - None / dict / list / str / int / float / bool：原样放行；
    - pydantic 模型：``model_dump(mode="json")`` 转 dict；
    - 其他对象：抛 TypeError（此类对象落盘时 json.dumps 必然失败，
      提前到写入关口报错，信息更明确）。
    """
    if details is None or isinstance(details, _JSON_NATIVE_TYPES):
        return details
    if isinstance(details, BaseModel):
        return details.model_dump(mode="json")
    raise TypeError(
        "entry.details 必须是 JSON 可序列化类型"
        f"（dict/list/基本类型或 pydantic 模型），得到 {type(details).__name__}"
    )


def entry_to_json(entry: FileEntry) -> str:
    """把条目序列化为 JSONL 行（对齐 TS ``JSON.stringify`` 的输出形状）。

    - 紧凑分隔符（无空格），与 TS 逐字节一致；
    - ``exclude_none=True``：None 字段省略键（对齐 TS undefined 键不落盘）。
    """
    return json.dumps(
        entry.model_dump(exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )


class SessionManager:
    """会话管理器"""

    def __init__(
        self,
        cwd: str,
        session_dir: str,
        session_file: Optional[str],
        persist: bool,
        session_id: Optional[str] = None,
        parent_session: Optional[str] = None,
    ):
        self._cwd = os.path.abspath(os.path.expanduser(cwd))
        self._session_dir = (
            os.path.abspath(os.path.expanduser(session_dir))
            if session_dir
            else session_dir
        )
        self._persist = persist
        self._session_id = ""
        self._session_file: Optional[str] = None
        self._flushed = False
        self._file_entries: List[FileEntry] = []
        self._by_id: Dict[str, SessionEntry] = {}
        self._labels_by_id: Dict[str, str] = {}
        self._label_timestamps_by_id: Dict[str, str] = {}
        self._leaf_id: Optional[str] = None

        if persist and session_dir and not os.path.exists(session_dir):
            os.makedirs(session_dir, exist_ok=True)

        if session_file:
            self.set_session_file(session_file)
        else:
            self.new_session(session_id=session_id, parent_session=parent_session)

    def set_session_file(self, session_file: str) -> None:
        """设置会话文件"""
        self._session_file = os.path.abspath(os.path.expanduser(session_file))
        if os.path.exists(self._session_file):
            self._file_entries = load_entries_from_file(self._session_file)

            # 空文件用新 header 初始化；非空但无法解析的文件抛错保护数据
            # （对齐 TS：不静默覆盖损坏的会话文件）
            if not self._file_entries:
                explicit_path = self._session_file
                if os.path.getsize(explicit_path) > 0:
                    raise ValueError(
                        f"Session file is not a valid session: {explicit_path}"
                    )
                self.new_session()
                self._session_file = explicit_path
                self._rewrite_file()
                self._flushed = True
                return

            header = next(
                (e for e in self._file_entries if isinstance(e, SessionHeader)), None
            )
            self._session_id = header.id if header else generate_session_id()

            self._build_index()
            self._flushed = True
        else:
            explicit_path = self._session_file
            self.new_session()
            self._session_file = explicit_path

    def new_session(
        self,
        session_id: Optional[str] = None,
        parent_session: Optional[str] = None,
    ) -> Optional[str]:
        """创建新会话；session_id 可自定义（需通过格式校验）。"""
        if session_id is not None:
            assert_valid_session_id(session_id)
        self._session_id = session_id or generate_session_id()
        timestamp = now_iso()
        header = SessionHeader(
            type="session",
            version=CURRENT_SESSION_VERSION,
            id=self._session_id,
            timestamp=timestamp,
            cwd=self._cwd,
            parent_session=parent_session,
        )
        self._file_entries = [header]
        self._by_id.clear()
        self._labels_by_id.clear()
        self._label_timestamps_by_id.clear()
        self._leaf_id = None
        self._flushed = False

        if self._persist:
            file_timestamp = timestamp.replace(":", "-").replace(".", "-")
            self._session_file = os.path.join(
                self.get_session_dir(), f"{file_timestamp}_{self._session_id}.jsonl"
            )
        return self._session_file

    def _build_index(self) -> None:
        """构建索引；leaf 恢复为最后一条 entry（对齐 TS）。"""
        self._by_id.clear()
        self._labels_by_id.clear()
        self._label_timestamps_by_id.clear()
        self._leaf_id = None

        for entry in self._file_entries:
            if entry.type == "session":
                continue
            self._by_id[entry.id] = entry
            self._leaf_id = entry.id
            if entry.type == "label":
                if entry.label:
                    self._labels_by_id[entry.target_id] = entry.label
                    self._label_timestamps_by_id[entry.target_id] = entry.timestamp
                else:
                    self._labels_by_id.pop(entry.target_id, None)
                    self._label_timestamps_by_id.pop(entry.target_id, None)

    def _rewrite_file(self) -> None:
        """重写文件"""
        if not self._persist or not self._session_file:
            return
        with open(self._session_file, "w", encoding="utf-8") as f:
            for entry in self._file_entries:
                f.write(entry_to_json(entry) + "\n")

    def _persist_entry(self, entry: SessionEntry) -> None:
        """持久化条目（对齐 TS _persist 的 flushed 语义）。

        文件未创建时推迟到首个 assistant 消息出现；一旦文件已存在
        （flushed），后续所有 entry 立即追加，保证用户消息不丢。
        """
        if not self._persist or not self._session_file:
            return

        has_assistant = any(
            e.type == "message" and e.message.role == "assistant"
            for e in self._file_entries
        )

        if not has_assistant:
            if self._flushed:
                with open(self._session_file, "a", encoding="utf-8") as f:
                    f.write(entry_to_json(entry) + "\n")
            else:
                # 标记为未落盘，等 assistant 到来时全量写出
                self._flushed = False
            return

        if not self._flushed:
            # 首写：独占创建（对齐 TS "wx"），防并发进程覆盖
            with open(self._session_file, "x", encoding="utf-8") as f:
                for e in self._file_entries:
                    f.write(entry_to_json(e) + "\n")
            self._flushed = True
        else:
            with open(self._session_file, "a", encoding="utf-8") as f:
                f.write(entry_to_json(entry) + "\n")

    def _append_entry(self, entry: SessionEntry) -> None:
        """追加条目"""
        self._file_entries.append(entry)
        self._by_id[entry.id] = entry
        self._leaf_id = entry.id
        self._persist_entry(entry)

    # ========================================================================
    # 公共API
    # ========================================================================

    def is_persisted(self) -> bool:
        return self._persist

    def get_cwd(self) -> str:
        return self._cwd

    def get_session_dir(self) -> str:
        return self._session_dir

    def uses_default_session_dir(self) -> bool:
        """会话目录是否为按 cwd 计算的默认目录（对齐 TS usesDefaultSessionDir）。"""
        return self._session_dir == get_default_session_dir_path(self._cwd)

    def get_session_id(self) -> str:
        return self._session_id

    def get_session_file(self) -> Optional[str]:
        return self._session_file

    def append_message(self, message: Union[Message, CustomAgentMessage]) -> str:
        """追加消息（compaction/branch 摘要须走 append_compaction/branch_with_summary）。

        用户工具消息（如 bashExecution）以 CustomAgentMessage 子类实例传入，
        经 ``SerializeAsAny`` 按自身 schema 序列化。
        """
        entry = SessionMessageEntry(
            type="message",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=now_iso(),
            message=message,
        )
        self._append_entry(entry)
        return entry.id

    def append_thinking_level_change(
        self, thinking_level: Optional[ModelThinkingLevel] = None
    ) -> str:
        """追加思考级别变更"""
        entry = ThinkingLevelChangeEntry(
            type="thinking_level_change",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=now_iso(),
            thinking_level=thinking_level,
        )
        self._append_entry(entry)
        return entry.id

    def append_model_change(self, provider: str, model_id: str) -> str:
        """追加模型变更"""
        entry = ModelChangeEntry(
            type="model_change",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=now_iso(),
            provider=provider,
            model_id=model_id,
        )
        self._append_entry(entry)
        return entry.id

    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Optional[Any] = None,
        from_hook: Optional[bool] = None,
    ) -> CompactionEntry:
        """追加压缩条目，返回落盘的 CompactionEntry。"""
        entry = CompactionEntry(
            type="compaction",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=now_iso(),
            summary=summary,
            first_kept_entry_id=first_kept_entry_id,
            tokens_before=tokens_before,
            details=_normalize_details(details),
            from_hook=from_hook,
        )
        self._append_entry(entry)
        return entry

    def append_custom_entry(self, custom_type: str, data: Optional[Any] = None) -> str:
        """追加自定义条目"""
        entry = CustomEntry(
            type="custom",
            custom_type=custom_type,
            data=_normalize_details(data),
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=now_iso(),
        )
        self._append_entry(entry)
        return entry.id

    def append_session_info(self, name: str) -> str:
        """追加会话信息（换行替换为空格，对齐 TS）"""
        sanitized = re.sub(r"[\r\n]+", " ", name).strip()
        entry = SessionInfoEntry(
            type="session_info",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=now_iso(),
            name=sanitized,
        )
        self._append_entry(entry)
        return entry.id

    def get_session_name(self) -> Optional[str]:
        """获取会话名称（最新一条 session_info 决定，空名表示显式清除）"""
        for entry in reversed(self.get_entries()):
            if entry.type == "session_info":
                name = entry.name.strip() if entry.name else ""
                return name or None
        return None

    def append_custom_message_entry(
        self,
        custom_type: str,
        content: Union[str, List[Union[TextContent, ImageContent]]],
        display: bool = True,
        details: Optional[Any] = None,
    ) -> str:
        """追加自定义消息条目"""
        entry = CustomMessageEntry(
            type="custom_message",
            custom_type=custom_type,
            content=content,
            display=display,
            details=_normalize_details(details),
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=now_iso(),
        )
        self._append_entry(entry)
        return entry.id

    def get_leaf_id(self) -> Optional[str]:
        return self._leaf_id

    def get_leaf_entry(self) -> Optional[SessionEntry]:
        return self._by_id.get(self._leaf_id) if self._leaf_id else None

    def get_entry(self, entry_id: str) -> Optional[SessionEntry]:
        return self._by_id.get(entry_id)

    def get_children(self, parent_id: str) -> List[SessionEntry]:
        """获取子条目"""
        return [e for e in self._by_id.values() if e.parent_id == parent_id]

    def get_label(self, entry_id: str) -> Optional[str]:
        """获取标签"""
        return self._labels_by_id.get(entry_id)

    def append_label_change(self, target_id: str, label: Optional[str]) -> str:
        """追加标签变更"""
        if target_id not in self._by_id:
            raise ValueError(f"Entry {target_id} not found")

        entry = LabelEntry(
            type="label",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=now_iso(),
            target_id=target_id,
            label=label,
        )
        self._append_entry(entry)
        if label:
            self._labels_by_id[target_id] = label
            self._label_timestamps_by_id[target_id] = entry.timestamp
        else:
            self._labels_by_id.pop(target_id, None)
            self._label_timestamps_by_id.pop(target_id, None)
        return entry.id

    def get_branch(self, from_id: Optional[str] = None) -> List[SessionEntry]:
        """获取分支路径"""
        path = []
        start_id = from_id if from_id is not None else self._leaf_id
        current = self._by_id.get(start_id) if start_id else None

        while current:
            path.insert(0, current)
            current = self._by_id.get(current.parent_id) if current.parent_id else None

        return path

    def build_context_entries(self) -> List[SessionEntry]:
        """构建压缩感知的活动条目列表（对齐 TS buildContextEntries）。"""
        return build_context_entries(self.get_entries(), self._leaf_id, self._by_id)

    def build_session_context(self) -> SessionContext:
        """构建会话上下文"""
        return build_session_context(self.get_entries(), self._leaf_id, self._by_id)

    def get_header(self) -> Optional[SessionHeader]:
        """获取头部"""
        return next(
            (e for e in self._file_entries if isinstance(e, SessionHeader)), None
        )

    def get_entries(self) -> List[SessionEntry]:
        """获取所有条目（不含 header）"""
        return [e for e in self._file_entries if not isinstance(e, SessionHeader)]

    def get_tree(self) -> List[SessionTreeNode]:
        """获取树结构"""
        from nova_harness.core.harness.session.builders import build_session_tree

        return build_session_tree(
            self.get_entries(), self._labels_by_id, self._label_timestamps_by_id
        )

    def branch(self, branch_from_id: str) -> None:
        """分支到指定条目（仅移动内存中的 leaf 指针，对齐 TS）。"""
        if branch_from_id not in self._by_id:
            raise ValueError(f"Entry {branch_from_id} not found")
        self._leaf_id = branch_from_id

    def reset_leaf(self) -> None:
        """重置 leaf 指针到 None（下次 append 产生新的根，对齐 TS resetLeaf）。"""
        self._leaf_id = None

    def branch_with_summary(
        self,
        branch_from_id: Optional[str],
        summary: str,
        details: Optional[Any] = None,
        from_hook: Optional[bool] = None,
    ) -> str:
        """带摘要的分支"""
        if branch_from_id is not None and branch_from_id not in self._by_id:
            raise ValueError(f"Entry {branch_from_id} not found")

        self._leaf_id = branch_from_id
        entry = BranchSummaryEntry(
            type="branch_summary",
            id=generate_id(set(self._by_id.keys())),
            parent_id=branch_from_id,
            timestamp=now_iso(),
            from_id=branch_from_id or "root",
            summary=summary,
            details=_normalize_details(details),
            from_hook=from_hook,
        )
        self._append_entry(entry)
        return entry.id

    def create_branched_session(self, leaf_id: str) -> Optional[str]:
        """创建分支会话"""
        from nova_harness.core.harness.session.builders import (
            create_branched_session_entries,
        )

        previous_session_file = self._session_file
        path = self.get_branch(leaf_id)
        if not path:
            raise ValueError(f"Entry {leaf_id} not found")

        # 收集路径上的标签（保留原时间戳，对齐 TS）
        path_entry_ids = {e.id for e in path if e.type != "label"}
        labels_to_write: List[Tuple[str, str, str]] = [
            (target_id, label, self._label_timestamps_by_id[target_id])
            for target_id, label in self._labels_by_id.items()
            if target_id in path_entry_ids
        ]

        # 同一时间戳用于新会话 header 与文件名（对齐 TS createBranchedSession）
        timestamp = now_iso()

        file_entries, new_session_id = create_branched_session_entries(
            self._cwd,
            previous_session_file if self._persist else None,
            path,
            labels_to_write,
            timestamp,
        )

        if self._persist:
            file_timestamp = timestamp.replace(":", "-").replace(".", "-")
            new_session_file = os.path.join(
                self.get_session_dir(), f"{file_timestamp}_{new_session_id}.jsonl"
            )
            self._file_entries = file_entries
            self._session_id = new_session_id
            self._session_file = new_session_file
            self._build_index()

            # 只在包含 assistant 消息时立即写文件，否则交给 _persist_entry
            # 延迟创建（对齐 TS createBranchedSession 的 newSession 契约）
            has_assistant = any(
                e.type == "message" and e.message.role == "assistant"
                for e in self._file_entries
            )
            if has_assistant:
                self._rewrite_file()
                self._flushed = True
            else:
                self._flushed = False

            return new_session_file

        # 内存模式
        self._file_entries = file_entries
        self._session_id = new_session_id
        self._build_index()
        return None

    # ========================================================================
    # 静态工厂方法
    # ========================================================================

    @classmethod
    def create(
        cls,
        cwd: str,
        session_dir: Optional[str] = None,
        session_id: Optional[str] = None,
        parent_session: Optional[str] = None,
    ) -> "SessionManager":
        """创建新会话"""
        dir_path = (
            session_dir if session_dir is not None else get_default_session_dir(cwd)
        )
        return cls(cwd, dir_path, None, True, session_id, parent_session)

    @classmethod
    def open(
        cls,
        path: str,
        session_dir: Optional[str] = None,
        cwd_override: Optional[str] = None,
    ) -> "SessionManager":
        """打开指定会话"""
        resolved_path = os.path.abspath(os.path.expanduser(path))
        entries = load_entries_from_file(resolved_path)
        header = next((e for e in entries if isinstance(e, SessionHeader)), None)
        cwd = (
            cwd_override
            if cwd_override is not None
            else (header.cwd if header and header.cwd else os.getcwd())
        )
        dir_path = (
            session_dir
            if session_dir is not None
            else os.path.abspath(os.path.dirname(resolved_path))
        )
        return cls(cwd, dir_path, resolved_path, True)

    @classmethod
    def continue_recent(
        cls, cwd: str, session_dir: Optional[str] = None
    ) -> "SessionManager":
        """继续最近的会话；自定义 session_dir 且非默认目录时按 cwd 过滤（对齐 TS）。"""
        dir_path = (
            session_dir if session_dir is not None else get_default_session_dir(cwd)
        )
        filter_cwd = session_dir is not None and os.path.abspath(
            os.path.expanduser(dir_path)
        ) != get_default_session_dir_path(cwd)
        most_recent = find_most_recent_session(dir_path, cwd if filter_cwd else None)
        if most_recent:
            return cls(cwd, dir_path, most_recent, True)
        return cls(cwd, dir_path, None, True)

    @classmethod
    def in_memory(
        cls,
        cwd: str = "",
        session_id: Optional[str] = None,
        parent_session: Optional[str] = None,
    ) -> "SessionManager":
        """创建内存会话"""
        if not cwd:
            cwd = os.getcwd()
        return cls(cwd, "", None, False, session_id, parent_session)

    @classmethod
    async def list_sessions(
        cls,
        cwd: str,
        session_dir: Optional[str] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> List[SessionInfo]:
        """列出会话；自定义 session_dir 且非默认目录时按 cwd 过滤（对齐 TS）。"""
        dir_path = (
            session_dir if session_dir is not None else get_default_session_dir(cwd)
        )
        filter_cwd = session_dir is not None and os.path.abspath(
            os.path.expanduser(dir_path)
        ) != get_default_session_dir_path(cwd)
        resolved_cwd = os.path.abspath(os.path.expanduser(cwd))
        sessions = await list_sessions_from_dir(dir_path, on_progress)
        if filter_cwd:
            sessions = [
                s
                for s in sessions
                if s.cwd and os.path.abspath(os.path.expanduser(s.cwd)) == resolved_cwd
            ]
        sessions.sort(key=lambda s: s.modified, reverse=True)
        return sessions

    @classmethod
    async def list_all_sessions(
        cls,
        session_dir: Optional[str] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> List[SessionInfo]:
        """列出所有项目的会话；提供 session_dir 时只列该目录（对齐 TS listAll）。"""
        if session_dir is not None:
            sessions = await list_sessions_from_dir(
                os.path.abspath(os.path.expanduser(session_dir)), on_progress
            )
            sessions.sort(key=lambda s: s.modified, reverse=True)
            return sessions

        sessions_dir = str(get_sessions_dir())

        try:
            if not os.path.exists(sessions_dir):
                return []

            dirs = [
                os.path.join(sessions_dir, d)
                for d in os.listdir(sessions_dir)
                if os.path.isdir(os.path.join(sessions_dir, d))
            ]

            # 先统计文件总数用于进度回调
            total_files = 0
            all_files: List[str] = []
            for dir_path in dirs:
                try:
                    files = [
                        os.path.join(dir_path, f)
                        for f in os.listdir(dir_path)
                        if f.endswith(".jsonl")
                    ]
                    all_files.extend(files)
                    total_files += len(files)
                except Exception:
                    continue

            semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSION_INFO_LOADS)
            loaded = 0
            sessions: List[SessionInfo] = []

            async def load_one(file: str) -> Optional[SessionInfo]:
                nonlocal loaded
                info = await build_session_info(file, semaphore)
                loaded += 1
                if on_progress:
                    on_progress(loaded, total_files)
                return info

            tasks = [asyncio.create_task(load_one(f)) for f in all_files]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, SessionInfo):
                    sessions.append(result)

            sessions.sort(key=lambda s: s.modified, reverse=True)
            return sessions
        except Exception:
            return []

    @classmethod
    def fork_from(
        cls,
        source_path: str,
        target_cwd: str,
        session_dir: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> "SessionManager":
        """从源会话 fork 新会话（完整历史复制到新 cwd 的会话目录）。

        复制走**行级原文**：类型化加载会丢弃未知类型的条目（校验式解析的
        取舍），而 fork 的语义是文件复制——合法 JSON 行原样保留（对齐 TS
        fork 后未知条目不丢），非法 JSON 行丢弃。先做一次类型化加载仅为
        验证源文件合法性（空文件/无 header 抛错）。
        """
        resolved_source = os.path.abspath(os.path.expanduser(source_path))
        source_entries = load_entries_from_file(resolved_source)
        if not source_entries:
            raise ValueError(
                f"Cannot fork: source session file is empty or invalid: {resolved_source}"
            )

        source_header = next(
            (e for e in source_entries if isinstance(e, SessionHeader)), None
        )
        if not source_header:
            raise ValueError(
                f"Cannot fork: source session has no header: {resolved_source}"
            )

        dir_path = (
            session_dir
            if session_dir is not None
            else get_default_session_dir(target_cwd)
        )
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        if session_id is not None:
            assert_valid_session_id(session_id)
        new_session_id = session_id or generate_session_id()
        timestamp = now_iso()
        file_timestamp = timestamp.replace(":", "-").replace(".", "-")
        new_session_file = os.path.join(
            dir_path, f"{file_timestamp}_{new_session_id}.jsonl"
        )

        # 新 header 指向源会话（parent），独占创建防覆盖（对齐 TS "wx"）
        new_header = SessionHeader(
            type="session",
            version=CURRENT_SESSION_VERSION,
            id=new_session_id,
            timestamp=timestamp,
            cwd=os.path.abspath(os.path.expanduser(target_cwd)),
            parent_session=resolved_source,
        )
        with open(new_session_file, "x", encoding="utf-8") as f:
            f.write(entry_to_json(new_header) + "\n")
            # 行级原文复制非 header 行：合法 JSON 原样保留（含未知类型条目）
            with open(resolved_source, "r", encoding="utf-8") as src:
                for line in src:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        raw = json.loads(stripped)
                    except ValueError:
                        continue
                    if isinstance(raw, dict) and raw.get("type") == "session":
                        continue
                    f.write(stripped + "\n")

        return cls(target_cwd, dir_path, new_session_file, True)
