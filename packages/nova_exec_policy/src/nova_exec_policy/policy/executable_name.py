"""可执行名查找键（对位 codex `execpolicy/src/executable_name.rs`）。

posix 语义：裸名键即原名（codex 的 Windows 可执行后缀剥离在
`#[cfg(not(windows))]` 分支为恒等——执行机沙箱 seatbelt/seccomp 只在
posix 跑）；路径键取 basename。

basename 手写 Rust `Path::file_name` 的 posix 语义——nova_protocol 纯洁性
纪律禁 import os/pathlib（见 `tests/test_purity.py`），字符串操作足够：
尾部分隔符忽略、尾部 `.`（CurDir）归一、终止于 `..`/空路径/纯分隔符
→ None（对位 Rust 同语义）。
"""

from __future__ import annotations


def executable_lookup_key(raw: str) -> str:
    """裸名查找键（对位 `executable_lookup_key` 的非 Windows 分支——恒等）"""
    return raw


def executable_path_lookup_key(path: str) -> str | None:
    """路径 basename 查找键（对位 `executable_path_lookup_key`——
    `Path::file_name` 为 None（根/`..`/空路径）时返回 None）"""
    name = _posix_file_name(path)
    return executable_lookup_key(name) if name is not None else None


def _posix_file_name(path: str) -> str | None:
    """Rust `Path::file_name` 的 posix 语义（CurDir 归一、RootDir/ParentDir → None）"""
    rest = path
    while True:
        stripped = rest.rstrip("/")
        if not stripped:
            return None
        head, _, name = stripped.rpartition("/")
        if name == ".":
            rest = head
            continue
        if name == "..":
            return None
        return name
