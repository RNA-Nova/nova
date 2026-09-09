"""JSONL 落盘后端（对齐 TS ``session/jsonl/``）。

- ``codec``：行编解码（header / mutation，snake_case 方言）
- ``storage``：追加写 + 启动重放 + 撕裂尾修复 + 原子发布
- ``repo``：目录布局 / create-fork 互斥 / header 嗅探 list
- ``local_fs``：本地盘文件系统实现（测试与 CLI 用）
- 任何 SessionRepo 后端都必须通过 ``session.testing`` 的 30 用例一致性套件
"""

from .errors import JsonlDecodeError
from .local_fs import LocalJsonlFileSystem
from .repo import (
    JsonlForkOptions,
    JsonlSessionRepo,
    list_jsonl_session_metadata,
    load_jsonl_session_storage,
)
from .storage import JsonlSessionStorage
from .types import (
    DirEntry,
    FileInfo,
    JsonlFileSystem,
    JsonlSessionCreateOptions,
    JsonlSessionListOptions,
    JsonlSessionMetadata,
    JsonlSessionRepoOptions,
    JsonlV4Header,
)

__all__ = [
    "DirEntry",
    "FileInfo",
    "JsonlDecodeError",
    "JsonlFileSystem",
    "JsonlForkOptions",
    "JsonlSessionCreateOptions",
    "JsonlSessionListOptions",
    "JsonlSessionMetadata",
    "JsonlSessionRepo",
    "JsonlSessionRepoOptions",
    "JsonlSessionStorage",
    "JsonlV4Header",
    "LocalJsonlFileSystem",
    "list_jsonl_session_metadata",
    "load_jsonl_session_storage",
]
