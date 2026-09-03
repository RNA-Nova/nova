"""JSONL 会话仓库（对齐 TS ``session/jsonl/repo.ts``）。

目录布局：``<sessions_root>/--<cwd 编码>--/<iso 时间戳>_<id>.jsonl``。纪律：

- **create/fork 同进程互斥**（``_active_create_destinations``）：文件名含时间戳，
  异步存在性检查放行不了同 tick 的并发竞态——逻辑目的地级 claim；
- **list 只嗅探 header**（``read_text_lines(max_lines=1)``），不加载会话体；
- ``root`` 绝对路径解析结果进程内缓存。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, cast

from .._ids import uuidv7
from ..session import Session, assert_json_serializable
from ..types import ForkOptions, SessionError
from .codec import metadata_from_header, parse_header
from .errors import file_result
from .storage import JsonlSessionStorage
from .types import (
    JsonlFileSystem,
    JsonlSessionCreateOptions,
    JsonlSessionListOptions,
    JsonlSessionMetadata,
    JsonlSessionRepoOptions,
    JsonlV4Header,
)

__all__ = ["JsonlSessionRepo", "list_jsonl_session_metadata", "load_jsonl_session_storage"]

_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


@dataclass(frozen=True, kw_only=True)
class JsonlForkOptions(ForkOptions):
    """fork 选项的 JSONL 扩展：目的地 ``cwd`` 与应用元数据（对齐 TS
    ``ForkOptions & JsonlSessionCreateOptions`` 交叉）。缺省 cwd 回落源会话。"""

    cwd: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_PATTERN.match(session_id):
        raise SessionError(
            "invalid_payload",
            "Session id must be non-empty, contain only alphanumeric characters, "
            "'-', '_', and '.', and start and end with an alphanumeric character",
        )


def _session_directory_name(cwd: str) -> str:
    return f"--{re.sub(r'^[/\\\\]', '', cwd).replace('/', '-').replace(chr(92), '-').replace(':', '-')}--"


async def _sessions_root(options: JsonlSessionRepoOptions) -> str:
    return await file_result(
        options["fs"].absolute_path(options["sessions_root"]),
        f"Failed to resolve sessions root {options['sessions_root']}",
    )


async def _session_directory(
    fs: JsonlFileSystem, sessions_root: str, cwd: str
) -> str:
    return await file_result(
        fs.join_path([sessions_root, _session_directory_name(cwd)]),
        f"Failed to resolve sessions directory for {cwd}",
    )


async def _session_directories(
    options: JsonlSessionRepoOptions, cwd: Optional[str] = None
) -> List[str]:
    fs = options["fs"]
    sessions_root = await _sessions_root(options)
    if cwd is not None:
        resolved_cwd = await file_result(
            fs.absolute_path(cwd), f"Failed to resolve session cwd {cwd}"
        )
        directory = await _session_directory(fs, sessions_root, resolved_cwd)
        if await file_result(
            fs.exists(directory), f"Failed to check sessions directory {directory}"
        ):
            return [directory]
        return []
    if not await file_result(
        fs.exists(sessions_root), f"Failed to check sessions directory {sessions_root}"
    ):
        return []
    entries = await file_result(
        fs.list_dir(sessions_root), f"Failed to list sessions directory {sessions_root}"
    )
    return [
        entry["path"]
        for entry in entries
        if entry["kind"] in ("directory", "symlink")
    ]


async def list_jsonl_session_metadata(
    options: JsonlSessionRepoOptions, query: Optional[JsonlSessionListOptions] = None
) -> List[JsonlSessionMetadata]:
    """列出会话元数据（只嗅探 header，按修改时间倒序）。"""
    fs = options["fs"]
    metadata: List[JsonlSessionMetadata] = []
    for directory in await _session_directories(options, (query or {}).get("cwd")):
        files = await file_result(
            fs.list_dir(directory), f"Failed to list sessions directory {directory}"
        )
        for entry in files:
            if entry["kind"] == "directory" or not entry["name"].endswith(".jsonl"):
                continue
            lines = await file_result(
                fs.read_text_lines(entry["path"], 1),
                f"Failed to read session header {entry['path']}",
            )
            if not lines:
                continue
            ok, header = parse_header(lines[0])
            if not ok:
                continue
            metadata.append(
                cast(JsonlSessionMetadata, metadata_from_header(header, entry["path"], entry["mtime_ms"]))
            )
    metadata.sort(key=lambda item: item["modified_at"], reverse=True)
    return metadata


async def load_jsonl_session_storage(
    options: JsonlSessionRepoOptions, metadata: JsonlSessionMetadata
) -> JsonlSessionStorage:
    fs = options["fs"]
    if not await file_result(
        fs.exists(metadata["path"]), f"Failed to check session {metadata['path']}"
    ):
        raise SessionError("not_found", f"Session not found: {metadata['id']}")
    storage = await JsonlSessionStorage.load(fs, metadata["path"])
    loaded = await storage.get_metadata()
    if loaded["id"] != metadata["id"]:
        raise SessionError("invalid_entry", f"Session id does not match header: {metadata['id']}")
    return storage


def _session_file_name(created_at: int, session_id: str) -> str:
    dt = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
    timestamp = dt.strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
    return f"{timestamp}_{session_id}.jsonl"


class JsonlSessionRepo:
    """JSONL 会话仓库（SessionRepo 的文件后端实现）。"""

    def __init__(self, options: JsonlSessionRepoOptions) -> None:
        self._fs = options["fs"]
        self._sessions_root_input = options["sessions_root"]
        self._active_create_destinations: Set[str] = set()
        self._root_cache: Optional[str] = None
        self._root_lock = asyncio.Lock()

    async def create(
        self, options: Optional[JsonlSessionCreateOptions] = None
    ) -> Session:
        opts: JsonlSessionCreateOptions = options or {}
        destination = await self._resolve_create_destination(opts)
        async def _operation() -> Session:
            return await self._create_with_claim(destination, opts)

        return await self._claim_create_destination(destination, _operation)

    async def open(self, metadata: JsonlSessionMetadata) -> Session:
        return Session(await self._load_storage(metadata))

    async def list(
        self, options: Optional[JsonlSessionListOptions] = None
    ) -> List[JsonlSessionMetadata]:
        return await list_jsonl_session_metadata(self._repo_options(), options)

    async def delete(self, metadata: JsonlSessionMetadata) -> None:
        await file_result(
            self._fs.remove(metadata["path"], force=True),
            f"Failed to delete session {metadata['path']}",
        )

    async def fork(
        self,
        source: JsonlSessionMetadata,
        options: Optional[ForkOptions] = None,
    ) -> Session:
        opts: ForkOptions = options or ForkOptions()
        cwd = getattr(opts, "cwd", None) or source["cwd"]
        app_metadata = getattr(opts, "metadata", None)
        source_storage = await self._load_storage(source)
        create_options: JsonlSessionCreateOptions = {"cwd": cwd}
        if opts.id is not None:
            create_options["id"] = opts.id
        if opts.parent_session_id is not None:
            create_options["parent_session_id"] = opts.parent_session_id
        else:
            create_options["parent_session_id"] = source.get("id", "")
        if app_metadata is not None:
            create_options["metadata"] = app_metadata
        destination = await self._resolve_create_destination(create_options)

        async def _operation() -> Session:
            header, path = await self._prepare_create(destination, create_options)
            storage = await source_storage.fork(path, header, opts)
            return Session(storage)

        return await self._claim_create_destination(destination, _operation)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _repo_options(self) -> JsonlSessionRepoOptions:
        return {"fs": self._fs, "sessions_root": self._sessions_root_input}

    async def _load_storage(self, metadata: JsonlSessionMetadata) -> JsonlSessionStorage:
        return await load_jsonl_session_storage(self._repo_options(), metadata)

    async def _resolve_create_destination(
        self, options: JsonlSessionCreateOptions
    ) -> Dict[str, str]:
        session_id = options.get("id")
        if session_id is None:
            session_id = uuidv7()
        _validate_session_id(session_id)
        cwd_input = options.get("cwd")
        if cwd_input is None:
            raise SessionError("invalid_payload", "JSONL session create requires a cwd")
        cwd = await file_result(
            self._fs.absolute_path(cwd_input),
            f"Failed to resolve session cwd {cwd_input}",
        )
        return {"id": session_id, "cwd": cwd}

    async def _claim_create_destination(
        self,
        destination: Dict[str, str],
        operation: Callable[[], Awaitable[Session]],
    ) -> Session:
        """同进程 create/fork 竞态防线：逻辑目的地级 claim（见 TS 版注释）。"""
        key = f"{destination['cwd']}\0{destination['id']}"
        if key in self._active_create_destinations:
            raise SessionError("already_exists", f"Session already exists: {destination['id']}")
        self._active_create_destinations.add(key)
        try:
            return await operation()
        finally:
            self._active_create_destinations.discard(key)

    async def _create_with_claim(
        self, destination: Dict[str, str], options: JsonlSessionCreateOptions
    ) -> Session:
        header, path = await self._prepare_create(destination, options)
        storage = await JsonlSessionStorage.create(self._fs, path, header)
        return Session(storage)

    async def _prepare_create(
        self, destination: Dict[str, str], options: JsonlSessionCreateOptions
    ) -> Tuple[JsonlV4Header, str]:
        session_id = destination["id"]
        cwd = destination["cwd"]
        if await self._session_id_exists(session_id, cwd):
            raise SessionError("already_exists", f"Session already exists: {session_id}")

        created_at = int(time.time() * 1000)
        session_directory = await self._session_directory(cwd)
        path = await file_result(
            self._fs.join_path([session_directory, _session_file_name(created_at, session_id)]),
            f"Failed to resolve path for session {session_id}",
        )
        metadata = options.get("metadata")
        if metadata is not None:
            assert_json_serializable(metadata)
        header: JsonlV4Header = {
            "kind": "header",
            "version": 4,
            "id": session_id,
            "created_at": created_at,
            "cwd": cwd,
        }
        parent_session_id = options.get("parent_session_id")
        if parent_session_id is not None:
            header["parent_session_id"] = parent_session_id
        if metadata is not None:
            header["metadata"] = metadata
        await file_result(
            self._fs.create_dir(session_directory),
            "Failed to create sessions directory",
        )
        return header, path

    async def _session_id_exists(self, session_id: str, cwd: str) -> bool:
        suffix = f"_{session_id}.jsonl"
        directory = await self._session_directory(cwd)
        if not await file_result(
            self._fs.exists(directory), f"Failed to check sessions directory {directory}"
        ):
            return False
        files = await file_result(
            self._fs.list_dir(directory), f"Failed to list sessions directory {directory}"
        )
        return any(
            entry["kind"] != "directory" and entry["name"].endswith(suffix)
            for entry in files
        )

    async def _session_directory(self, cwd: str) -> str:
        return await _session_directory(self._fs, await self._root(), cwd)

    async def _root(self) -> str:
        async with self._root_lock:
            if self._root_cache is None:
                self._root_cache = await file_result(
                    self._fs.absolute_path(self._sessions_root_input),
                    f"Failed to resolve sessions root {self._sessions_root_input}",
                )
            return self._root_cache
