"""工具可插拔 operations 抽象。

与 TypeScript ``core/tools/{bash,read,write,edit,grep,find,ls}.ts`` 中的
``*Operations`` 接口对齐。默认实现 ``createLocal*Operations`` 提供本地文件
系统/子进程能力；未来可通过注入不同 operations 实现远程执行或 mock 测试。
"""

from __future__ import annotations

import base64
import fnmatch
import mimetypes
import os
import re
import shutil
import subprocess
from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

SUPPORTED_IMAGE_TYPES = {"png", "jpeg", "jpg", "gif", "webp", "bmp"}

_IMAGE_MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": (b"RIFF", b"WEBP"),
    "bmp": (b"BM",),
}


@dataclass
class ReadResult:
    """读取结果。"""

    text: Optional[str] = None
    bytes_data: Optional[bytes] = None
    mime_type: Optional[str] = None
    size: int = 0
    error: Optional[str] = None


class ReadOperations(Protocol):
    """Read tool 的可插拔 operations。"""

    @abstractmethod
    async def read_text(self, path: str, encoding: str = "utf-8") -> ReadResult:
        """读取文本文件。"""

    @abstractmethod
    async def read_image(self, path: str) -> ReadResult:
        """读取图片文件并返回 base64。"""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """路径是否存在。"""

    @abstractmethod
    def is_file(self, path: str) -> bool:
        """是否是文件。"""

    @abstractmethod
    def is_image_file(self, path: str) -> bool:
        """是否是图片文件。"""

    @abstractmethod
    def guess_mime_type(self, path: str) -> Optional[str]:
        """猜测 MIME 类型。"""


class LocalReadOperations:
    """本地文件系统 ReadOperations 实现。"""

    async def read_text(self, path: str, encoding: str = "utf-8") -> ReadResult:
        try:
            with open(path, "rb") as f:
                raw = f.read()
            text = raw.decode(encoding, errors="replace")
            return ReadResult(text=text, size=len(raw))
        except Exception as exc:
            return ReadResult(error=str(exc))

    async def read_image(self, path: str) -> ReadResult:
        try:
            with open(path, "rb") as f:
                data = f.read()
            mime, _ = mimetypes.guess_type(path)
            if mime is None:
                mime = "image/png"
            b64 = base64.b64encode(data).decode("utf-8")
            return ReadResult(bytes_data=data, mime_type=mime, size=len(data))
        except Exception as exc:
            return ReadResult(error=str(exc))

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def is_file(self, path: str) -> bool:
        return os.path.isfile(path)

    def is_image_file(self, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext in SUPPORTED_IMAGE_TYPES:
            return True
        try:
            with open(path, "rb") as f:
                header = f.read(12)
        except Exception:
            return False
        for img_type, magics in _IMAGE_MAGIC.items():
            for magic in magics:
                if header.startswith(magic):
                    return True
            if img_type == "webp" and len(header) >= 12 and header[8:12] == b"WEBP":
                return True
        return False

    def guess_mime_type(self, path: str) -> Optional[str]:
        mime, _ = mimetypes.guess_type(path)
        return mime


def create_local_read_operations() -> ReadOperations:
    return LocalReadOperations()


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


@dataclass
class WriteResult:
    """写入结果。"""

    existed: bool = False
    chars: int = 0
    error: Optional[str] = None


class WriteOperations(Protocol):
    """Write tool 的可插拔 operations。"""

    @abstractmethod
    async def write_file(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> WriteResult:
        """写入文件。"""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """路径是否存在。"""

    @abstractmethod
    def ensure_parent_dir(self, path: str) -> None:
        """确保父目录存在。"""


class LocalWriteOperations:
    """本地文件系统 WriteOperations 实现。"""

    async def write_file(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> WriteResult:
        try:
            existed = os.path.exists(path)
            self.ensure_parent_dir(path)
            with open(path, "w", encoding=encoding) as f:
                f.write(content)
            return WriteResult(existed=existed, chars=len(content))
        except Exception as exc:
            return WriteResult(error=str(exc))

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def ensure_parent_dir(self, path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)


def create_local_write_operations() -> WriteOperations:
    return LocalWriteOperations()


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


@dataclass
class EditResult:
    """编辑结果。"""

    new_text: str = ""
    total_replacements: int = 0
    diffs: List[str] = field(default_factory=list)
    error: Optional[str] = None


class EditOperations(Protocol):
    """Edit tool 的可插拔 operations。"""

    @abstractmethod
    async def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """读取文本。"""

    @abstractmethod
    async def write_text(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> None:
        """写入文本。"""

    @abstractmethod
    def apply_edits(self, text: str, edits: List[Dict[str, str]]) -> EditResult:
        """应用编辑。"""


class LocalEditOperations:
    """本地文件系统 EditOperations 实现。"""

    async def read_text(self, path: str, encoding: str = "utf-8") -> str:
        with open(path, "r", encoding=encoding) as f:
            return f.read()

    async def write_text(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> None:
        with open(path, "w", encoding=encoding) as f:
            f.write(content)

    def apply_edits(self, text: str, edits: List[Dict[str, str]]) -> EditResult:
        total = 0
        diffs: List[str] = []
        for edit in edits:
            old_text = edit.get("oldText", "")
            new_text = edit.get("newText", "")
            if old_text == "":
                diffs.append("⚠️ 跳过空 oldText 的编辑项")
                continue
            count = text.count(old_text)
            if count == 0:
                diffs.append(f"❌ 未找到: {old_text[:40]!r}")
                continue
            text = text.replace(old_text, new_text)
            total += count
            diffs.append(f"✅ {count} 处替换: {old_text[:40]!r} -> {new_text[:40]!r}")
        return EditResult(new_text=text, total_replacements=total, diffs=diffs)


def create_local_edit_operations() -> EditOperations:
    return LocalEditOperations()


# ---------------------------------------------------------------------------
# Grep
# ---------------------------------------------------------------------------


@dataclass
class GrepMatch:
    """Grep 单条匹配。"""

    path: str
    line: int
    text: str


@dataclass
class GrepOptions:
    """Grep 选项。"""

    regex: str
    file_pattern: Optional[str] = None
    case_sensitive: bool = False
    literal: bool = False
    context_lines: int = 0
    limit: int = 100


class GrepOperations(Protocol):
    """Grep tool 的可插拔 operations。"""

    @abstractmethod
    async def grep(self, path: str, options: GrepOptions) -> List[GrepMatch]:
        """搜索文件内容。"""


class LocalGrepOperations:
    """本地 GrepOperations 实现（优先 rg，fallback Python）。"""

    async def grep(self, path: str, options: GrepOptions) -> List[GrepMatch]:
        if shutil.which("rg"):
            return self._grep_with_rg(path, options)
        return self._grep_with_python(path, options)

    def _grep_with_rg(self, path: str, options: GrepOptions) -> List[GrepMatch]:
        args = [
            "rg",
            "--json",
            "--line-number",
            "--max-count",
            str(options.limit),
        ]
        if not options.case_sensitive:
            args.append("--ignore-case")
        if options.literal:
            args.append("--fixed-strings")
        if options.file_pattern:
            args.extend(["--glob", options.file_pattern])
        if options.context_lines > 0:
            args.extend(["--context", str(options.context_lines)])
        args.extend(["--", options.regex, path])

        try:
            proc = subprocess.run(args, capture_output=True, text=True, check=False)
        except Exception:
            return []

        results: List[GrepMatch] = []
        for line in proc.stdout.splitlines():
            try:
                import json

                data = json.loads(line)
                if data.get("type") != "match":
                    continue
                payload = data.get("data", {})
                path_obj = payload.get("path", {})
                file_path = path_obj.get("text", "")
                line_num = payload.get("line_number", 0)
                lines = payload.get("lines", {})
                text = lines.get("text", "")
                results.append(
                    GrepMatch(path=file_path, line=line_num, text=text.rstrip("\n"))
                )
            except Exception:
                continue
        return results

    def _grep_with_python(self, path: str, options: GrepOptions) -> List[GrepMatch]:
        flags = 0 if options.case_sensitive else re.IGNORECASE
        pattern_str = re.escape(options.regex) if options.literal else options.regex
        pattern = re.compile(pattern_str, flags)
        results: List[GrepMatch] = []

        targets: List[str] = []
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                for name in files:
                    if options.file_pattern and not fnmatch.fnmatch(
                        name, options.file_pattern
                    ):
                        continue
                    targets.append(os.path.join(root, name))
        else:
            targets.append(path)

        for file_path in targets:
            if not os.path.isfile(file_path):
                continue
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if pattern.search(line):
                            results.append(
                                GrepMatch(
                                    path=file_path,
                                    line=lineno,
                                    text=line.rstrip("\n"),
                                )
                            )
                            if len(results) >= options.limit:
                                return results
            except Exception:
                continue
        return results


def create_local_grep_operations() -> GrepOperations:
    return LocalGrepOperations()


# ---------------------------------------------------------------------------
# Find
# ---------------------------------------------------------------------------


@dataclass
class FindOptions:
    """Find 选项。"""

    pattern: str = ""
    path: str = "."
    find_type: str = "file"  # "file" | "directory"
    limit: int = 1000


class FindOperations(Protocol):
    """Find tool 的可插拔 operations。"""

    @abstractmethod
    async def find(self, options: FindOptions) -> List[str]:
        """查找文件或目录，返回绝对路径列表。"""


class LocalFindOperations:
    """本地 FindOperations 实现（优先 fd，fallback Python）。"""

    async def find(self, options: FindOptions) -> List[str]:
        if shutil.which("fd"):
            return self._find_with_fd(options)
        return self._find_with_python(options)

    def _find_with_fd(self, options: FindOptions) -> List[str]:
        args = [
            "fd",
            "--absolute-path",
            "--max-results",
            str(options.limit),
        ]
        if options.find_type == "directory":
            args.extend(["--type", "d"])
        else:
            args.extend(["--type", "f"])
        if options.pattern:
            # 用户传入的是 shell glob（如 *.py），fd 默认按正则解析，需要 --glob
            args.extend(["--glob", options.pattern])
        args.append(options.path)

        try:
            proc = subprocess.run(args, capture_output=True, text=True, check=False)
            if proc.returncode != 0 and proc.returncode != 1:
                # fd 返回 1 仅表示未找到匹配；其他返回码视为错误
                return []
            return [line for line in proc.stdout.splitlines() if line]
        except Exception:
            return []

    def _find_with_python(self, options: FindOptions) -> List[str]:
        root = Path(options.path)
        results: List[str] = []
        pattern = options.pattern or "*"

        if options.find_type == "directory":
            candidates = [p for p in root.rglob("*") if p.is_dir()]
        else:
            candidates = [p for p in root.rglob("*") if p.is_file()]

        for candidate in candidates:
            if options.pattern and not candidate.match(pattern):
                continue
            results.append(str(candidate.resolve()))
            if len(results) >= options.limit:
                break
        return results


def create_local_find_operations() -> FindOperations:
    return LocalFindOperations()


# ---------------------------------------------------------------------------
# Ls
# ---------------------------------------------------------------------------


@dataclass
class LsEntry:
    """Ls 单条目录条目。"""

    name: str
    is_directory: bool


@dataclass
class LsOptions:
    """Ls 选项。"""

    path: str = "."
    limit: int = 500


class LsOperations(Protocol):
    """Ls tool 的可插拔 operations。"""

    @abstractmethod
    async def list_dir(self, options: LsOptions) -> Tuple[List[LsEntry], bool]:
        """列出目录。返回 (条目列表, 是否被截断)。"""


class LocalLsOperations:
    """本地 LsOperations 实现。"""

    async def list_dir(self, options: LsOptions) -> Tuple[List[LsEntry], bool]:
        names = sorted(os.listdir(options.path), key=str.lower)
        entries = []
        for name in names:
            full = os.path.join(options.path, name)
            entries.append(LsEntry(name=name, is_directory=os.path.isdir(full)))
        truncated = len(entries) > options.limit
        return entries[: options.limit], truncated


def create_local_ls_operations() -> LsOperations:
    return LocalLsOperations()


__all__ = [
    "ReadOperations",
    "ReadResult",
    "create_local_read_operations",
    "WriteOperations",
    "WriteResult",
    "create_local_write_operations",
    "EditOperations",
    "EditResult",
    "create_local_edit_operations",
    "GrepOperations",
    "GrepMatch",
    "GrepOptions",
    "create_local_grep_operations",
    "FindOperations",
    "FindOptions",
    "create_local_find_operations",
    "LsOperations",
    "LsEntry",
    "LsOptions",
    "create_local_ls_operations",
]
