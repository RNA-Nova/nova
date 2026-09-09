"""本地文件系统实现（对齐 TS ``harness/env/nodejs.ts`` 的 fs 适配层）。

所有方法线程池执行（``asyncio.to_thread``），不阻塞事件循环；错误以 OSError
族抛出，由 ``errors.file_result`` 映射为 SessionError。
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import List

from .types import DirEntry, FileInfo

__all__ = ["LocalJsonlFileSystem"]


class LocalJsonlFileSystem:
    """基于本地盘的 :class:`JsonlFileSystem` 实现。"""

    async def absolute_path(self, path: str) -> str:
        return await asyncio.to_thread(lambda: str(Path(path).resolve(strict=False)))

    async def join_path(self, parts: List[str]) -> str:
        return await asyncio.to_thread(lambda: str(Path(*parts)))

    async def read_text_file(self, path: str) -> str:
        return await asyncio.to_thread(Path(path).read_text, "utf-8")

    async def read_text_lines(self, path: str, max_lines: int) -> List[str]:
        def _read() -> List[str]:
            lines: List[str] = []
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if len(lines) >= max_lines:
                        break
                    lines.append(line.rstrip("\n"))
            return lines

        return await asyncio.to_thread(_read)

    async def write_file(self, path: str, content: str) -> None:
        def _write() -> None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(content, "utf-8")

        await asyncio.to_thread(_write)

    async def append_file(self, path: str, content: str) -> None:
        def _append() -> None:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(content)

        await asyncio.to_thread(_append)

    async def rename_file(self, src: str, dst: str) -> None:
        def _rename() -> None:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)

        await asyncio.to_thread(_rename)

    async def file_info(self, path: str) -> FileInfo:
        def _info() -> FileInfo:
            stat = Path(path).stat()
            return {"mtime_ms": stat.st_mtime * 1000}

        return await asyncio.to_thread(_info)

    async def list_dir(self, path: str) -> List[DirEntry]:
        def _list() -> List[DirEntry]:
            root = Path(path)
            entries: List[DirEntry] = []
            for child in sorted(root.iterdir(), key=lambda p: p.name):
                if child.is_symlink():
                    kind = "symlink"
                elif child.is_dir():
                    kind = "directory"
                else:
                    kind = "file"
                entries.append(
                    {
                        "path": str(child),
                        "name": child.name,
                        "kind": kind,
                        "mtime_ms": child.stat().st_mtime * 1000,
                    }
                )
            return entries

        return await asyncio.to_thread(_list)

    async def exists(self, path: str) -> bool:
        return await asyncio.to_thread(lambda: Path(path).exists())

    async def create_dir(self, path: str) -> None:
        await asyncio.to_thread(lambda: Path(path).mkdir(parents=True, exist_ok=True))

    async def remove(self, path: str, force: bool = False) -> None:
        def _remove() -> None:
            target = Path(path)
            if not target.exists():
                if force:
                    return
                raise FileNotFoundError(path)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()

        await asyncio.to_thread(_remove)
