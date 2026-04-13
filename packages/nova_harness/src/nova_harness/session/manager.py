"""
Session Manager - Core session management class
"""

from functools import partial
import os
import json
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any, Union, Callable

from nova_ai import Message, TextContent, ImageContent, ThinkingLevel
from .types import (
    AgentToFrontendEntry, FrontendToAgentEntry, SessionHeader, SessionEntry, FileEntry, SessionTreeNode,
    SessionContext, SessionInfo, SessionMessageEntry,
    ThinkingLevelChangeEntry, ModelChangeEntry, CompactionEntry,
    BranchSummaryEntry, CustomEntry, CustomMessageEntry,
    LabelEntry, SessionInfoEntry
)
from .constants import CURRENT_SESSION_VERSION
from .utils import (
    generate_id, load_entries_from_file, get_default_session_dir,
    find_most_recent_session, build_session_context
)
from .models import list_sessions_from_dir, build_session_info
from ..messages import (
    BashExecutionMessage, CustomMessage, FileContent
)
from ..config import get_sessions_dir


class SessionManager:
    """会话管理器"""
    
    def __init__(self, cwd: str, session_dir: str, session_file: Optional[str], persist: bool):
        self._cwd = cwd
        self._session_dir = session_dir
        self._persist = persist
        self._session_id = ""
        self._session_file: Optional[str] = None
        self._flushed = False
        self._file_entries: List[FileEntry] = []
        self._by_id: Dict[str, SessionEntry] = {}
        self._labels_by_id: Dict[str, str] = {}
        self._leaf_id: Optional[str] = None
        
        if persist and session_dir and not os.path.exists(session_dir):
            os.makedirs(session_dir, exist_ok=True)
        
        if session_file:
            self.set_session_file(session_file)
        else:
            self.new_session()
    
    def set_session_file(self, session_file: str) -> None:
        """设置会话文件"""
        self._session_file = os.path.abspath(session_file)
        if os.path.exists(self._session_file):
            self._file_entries = load_entries_from_file(self._session_file)
            
            # 如果文件为空或损坏，重新开始
            if not self._file_entries:
                explicit_path = self._session_file
                self.new_session()
                self._session_file = explicit_path
                self._rewrite_file()
                self._flushed = True
                return
            
            header = next((e for e in self._file_entries if isinstance(e, SessionHeader)), None)
            self._session_id = header.id if header else uuid.uuid4().hex
            
            self._build_index()
            self._flushed = True
        else:
            explicit_path = self._session_file
            self.new_session()
            self._session_file = explicit_path
    
    def new_session(self, parent_session: Optional[str] = None) -> Optional[str]:
        """创建新会话"""
        self._session_id = uuid.uuid4().hex
        timestamp = datetime.now().isoformat()
        header = SessionHeader(
            type="session",
            version=CURRENT_SESSION_VERSION,
            id=self._session_id,
            timestamp=timestamp,
            cwd=self._cwd,
            parent_session=parent_session
        )
        self._file_entries = [header]
        self._by_id.clear()
        self._labels_by_id.clear()
        self._leaf_id = None
        self._flushed = False
        
        if self._persist:
            file_timestamp = timestamp.replace(':', '-').replace('.', '-')
            self._session_file = os.path.join(self.get_session_dir(), 
                                             f"{file_timestamp}_{self._session_id}.jsonl")
        return self._session_file
    
    def _build_index(self) -> None:
        """构建索引"""
        self._by_id.clear()
        self._labels_by_id.clear()
        self._leaf_id = None
        
        for entry in self._file_entries:
            if entry.type == "session":
                continue
            self._by_id[entry.id] = entry
            self._leaf_id = entry.id
            if entry.type == "label":
                if entry.label:
                    self._labels_by_id[entry.target_id] = entry.label
                else:
                    self._labels_by_id.pop(entry.target_id, None)
    
    def _rewrite_file(self) -> None:
        """重写文件"""
        if not self._persist or not self._session_file:
            return
        with open(self._session_file, 'w', encoding='utf-8') as f:
            for entry in self._file_entries:
                f.write(self._entry_to_json(entry) + '\n')
    
    def _entry_to_json(self, entry: FileEntry) -> str:
        """将条目转换为JSON字符串"""
        encoder = partial(json.dumps, ensure_ascii=False)
        return entry.to_json(encoder=encoder)
    
    def _persist_entry(self, entry: SessionEntry) -> None:
        """持久化条目"""
        if not self._persist or not self._session_file:
            return
        
        has_assistant = any(
            e.type == "message" and hasattr(e.message, 'role') and e.message.role == "assistant"
            for e in self._file_entries
        )
        
        if not has_assistant:
            self._flushed = False
            return
        
        if not self._flushed:
            with open(self._session_file, 'w', encoding='utf-8') as f:
                for e in self._file_entries:
                    f.write(self._entry_to_json(e) + '\n')
            self._flushed = True
        else:
            with open(self._session_file, 'a', encoding='utf-8') as f:
                f.write(self._entry_to_json(entry) + '\n')
    
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
    
    def get_session_id(self) -> str:
        return self._session_id
    
    def get_session_file(self) -> Optional[str]:
        return self._session_file
    
    def append_message(self, message: Union[Message, CustomMessage, BashExecutionMessage]) -> str:
        """追加消息"""
        entry = SessionMessageEntry(
            type="message",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat(),
            message=message
        )
        self._append_entry(entry)
        return entry.id
    
    def append_frontend_to_agent_message(
            self,
            content: Union[str, List[Union[TextContent, ImageContent, FileContent]]],
            display: bool = True,
        ) -> str:
        """追加前端的消息"""
        entry = FrontendToAgentEntry(
            type="frontend_to_agent",
            content=content,
            display=display,
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat()
        )
        self._append_entry(entry)
        return entry.id
    
    def append_agent_to_frontend_message(
            self,
            content: Union[str, List[Union[TextContent, ImageContent, FileContent]]],
            display: bool = True,
        ) -> str:
        """追加前端的消息"""
        entry = AgentToFrontendEntry(
            type="agent_to_frontend",
            content=content,
            display=display,
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat()
        )
        self._append_entry(entry)
        return entry.id
    
    def append_thinking_level_change(self, thinking_level: Optional[ThinkingLevel] = None) -> str:
        """追加思考级别变更"""
        entry = ThinkingLevelChangeEntry(
            type="thinking_level_change",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat(),
            thinking_level=thinking_level
        )
        self._append_entry(entry)
        return entry.id
    
    def append_model_change(self, provider: str, model_id: str) -> str:
        """追加模型变更"""
        entry = ModelChangeEntry(
            type="model_change",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat(),
            provider=provider,
            model_id=model_id
        )
        self._append_entry(entry)
        return entry.id
    
    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Optional[Any] = None,
        from_hook: bool = False
    ) -> str:
        """追加压缩条目"""
        entry = CompactionEntry(
            type="compaction",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat(),
            summary=summary,
            first_kept_entry_id=first_kept_entry_id,
            tokens_before=tokens_before,
            details=details,
            from_hook=from_hook
        )
        self._append_entry(entry)
        return entry.id
    
    def append_custom_entry(self, custom_type: str, data: Optional[Any] = None) -> str:
        """追加自定义条目"""
        entry = CustomEntry(
            type="custom",
            custom_type=custom_type,
            data=data,
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat()
        )
        self._append_entry(entry)
        return entry.id
    
    def append_session_info(self, name: str) -> str:
        """追加会话信息"""
        entry = SessionInfoEntry(
            type="session_info",
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat(),
            name=name.strip()
        )
        self._append_entry(entry)
        return entry.id
    
    def get_session_name(self) -> Optional[str]:
        """获取会话名称"""
        entries = self.get_entries()
        for entry in reversed(entries):
            if entry.type == "session_info" and entry.name:
                return entry.name
        return None
    
    def append_custom_message_entry(
        self,
        custom_type: str,
        content: Union[str, List[Union[TextContent, ImageContent]]],
        display: bool = True,
        details: Optional[Any] = None
    ) -> str:
        """追加自定义消息条目"""
        entry = CustomMessageEntry(
            type="custom_message",
            custom_type=custom_type,
            content=content,
            display=display,
            details=details,
            id=generate_id(set(self._by_id.keys())),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat()
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
            timestamp=datetime.now().isoformat(),
            target_id=target_id,
            label=label
        )
        self._append_entry(entry)
        if label:
            self._labels_by_id[target_id] = label
        else:
            self._labels_by_id.pop(target_id, None)
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
    
    def build_session_context(self) -> SessionContext:
        """构建会话上下文"""
        return build_session_context(self.get_entries(), self._leaf_id, self._by_id)
    
    def get_header(self) -> Optional[SessionHeader]:
        """获取头部"""
        h = next((e for e in self._file_entries if isinstance(e, SessionHeader)), None)
        return h
    
    def get_entries(self) -> List[SessionEntry]:
        """获取所有条目"""
        return [e for e in self._file_entries if not isinstance(e, SessionHeader)]
    
    def get_tree(self) -> List[SessionTreeNode]:
        """获取树结构"""
        from .builders import build_session_tree
        return build_session_tree(self.get_entries(), self._labels_by_id)
    
    def branch(self, branch_from_id: str) -> None:
        """分支到指定条目"""
        if branch_from_id not in self._by_id:
            raise ValueError(f"Entry {branch_from_id} not found")
        self._leaf_id = branch_from_id
    
    def reset_leaf(self) -> None:
        """重置叶子节点"""
        self._leaf_id = None
    
    def branch_with_summary(
        self,
        branch_from_id: Optional[str],
        summary: str,
        details: Optional[Any] = None,
        from_hook: bool = False
    ) -> str:
        """带摘要的分支"""
        if branch_from_id is not None and branch_from_id not in self._by_id:
            raise ValueError(f"Entry {branch_from_id} not found")
        
        self._leaf_id = branch_from_id
        entry = BranchSummaryEntry(
            type="branch_summary",
            id=generate_id(set(self._by_id.keys())),
            parent_id=branch_from_id,
            timestamp=datetime.now().isoformat(),
            from_id=branch_from_id or "root",
            summary=summary,
            details=details,
            from_hook=from_hook
        )
        self._append_entry(entry)
        return entry.id
    
    def create_branched_session(self, leaf_id: str) -> Optional[str]:
        """创建分支会话"""
        from .builders import create_branched_session_entries
        
        previous_session_file = self._session_file
        path = self.get_branch(leaf_id)
        if not path:
            raise ValueError(f"Entry {leaf_id} not found")
        
        # 过滤标签条目
        path_without_labels = [e for e in path if e.type != "label"]
        
        # 收集标签
        path_entry_ids = {e.id for e in path_without_labels}
        labels_to_write = []
        for target_id, label in self._labels_by_id.items():
            if target_id in path_entry_ids:
                labels_to_write.append((target_id, label))
        
        if self._persist:
            new_session_id = uuid.uuid4().hex
            timestamp = datetime.now().isoformat()
            file_timestamp = timestamp.replace(':', '-').replace('.', '-')
            new_session_file = os.path.join(self.get_session_dir(), 
                                           f"{file_timestamp}_{new_session_id}.jsonl")
            
            file_entries, new_session_id = create_branched_session_entries(
                self._cwd, previous_session_file, path_without_labels, labels_to_write, True
            )
            
            self._file_entries = file_entries
            self._session_id = new_session_id
            self._session_file = new_session_file
            self._build_index()
            
            # 检查是否有assistant消息
            has_assistant = any(
                e.type == "message" and hasattr(e.message, 'role') and e.message.role == "assistant"
                for e in self._file_entries
            )
            if has_assistant:
                self._rewrite_file()
                self._flushed = True
            else:
                self._flushed = False
            
            return new_session_file
        
        # 内存模式
        file_entries, new_session_id = create_branched_session_entries(
            self._cwd, previous_session_file, path_without_labels, labels_to_write, False
        )
        self._file_entries = file_entries
        self._session_id = new_session_id
        self._build_index()
        return None
    
    # ========================================================================
    # 静态工厂方法
    # ========================================================================
    
    @classmethod
    def create(cls, cwd: str, session_dir: Optional[str] = None) -> 'SessionManager':
        """创建新会话"""
        dir_path = session_dir if session_dir is not None else get_default_session_dir(cwd)
        return cls(cwd, dir_path, None, True)
    
    @classmethod
    def open(cls, path: str, session_dir: Optional[str] = None) -> 'SessionManager':
        """打开指定会话"""
        entries = load_entries_from_file(path)
        header = next((e for e in entries if isinstance(e, SessionHeader)), None)
        cwd = header.cwd if header and hasattr(header, 'cwd') else os.getcwd()
        dir_path = session_dir if session_dir is not None else os.path.abspath(os.path.dirname(path))
        return cls(cwd, dir_path, path, True)
    
    @classmethod
    def continue_recent(cls, cwd: str, session_dir: Optional[str] = None) -> 'SessionManager':
        """继续最近的会话"""
        dir_path = session_dir if session_dir is not None else get_default_session_dir(cwd)
        most_recent = find_most_recent_session(dir_path)
        if most_recent:
            return cls(cwd, dir_path, most_recent, True)
        return cls(cwd, dir_path, None, True)
    
    @classmethod
    def in_memory(cls, cwd: str = "") -> 'SessionManager':
        """创建内存会话"""
        if not cwd:
            cwd = os.getcwd()
        return cls(cwd, "", None, False)
    
    @classmethod
    async def list(cls, cwd: str, session_dir: Optional[str] = None,
                   on_progress: Optional[Callable[[int, int], None]] = None) -> List[SessionInfo]:
        """列出会话"""
        dir_path = session_dir if session_dir is not None else get_default_session_dir(cwd)
        sessions = await list_sessions_from_dir(dir_path, on_progress)
        sessions.sort(key=lambda s: s.modified, reverse=True)
        return sessions
    
    @classmethod
    async def list_all(cls, on_progress: Optional[Callable[[int, int], None]] = None) -> List[SessionInfo]:
        """列出所有会话"""
        sessions_dir = get_sessions_dir()
        
        try:
            if not os.path.exists(sessions_dir):
                return []
            
            dirs = [os.path.join(sessions_dir, d) for d in os.listdir(sessions_dir)
                   if os.path.isdir(os.path.join(sessions_dir, d))]
            
            # 统计文件总数
            total_files = 0
            dir_files = []
            for dir_path in dirs:
                try:
                    files = [os.path.join(dir_path, f) for f in os.listdir(dir_path) 
                            if f.endswith('.jsonl')]
                    dir_files.append(files)
                    total_files += len(files)
                except:
                    dir_files.append([])
            
            # 处理所有文件
            loaded = 0
            sessions = []
            all_files = [f for files in dir_files for f in files]
            
            results = []
            for file in all_files:
                info = await build_session_info(file)
                loaded += 1
                if on_progress:
                    on_progress(loaded, total_files)
                if info:
                    results.append(info)
            
            sessions.extend(results)
            sessions.sort(key=lambda s: s.modified, reverse=True)
            return sessions
        except Exception:
            return []
    
    @classmethod
    def fork_from(cls, source_path: str, target_cwd: str, session_dir: Optional[str] = None) -> 'SessionManager':
        """从源会话fork新会话"""
        source_entries = load_entries_from_file(source_path)
        if not source_entries:
            raise ValueError(f"Cannot fork: source session file is empty or invalid: {source_path}")
        
        source_header = next((e for e in source_entries if isinstance(e, SessionHeader)), None)
        if not source_header:
            raise ValueError(f"Cannot fork: source session has no header: {source_path}")
        
        dir_path = session_dir if session_dir is not None else get_default_session_dir(target_cwd)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        
        # 创建新会话文件
        new_session_id = uuid.uuid4().hex
        timestamp = datetime.now().isoformat()
        file_timestamp = timestamp.replace(':', '-').replace('.', '-')
        new_session_file = os.path.join(dir_path, f"{file_timestamp}_{new_session_id}.jsonl")
        
        # 写入新头部
        new_header = SessionHeader(
            type="session",
            version=CURRENT_SESSION_VERSION,
            id=new_session_id,
            timestamp=timestamp,
            cwd=target_cwd,
            parent_session=source_path
        )
        with open(new_session_file, 'w', encoding='utf-8') as f:
            f.write(json.dumps({
                'type': 'session',
                'version': new_header.version,
                'id': new_header.id,
                'timestamp': new_header.timestamp,
                'cwd': new_header.cwd,
                'parent_session': new_header.parent_session
            }, default=str) + '\n')
        
        # 复制非头部条目
        for entry in source_entries:
            if not isinstance(entry, SessionHeader):
                with open(new_session_file, 'a', encoding='utf-8') as f:
                    data = {k: v for k, v in entry.__dict__.items() if not k.startswith('_')}
                    f.write(json.dumps(data, default=str) + '\n')
        
        return cls(target_cwd, dir_path, new_session_file, True)