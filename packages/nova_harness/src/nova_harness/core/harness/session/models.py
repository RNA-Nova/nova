"""
Session models and data structures
"""

import asyncio
import os
from datetime import datetime
from typing import Callable, List, Optional

from nova_harness.core.harness.session.utils import (
    extract_text_content,
    get_session_modified_date,
    parse_session_entries,
)
from nova_harness.core.types.session.entries import SessionHeader
from nova_harness.core.types.session.info import SessionInfo

# 同时加载会话信息的最大并发数
MAX_CONCURRENT_SESSION_INFO_LOADS = 10


def _build_session_info_sync(file_path: str) -> Optional[SessionInfo]:
    """同步方式构建单个会话信息（供线程池调用）。"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
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
            if not hasattr(entry.message, "role") or entry.message.role not in (
                "user",
                "assistant",
            ):
                continue

            text_content = extract_text_content(entry.message)
            if not text_content:
                continue

            all_messages.append(text_content)
            if not first_message and entry.message.role == "user":
                first_message = text_content

        cwd = header.cwd if hasattr(header, "cwd") else ""
        parent_session_path = (
            header.parent_session if hasattr(header, "parent_session") else None
        )
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
            all_messages_text=" ".join(all_messages),
        )
    except Exception:
        return None


async def build_session_info(
    file_path: str, semaphore: Optional[asyncio.Semaphore] = None
) -> Optional[SessionInfo]:
    """异步构建会话信息，支持并发限制。

    由于底层是同步文件 I/O，使用 `asyncio.to_thread` 避免阻塞事件循环。
    """
    if semaphore is None:
        return await asyncio.to_thread(_build_session_info_sync, file_path)

    async with semaphore:
        return await asyncio.to_thread(_build_session_info_sync, file_path)


async def list_sessions_from_dir(
    dir_path: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
    progress_offset: int = 0,
    progress_total: Optional[int] = None,
) -> List[SessionInfo]:
    """从目录列出会话，限制最大并发数。"""
    sessions: List[SessionInfo] = []
    if not os.path.exists(dir_path):
        return sessions

    try:
        files = [
            os.path.join(dir_path, f)
            for f in os.listdir(dir_path)
            if f.endswith(".jsonl")
        ]
        total = progress_total if progress_total is not None else len(files)

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSION_INFO_LOADS)
        loaded = 0

        async def load_one(file: str) -> Optional[SessionInfo]:
            nonlocal loaded
            info = await build_session_info(file, semaphore)
            loaded += 1
            if on_progress:
                on_progress(progress_offset + loaded, total)
            return info

        tasks = [asyncio.create_task(load_one(f)) for f in files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, SessionInfo):
                sessions.append(result)
    except Exception:
        pass

    return sessions
