"""
Session listing helpers: scan session directories and build session infos.
"""

import asyncio
import os
from datetime import datetime
from typing import Callable, List, Optional

from nova_harness.core.harness.session.utils import (
    message_activity_time,
    parse_session_entry_line,
)
from nova_harness.core.types.session.entries import SessionHeader
from nova_harness.core.types.session.info import SessionInfo
from nova_harness.core.utils.messages import extract_text_from_content

# 同时加载会话信息的最大并发数
MAX_CONCURRENT_SESSION_INFO_LOADS = 10


def _build_session_info_sync(file_path: str) -> Optional[SessionInfo]:
    """同步方式构建单个会话信息（单次流式扫描，不完整读入内存）。"""
    try:
        stats = os.stat(file_path)
        header: Optional[SessionHeader] = None
        message_count = 0
        first_message = ""
        all_messages: List[str] = []
        name: Optional[str] = None
        last_time: Optional[float] = None

        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                entry = parse_session_entry_line(line)
                if entry is None:
                    continue
                if header is None:
                    if not isinstance(entry, SessionHeader):
                        return None
                    header = entry
                    continue

                if entry.type == "session_info":
                    # 最新一条决定（空名表示显式清除，对齐 TS）
                    name = entry.name.strip() or None if entry.name else None
                    continue

                if entry.type != "message":
                    continue
                message_count += 1

                activity = message_activity_time(entry)
                if activity is not None:
                    last_time = max(last_time or 0, activity)

                message = entry.message
                if message.role not in ("user", "assistant"):
                    continue
                text_content = extract_text_from_content(message.content)
                if not text_content:
                    continue
                all_messages.append(text_content)
                if not first_message and message.role == "user":
                    first_message = text_content

        if header is None:
            return None

        if last_time:
            modified = datetime.fromtimestamp(last_time / 1000)
        else:
            try:
                modified = datetime.fromisoformat(header.timestamp)
            except ValueError:
                modified = datetime.fromtimestamp(stats.st_mtime)

        try:
            created = datetime.fromisoformat(header.timestamp)
        except ValueError:
            # header 时间戳缺失/非法时回退文件 mtime（对齐 TS：不因此丢弃会话）
            created = datetime.fromtimestamp(stats.st_mtime)

        return SessionInfo(
            path=file_path,
            id=header.id,
            cwd=header.cwd,
            name=name,
            parent_session_path=header.parent_session,
            created=created,
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
