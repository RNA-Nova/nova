"""
Session models and data structures
"""

import os
from typing import List, Optional, Callable
from datetime import datetime

from .types import SessionInfo, SessionHeader
from .utils import (
    parse_session_entries, get_session_modified_date,
    extract_text_content
)


async def build_session_info(file_path: str) -> Optional[SessionInfo]:
    """构建会话信息"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        entries = parse_session_entries(content)
        if not entries:
            return None

        header = entries[0]
        if not isinstance(header, SessionHeader):
            return None

        stats = os.stat(file_path)
        message_count = 0
        first_message = ""
        all_messages = []
        name = None

        for entry in entries:
            if entry.type == "session_info":
                name = entry.name.strip() if entry.name else None

            if entry.type != "message":
                continue

            message_count += 1
            if not hasattr(entry.message, 'role') or entry.message.role not in ('user', 'assistant'):
                continue

            text_content = extract_text_content(entry.message)
            if not text_content:
                continue

            all_messages.append(text_content)
            if not first_message and entry.message.role == "user":
                first_message = text_content

        cwd = header.cwd if hasattr(header, 'cwd') else ""
        parent_session_path = header.parent_session if hasattr(header, 'parent_session') else None
        modified = get_session_modified_date(entries, header, stats.st_mtime)

        return SessionInfo(
            path=file_path,
            id=header.id,
            cwd=cwd,
            name=name,
            parent_session_path=parent_session_path,
            created=datetime.fromisoformat(header.timestamp),
            modified=modified,
            message_count=message_count,
            first_message=first_message or "(no messages)",
            all_messages_text=' '.join(all_messages)
        )
    except Exception:
        return None


async def list_sessions_from_dir(
    dir_path: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
    progress_offset: int = 0,
    progress_total: Optional[int] = None
) -> List[SessionInfo]:
    """从目录列出会话"""
    sessions = []
    if not os.path.exists(dir_path):
        return sessions

    try:
        files = [os.path.join(dir_path, f) for f in os.listdir(dir_path) 
                if f.endswith('.jsonl')]
        total = progress_total if progress_total is not None else len(files)

        loaded = 0
        results = []
        for file in files:
            info = await build_session_info(file)
            loaded += 1
            if on_progress:
                on_progress(progress_offset + loaded, total)
            if info:
                results.append(info)

        sessions.extend(results)
    except Exception:
        pass

    return sessions