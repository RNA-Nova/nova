"""工具可插拔 operations 抽象。

与 TypeScript ``core/tools/{bash,read,write,edit,grep,find,ls}.ts`` 中的
``*Operations`` 接口对齐。

**架构**：六个 fs 工具的实现类全部参数化在
``tools_common/fs_layer.FileSystemLayer`` 之上，
``create_local_*_operations`` 缺省注入本地 layer。grep/find 的本机二进制
加速（fd/rg 子进程）归 layer 的 ``accelerates_search`` 裁决，便携引擎
（walk + read + 正则/匹配）兜底。

并发约定：agent loop 的 parallel 模式靠 asyncio 并发驱动多个工具，任何
阻塞调用都会冻结整个事件循环（其他工具的执行、on_update 流式推送、
abort 处理全部停摆）。因此——

- 子进程（rg / fd）一律走 ``asyncio.create_subprocess_exec``；
- 本地 layer 的纯 Python 遍历与文件 I/O 一律 ``asyncio.to_thread``
  移出事件循环（见 fs_layer）。
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import json
import mimetypes
import os
import re
from abc import abstractmethod
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, Tuple

from nova_coding_agent.tools_common.fs_layer import (
    FileSystemLayer,
    get_local_file_system_layer,
)
from nova_coding_agent.tools_common.truncate import (
    UNLIMITED_MAX_LINES,
    TruncationOptions,
    truncate_head,
    truncate_line,
)

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

SUPPORTED_IMAGE_TYPES = {"png", "jpeg", "jpg", "gif", "webp", "bmp"}


def detect_image_mime_type(header: bytes) -> Optional[str]:
    """按魔数嗅探图片 MIME。

    只识别五种内联格式，识别不出返回 ``None``。
    更严格的结构校验（PNG IHDR 完整性、APNG 拒绝、JPEG 0xF7 拒绝、
    BMP 结构校验）有意不做：无法解码的输入由 ``process_image`` 的
    优雅降级路径兜底（返回提示文本让模型继续）。
    """
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header[:3] == b"GIF":
        return "image/gif"
    # webp 必须 RIFF 头 + 偏移 8 处 WEBP 四字节（wav/avi 等 RIFF 文件不算）
    if header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"BM"):
        return "image/bmp"
    return None


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
        """读取图片文件，返回原始字节与 MIME 类型。"""

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """路径是否存在。"""

    @abstractmethod
    async def is_file(self, path: str) -> bool:
        """是否是文件。"""

    @abstractmethod
    async def is_image_file(self, path: str) -> bool:
        """是否是图片文件。"""

    @abstractmethod
    def guess_mime_type(self, path: str) -> Optional[str]:
        """猜测 MIME 类型（纯字符串推断，无 IO——保持同步）。"""


class LocalReadOperations:
    """ReadOperations 实现（参数化 FileSystemLayer——本地/远程同构）。

    远程后端注入 ``ExecutorFileSystemLayer`` 即切换为远程读取，
    实现体零分叉（executor 接入定案）。
    """

    def __init__(self, fs: Optional[FileSystemLayer] = None) -> None:
        self._fs = fs or get_local_file_system_layer()

    async def read_text(self, path: str, encoding: str = "utf-8") -> ReadResult:
        try:
            raw = await self._fs.read_bytes(path)
            text = raw.decode(encoding, errors="replace")
            return ReadResult(text=text, size=len(raw))
        except Exception as exc:
            return ReadResult(error=str(exc))

    async def read_image(self, path: str) -> ReadResult:
        try:
            data = await self._fs.read_bytes(path)
            # MIME 以魔数嗅探为准：
            # 扩展名不可信（无扩展名或张冠李戴时 mimetypes 会标错类型）；
            # 嗅探不出再退 mimetypes，最后兜底 image/png
            mime = detect_image_mime_type(data[:12])
            if mime is None:
                mime = mimetypes.guess_type(path)[0] or "image/png"
            return ReadResult(bytes_data=data, mime_type=mime, size=len(data))
        except Exception as exc:
            return ReadResult(error=str(exc))

    async def exists(self, path: str) -> bool:
        return (await self._fs.metadata(path)).exists

    async def is_file(self, path: str) -> bool:
        return (await self._fs.metadata(path)).is_file

    async def is_image_file(self, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext in SUPPORTED_IMAGE_TYPES:
            return True
        try:
            header = await self._fs.read_range(path, 0, 12)
        except Exception:
            return False
        return detect_image_mime_type(header) is not None

    def guess_mime_type(self, path: str) -> Optional[str]:
        mime, _ = mimetypes.guess_type(path)
        return mime


def create_local_read_operations(
    fs: Optional[FileSystemLayer] = None,
) -> ReadOperations:
    return LocalReadOperations(fs)


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
    async def exists(self, path: str) -> bool:
        """路径是否存在。"""

    @abstractmethod
    async def ensure_parent_dir(self, path: str) -> None:
        """确保父目录存在。"""


class LocalWriteOperations:
    """WriteOperations 实现（参数化 FileSystemLayer——本地/远程同构）。"""

    def __init__(self, fs: Optional[FileSystemLayer] = None) -> None:
        self._fs = fs or get_local_file_system_layer()

    async def write_file(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> WriteResult:
        try:
            existed = (await self._fs.metadata(path)).exists
            await self.ensure_parent_dir(path)
            # 显式 encode 落盘：内容原样写出（对齐原实现的字节保真语义）
            await self._fs.write_bytes(path, content.encode(encoding))
            return WriteResult(existed=existed, chars=len(content))
        except Exception as exc:
            return WriteResult(error=str(exc))

    async def exists(self, path: str) -> bool:
        return (await self._fs.metadata(path)).exists

    async def ensure_parent_dir(self, path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            await self._fs.create_dir(parent)


def create_local_write_operations(
    fs: Optional[FileSystemLayer] = None,
) -> WriteOperations:
    return LocalWriteOperations(fs)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


class EditOperations(Protocol):
    """Edit tool 的可插拔 operations（仅文件 IO；编辑语义见 edit_engine）。"""

    @abstractmethod
    async def access(self, path: str) -> None:
        """检查文件存在且可读可写（不满足则抛异常）。"""

    @abstractmethod
    async def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """读取文本。"""

    @abstractmethod
    async def write_text(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> None:
        """写入文本。"""


class LocalEditOperations:
    """EditOperations 实现（参数化 FileSystemLayer——本地/远程同构）。

    编辑语义（匹配/fuzzy/diff）全在上层 edit_engine，本层只有文件 IO——
    天然后端无关（executor 接入定案）。
    """

    def __init__(self, fs: Optional[FileSystemLayer] = None) -> None:
        self._fs = fs or get_local_file_system_layer()

    async def access(self, path: str) -> None:
        # 读前 fail-fast：不存在/只读在读与匹配之前就报错，而不是等写盘
        # 才暴露（本地带 R_OK|W_OK；远程写时自然报错，见 layer 文档）
        await self._fs.check_writable(path)

    async def read_text(self, path: str, encoding: str = "utf-8") -> str:
        # 二进制读 + 显式 decode：保原字节（detect_line_ending 需要看到
        # 真实 CRLF，文本模式的 universal newlines 会抹掉）
        raw = await self._fs.read_bytes(path)
        return raw.decode(encoding)

    async def write_text(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> None:
        # 显式 encode 落盘：restore_line_endings 恢复出的 \r\n 原样写出
        await self._fs.write_bytes(path, content.encode(encoding))


def create_local_edit_operations(
    fs: Optional[FileSystemLayer] = None,
) -> EditOperations:
    return LocalEditOperations(fs)


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

    pattern: str
    glob: Optional[str] = None
    ignore_case: bool = False
    literal: bool = False
    context: int = 0
    limit: int = 100
    signal: Any = None  # abort 信号：rg 子进程被 kill，Python 兜底步骤间检查


@dataclass
class GrepResult:
    """Grep 结果（渲染后）。"""

    content: str = ""
    match_count: int = 0
    match_limit_reached: bool = False
    truncated: bool = False
    lines_truncated: bool = False
    no_matches: bool = False


class GrepOperations(Protocol):
    """Grep tool 的可插拔 operations。"""

    @abstractmethod
    async def grep(self, path: str, options: GrepOptions) -> GrepResult:
        """搜索文件内容并渲染输出。"""


def _is_aborted(signal: Any) -> bool:
    return signal is not None and getattr(signal, "aborted", False)


# 便携引擎（grep/find 无二进制路径）的并发读窗口
# ——本地无感、远程 WS 延迟摊薄的关键
_SEARCH_CONCURRENCY = 8


async def _session_lines_with_abort(session: Any, signal: Any) -> AsyncIterator[str]:
    """session 行流 + abort 监听（触发即 terminate）。

    三个加速链调用点（grep rg --json / find fd / find rg --files）共用的
    读泵形状；提前停止（达 limit/abort）的 terminate 归调用方（见各
    调用点注释——EOF 自然收尾时不 terminate，保住真实退出码）。
    """

    async def _watch() -> None:
        wait_fn = getattr(signal, "wait", None)
        if signal is None or not callable(wait_fn):
            return
        result = wait_fn()
        if inspect.isawaitable(result):
            await result
        await session.terminate()

    watcher = asyncio.create_task(_watch())
    try:
        async for line in session.stdout_lines():
            yield line
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass


class LocalGrepOperations:
    """GrepOperations 实现（参数化 FileSystemLayer + ProcessRunner——
    本地/远程同一份实现）。

    双加速面（``runner`` 提供，缺省/为 None 时落便携引擎）：
    - rg --json 优先（达整体上限即 terminate；不传 --max-count）；
    - 便携引擎兜底（layer.walk + read + 逐行正则——有界并发保序）。
    本地 runner = 本机子进程（resolve_binary 三级解析）；远程 runner =
    executor process/start 无壳 argv 直启（rg 路径随供给探测）。
    ``--hidden`` 包含隐藏文件；context 不由 rg 生成，
    而是按行号自渲染（长行经 ``truncate_line`` 截断，总输出经
    ``truncate_head`` 截断）。
    """

    def __init__(
        self,
        fs: Optional[FileSystemLayer] = None,
        runner: Optional[Any] = None,
    ) -> None:
        self._fs = fs or get_local_file_system_layer()
        self._runner = runner

    async def grep(self, path: str, options: GrepOptions) -> GrepResult:
        if _is_aborted(options.signal):
            raise RuntimeError("Operation aborted")
        # 路径不存在统一前置报错（rg 与便携引擎同语义），不再静默误报无匹配
        stat = await self._fs.metadata(path)
        if not stat.exists:
            raise RuntimeError(f"Path not found: {path}")
        is_dir = stat.is_dir

        matches: List[GrepMatch]
        match_limit_reached = False
        rg_path = await self._runner.rg_path() if self._runner is not None else None
        if rg_path:
            matches, match_limit_reached = await self._collect_with_rg(
                rg_path, path, is_dir, options
            )
        else:
            matches, match_limit_reached = await self._collect_with_walk(
                path, is_dir, options
            )

        if not matches:
            return GrepResult(no_matches=True)
        if _is_aborted(options.signal):
            raise RuntimeError("Operation aborted")

        lines, lines_truncated = await self._render(matches, path, is_dir, options)
        raw_output = "\n".join(lines)
        # 只按字节截断：行数已由匹配数 limit 收口，
        # 再叠默认 2000 行上限会在 context 放大行数时提前截断
        truncation = truncate_head(
            raw_output, TruncationOptions(max_lines=UNLIMITED_MAX_LINES)
        )
        content = truncation.content
        notices: List[str] = []
        if match_limit_reached:
            notices.append(
                f"{max(1, options.limit)} matches limit reached. "
                f"Use limit={max(1, options.limit) * 2} for more, or refine pattern"
            )
        if truncation.truncated:
            notices.append("50KB limit reached")
        if lines_truncated:
            notices.append(
                "Some lines truncated to 500 chars. Use read tool to see full lines"
            )
        if notices:
            content += f"\n\n[{'. '.join(notices)}]"

        return GrepResult(
            content=content,
            match_count=len(matches),
            match_limit_reached=match_limit_reached,
            truncated=truncation.truncated,
            lines_truncated=lines_truncated,
        )

    async def _collect_with_rg(
        self, rg_path: str, path: str, is_dir: bool, options: GrepOptions
    ) -> Tuple[List[GrepMatch], bool]:
        """rg --json 收集匹配（经 ProcessRunner spawn——本地/远程同一份解析；
        达整体上限即 terminate；不传 --max-count）。"""
        assert self._runner is not None
        args = [rg_path, "--json", "--line-number", "--color=never", "--hidden"]
        if options.ignore_case:
            args.append("--ignore-case")
        if options.literal:
            args.append("--fixed-strings")
        if options.glob:
            args.extend(["--glob", options.glob])
        args.extend(["--", options.pattern, path])

        cwd = path if is_dir else (os.path.dirname(path) or ".")
        session = await self._runner.spawn(args, cwd)

        limit = max(1, options.limit)
        match_limit_reached = False
        results: List[GrepMatch] = []
        async for line in _session_lines_with_abort(session, options.signal):
            try:
                data = json.loads(line)
            except Exception:
                continue
            if data.get("type") != "match":
                continue
            payload = data.get("data", {})
            file_path = payload.get("path", {}).get("text", "")
            line_num = payload.get("line_number", 0)
            text = payload.get("lines", {}).get("text", "")
            results.append(
                GrepMatch(path=file_path, line=line_num, text=text.rstrip("\n"))
            )
            if len(results) >= limit:
                match_limit_reached = True
                break
        # 仅在主动停止（达上限/abort）时 terminate：读到 EOF 说明 rg 已收尾，
        # 盲目 kill 会把真实退出码抹成 -9，错误信息随之丢失
        if match_limit_reached or _is_aborted(options.signal):
            await session.terminate()
        exit_code = await session.wait()
        if _is_aborted(options.signal):
            raise RuntimeError("Operation aborted")
        # rg 退出码：0=有匹配，1=无匹配，2=错误（坏正则/坏 glob/IO 错误）；
        # 错误时把 stderr 透出，不再静默当作"无匹配"
        if not match_limit_reached and exit_code not in (0, 1):
            detail = await session.stderr_text()
            raise RuntimeError(detail or f"rg exited with code {exit_code}")
        return results, match_limit_reached

    async def _collect_with_walk(
        self, path: str, is_dir: bool, options: GrepOptions
    ) -> Tuple[List[GrepMatch], bool]:
        """便携引擎（layer.walk + read + 逐行正则——远程与本机无 rg 共用）。

        与原 Python 兜底同语义：默认含隐藏文件；glob 作用于 basename；
        不可读文件跳过。读取经**有界并发保序流水线**（窗口
        ``_SEARCH_CONCURRENCY``，按目标序逐个 drain——结果顺序与串行
        一致），达整体 limit 即停读剩余（远程 WS 场景延迟摊薄的关键，
        本地同享）。
        """
        flags = re.IGNORECASE if options.ignore_case else 0
        pattern_str = re.escape(options.pattern) if options.literal else options.pattern
        pattern = re.compile(pattern_str, flags)
        limit = max(1, options.limit)

        if is_dir:
            walk = await self._fs.walk(path)
            targets = [
                item.path
                for item in walk.entries
                if not item.is_dir
                and (
                    not options.glob
                    or fnmatch.fnmatch(os.path.basename(item.path), options.glob)
                )
            ]
        else:
            targets = [path]

        async def _scan(file_path: str) -> List[GrepMatch]:
            try:
                raw = await self._fs.read_bytes(file_path)
            except Exception:
                return []
            text = raw.decode("utf-8", errors="replace")
            return [
                GrepMatch(path=file_path, line=lineno, text=line.rstrip("\n"))
                for lineno, line in enumerate(text.split("\n"), 1)
                if pattern.search(line)
            ]

        results: List[GrepMatch] = []
        target_iter = iter(targets)
        inflight: List[asyncio.Task] = []

        def _fill() -> None:
            while len(inflight) < _SEARCH_CONCURRENCY:
                next_target = next(target_iter, None)
                if next_target is None:
                    return
                inflight.append(asyncio.ensure_future(_scan(next_target)))

        try:
            _fill()
            while inflight:
                if _is_aborted(options.signal):
                    raise RuntimeError("Operation aborted")
                # 按目标序逐个 drain（保序）；窗口随即补位（并发）
                matches = await inflight.pop(0)
                _fill()
                for match in matches:
                    results.append(match)
                    if len(results) >= limit:
                        return results, True
        finally:
            for task in inflight:
                task.cancel()
        return results, False

    async def _render(
        self,
        matches: List[GrepMatch],
        path: str,
        is_dir: bool,
        options: GrepOptions,
    ) -> Tuple[List[str], bool]:
        """按 行格式渲染：匹配行 ``path:N: text``，上下文行 ``path-N- text``。"""

        def format_path(file_path: str) -> str:
            if is_dir:
                rel = os.path.relpath(file_path, path)
                if rel and not rel.startswith(".."):
                    return rel.replace(os.sep, "/")
            return os.path.basename(file_path)

        # context > 0 时按需读文件（带缓存）；context == 0 直接用 rg 给出的行文本
        file_cache: Dict[str, List[str]] = {}

        async def get_file_lines(file_path: str) -> List[str]:
            if file_path not in file_cache:
                try:
                    raw = await self._fs.read_bytes(file_path)
                    content = raw.decode("utf-8", errors="replace")
                    file_cache[file_path] = (
                        content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                    )
                except Exception:
                    file_cache[file_path] = []
            return file_cache[file_path]

        output_lines: List[str] = []
        lines_truncated = False

        async def render_block(match: GrepMatch) -> None:
            nonlocal lines_truncated
            relative = format_path(match.path)
            if options.context <= 0:
                sanitized = match.text.replace("\r\n", "\n").replace("\r", "")
                text, was_truncated = truncate_line(sanitized)
                lines_truncated = lines_truncated or was_truncated
                output_lines.append(f"{relative}:{match.line}: {text}")
                return
            file_lines = await get_file_lines(match.path)
            if not file_lines:
                output_lines.append(f"{relative}:{match.line}: (unable to read file)")
                return
            start = max(1, match.line - options.context)
            end = min(len(file_lines), match.line + options.context)
            for current in range(start, end + 1):
                sanitized = file_lines[current - 1].replace("\r", "")
                text, was_truncated = truncate_line(sanitized)
                lines_truncated = lines_truncated or was_truncated
                if current == match.line:
                    output_lines.append(f"{relative}:{current}: {text}")
                else:
                    output_lines.append(f"{relative}-{current}- {text}")

        for match in matches:
            await render_block(match)
        return output_lines, lines_truncated


def create_local_grep_operations(
    fs: Optional[FileSystemLayer] = None,
    runner: Optional[Any] = None,
) -> GrepOperations:
    """本地缺省：本机 layer + 本机 ProcessRunner（rg 三级解析）。"""
    if runner is None:
        from nova_coding_agent.tools_common.process_runner import LocalProcessRunner

        runner = LocalProcessRunner()
    return LocalGrepOperations(fs, runner)


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
    signal: Any = None  # abort 信号


def _inside_git_repo(start: str) -> bool:
    """从 start 向上查找 .git。"""
    current = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _relativize(results: List[str], search_path: str) -> List[str]:
    """结果相对化到搜索根（posix 分隔符）。"""
    base = os.path.abspath(search_path)
    relativized: List[str] = []
    for item in results:
        try:
            rel = os.path.relpath(item, base)
        except ValueError:
            rel = item
        relativized.append(rel.replace(os.sep, "/"))
    return relativized


class FindOperations(Protocol):
    """Find tool 的可插拔 operations。"""

    @abstractmethod
    async def find(self, options: FindOptions) -> List[str]:
        """查找文件或目录，返回**相对搜索根**的路径列表（posix 分隔符）。"""


class LocalFindOperations:
    """FindOperations 实现（参数化 FileSystemLayer + ProcessRunner——
    本地/远程同一份实现）。

    加速链（归 runner 供给，缺省/为 None 时落便携引擎）：fd → rg
    ``--files`` → walk 便携。fd 调用``--hidden`` 包含
    隐藏文件、git 仓库外 ``--no-require-git``、带 ``/`` 的 pattern 走
    ``--full-path``；目录查找是 Nova 超集（rg 列不了目录）。远程
    runner 的 fd 恒 None（v1）——远程 find 走 rg --files 或便携引擎。
    """

    def __init__(
        self,
        fs: Optional[FileSystemLayer] = None,
        runner: Optional[Any] = None,
    ) -> None:
        self._fs = fs or get_local_file_system_layer()
        self._runner = runner

    async def find(self, options: FindOptions) -> List[str]:
        if _is_aborted(options.signal):
            raise RuntimeError("Operation aborted")
        # 路径不存在统一前置报错（各路径同语义），不再静默误报无结果
        if not (await self._fs.metadata(options.path)).exists:
            raise RuntimeError(f"Path not found: {options.path}")
        if self._runner is not None:
            fd_path = await self._runner.fd_path()
            if fd_path:
                return await self._find_with_fd(fd_path, options)
            rg_path = await self._runner.rg_path()
            if rg_path and options.find_type == "file":
                return await self._find_with_rg(rg_path, options)
        return await self._find_with_walk(options)

    async def _find_with_fd(self, fd_path: str, options: FindOptions) -> List[str]:
        assert self._runner is not None
        search_path = os.path.abspath(options.path)
        args = [fd_path, "--glob", "--color=never", "--hidden", "--absolute-path"]
        if not _inside_git_repo(search_path):
            # fd 在仓库外默认忽略 .gitignore 规则；处理
            args.append("--no-require-git")
        args.extend(["--max-results", str(max(1, options.limit))])
        args.extend(["--type", "d" if options.find_type == "directory" else "f"])

        # pattern 缺省时以 "*" 匹配全部（fd 的第一个位置参数是 pattern 而非路径，
        # 不传 pattern 会把搜索根误当正则）
        pattern = options.pattern or "*"
        # fd --glob 默认只匹配 basename；带 / 的 pattern 需要 --full-path
        # （此时匹配绝对候选路径，非 ** 开头的 pattern 要补 **/ 前缀）
        if "/" in pattern:
            args.append("--full-path")
            if not pattern.startswith(("/", "**/")) and pattern != "**":
                pattern = f"**/{pattern}"
        args.extend(["--", pattern, search_path])

        session = await self._runner.spawn(args, search_path)
        results: List[str] = []
        async for text in _session_lines_with_abort(session, options.signal):
            if text:
                results.append(text)
        # 仅 abort 主动 terminate；正常读到 EOF 时 fd 已收尾，
        # 盲目 kill 会把真实退出码抹成 -9
        if _is_aborted(options.signal):
            await session.terminate()
        exit_code = await session.wait()
        if _is_aborted(options.signal):
            raise RuntimeError("Operation aborted")
        # fd 与 rg 语义不同：无结果也退出 0，非 0 即错误
        # （坏 glob / 无效搜索路径，实测退出码 1）；但非零退出时若已有产出，
        # 保留部分结果（仅无输出才把 stderr 透出为错误）
        if exit_code != 0 and not results:
            detail = await session.stderr_text()
            raise RuntimeError(detail or f"fd exited with code {exit_code}")
        return _relativize(results, search_path)

    async def _find_with_rg(self, rg_path: str, options: FindOptions) -> List[str]:
        """用 ``rg --files`` 顶替 fd（性能实测打平，同一遍历引擎）。

        rg 没有 --max-results 语义：读够 limit 行后 terminate（对齐 grep 的处理）。
        """
        assert self._runner is not None
        search_path = os.path.abspath(options.path)
        args = [rg_path, "--files", "--hidden"]
        if options.pattern:
            args.extend(["--glob", options.pattern])
        args.extend(["--", search_path])

        session = await self._runner.spawn(args, search_path)
        limit = max(1, options.limit)
        results: List[str] = []
        async for text in _session_lines_with_abort(session, options.signal):
            if text:
                results.append(text)
            if len(results) >= limit or _is_aborted(options.signal):
                break
        # 仅在主动停止（读够 limit/abort）时 terminate；读到 EOF 说明 rg 已
        # 收尾，盲目 kill 会把真实退出码抹成 -9
        if len(results) >= limit or _is_aborted(options.signal):
            await session.terminate()
        exit_code = await session.wait()
        if _is_aborted(options.signal):
            raise RuntimeError("Operation aborted")
        # rg 退出码：0=有结果，1=无结果，2=错误（坏 glob/IO 错误）；
        # 读够 limit 主动终止的场景退出码无意义，跳过检查
        if len(results) < limit and exit_code not in (0, 1):
            detail = await session.stderr_text()
            raise RuntimeError(detail or f"rg exited with code {exit_code}")
        return _relativize(results, search_path)

    async def _find_with_walk(self, options: FindOptions) -> List[str]:
        """便携引擎（layer.walk——远程后端与本机无 fd/rg 时共用）。"""
        walk = await self._fs.walk(options.path)
        limit = max(1, options.limit)
        results: List[str] = []
        for item in walk.entries:
            if _is_aborted(options.signal):
                raise RuntimeError("Operation aborted")
            if options.find_type == "directory" and not item.is_dir:
                continue
            if options.find_type == "file" and item.is_dir:
                continue
            # 与原 pathlib 兜底同语义：pattern 作用于 PurePath.match
            if options.pattern and not PurePath(item.path).match(options.pattern):
                continue
            results.append(item.path)
            if len(results) >= limit:
                break
        return _relativize(results, options.path)


def create_local_find_operations(
    fs: Optional[FileSystemLayer] = None,
    runner: Optional[Any] = None,
) -> FindOperations:
    """本地缺省：本机 layer + 本机 ProcessRunner（fd/rg 三级解析）。"""
    if runner is None:
        from nova_coding_agent.tools_common.process_runner import LocalProcessRunner

        runner = LocalProcessRunner()
    return LocalFindOperations(fs, runner)


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
    """LsOperations 实现（参数化 FileSystemLayer——本地/远程同构）。

    排序/截断语义在本地实现；目录枚举与错误形态（不存在/非目录）归
    layer 双实现同语义（``FileNotFoundError``/``NotADirectoryError``）。
    """

    def __init__(self, fs: Optional[FileSystemLayer] = None) -> None:
        self._fs = fs or get_local_file_system_layer()

    async def list_dir(self, options: LsOptions) -> Tuple[List[LsEntry], bool]:
        raw = await self._fs.list_dir(options.path)
        raw.sort(key=lambda entry: entry.name.lower())
        entries: List[LsEntry] = []
        truncated = False
        for entry in raw:
            # 达 limit 即停
            if len(entries) >= options.limit:
                truncated = True
                break
            entries.append(LsEntry(name=entry.name, is_directory=entry.is_dir))
        return entries, truncated


def create_local_ls_operations(fs: Optional[FileSystemLayer] = None) -> LsOperations:
    return LocalLsOperations(fs)


__all__ = [
    "ReadOperations",
    "ReadResult",
    "create_local_read_operations",
    "detect_image_mime_type",
    "WriteOperations",
    "WriteResult",
    "create_local_write_operations",
    "EditOperations",
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
