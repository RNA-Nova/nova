"""JSONL 落盘后端的类型与文件系统契约（对齐 TS ``session/jsonl/types.ts``）。

落盘格式：nov v4 方言——header 行 + mutation 行的 JSONL；**键为 snake_case**
（与库层内部表示零转换；AGENTS.md 规则 10 的 durable 形状即落盘形状）。
``cwd`` 编码目录 ``--<encoded-cwd>--`` 与文件名 ``<iso-ts>_<id>.jsonl`` 布局
沿用 TS 版。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, NotRequired, Protocol, TypedDict

from ..types import SessionMetadata

__all__ = [
    "DirEntry",
    "FileInfo",
    "JsonlDirectoryEntryKind",
    "JsonlFileSystem",
    "JsonlSessionCreateOptions",
    "JsonlSessionListOptions",
    "JsonlSessionMetadata",
    "JsonlSessionRepoOptions",
    "JsonlV4Header",
]


class FileInfo(TypedDict):
    """文件元数据（存储层分配 metadata 的时间戳来源）。"""

    mtime_ms: float


JsonlDirectoryEntryKind = Literal["directory", "file", "symlink"]


class DirEntry(TypedDict):
    """``list_dir`` 的目录项。"""

    path: str
    name: str
    kind: JsonlDirectoryEntryKind
    mtime_ms: float


class JsonlFileSystem(Protocol):
    """JSONL 后端依赖的文件系统原语（对齐 TS ``FileSystem`` 的必需子集）。

    库层零路径假设——调用方注入实现（本地盘 / 内存 / 远端）。实现以 OSError
    报错：``FileNotFoundError`` 映射 ``not_found``，其余映射 ``storage``。
    """

    async def absolute_path(self, path: str) -> str: ...
    async def join_path(self, parts: List[str]) -> str: ...
    async def read_text_file(self, path: str) -> str: ...
    async def read_text_lines(self, path: str, max_lines: int) -> List[str]: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def append_file(self, path: str, content: str) -> None: ...
    async def rename_file(self, src: str, dst: str) -> None: ...
    async def file_info(self, path: str) -> FileInfo: ...
    async def list_dir(self, path: str) -> List[DirEntry]: ...
    async def exists(self, path: str) -> bool: ...
    async def create_dir(self, path: str) -> None: ...
    async def remove(self, path: str, force: bool = False) -> None: ...


class JsonlSessionRepoOptions(TypedDict):
    fs: JsonlFileSystem
    """包含 cwd 编码会话目录的根。"""
    sessions_root: str


class JsonlSessionMetadata(SessionMetadata):
    """扩展会话元数据：cwd / path / 修改时间 / 来源格式。"""

    cwd: str
    path: str
    modified_at: float
    source_format: Literal[3, 4]
    legacy_parent_session_path: NotRequired[str]
    """仅当 v3 父路径无法解析为会话 id 时保留。"""
    metadata: NotRequired[Dict[str, Any]]
    """应用自有的不透明元数据。"""


class JsonlSessionCreateOptions(TypedDict, total=False):
    id: str
    parent_session_id: str
    cwd: str
    metadata: Dict[str, Any]


class JsonlSessionListOptions(TypedDict, total=False):
    cwd: str


class JsonlV4Header(TypedDict):
    """会话文件首行（v4 头）。"""

    kind: Literal["header"]
    version: int
    id: str
    created_at: int
    cwd: str
    parent_session_id: NotRequired[str]
    legacy_parent_session_path: NotRequired[str]
    metadata: NotRequired[Dict[str, Any]]
