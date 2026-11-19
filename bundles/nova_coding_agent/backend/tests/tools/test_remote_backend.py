"""六个 fs 工具的远程后端切换测试（内存 layer 注入）。

真实模式格翻转为远程 executor（resolve_backend_path 走真路径解析），
仅把 ``backend_file_layer`` 换为内存 layer——验证工具在远程后端下
解析远程路径并经由 fs 层读写（便携引擎、无本机 fd/rg）。
"""

import asyncio
import importlib.util
import os

import pytest

from nova_coding_agent.executor import (
    BackendSelection,
    reset_backend_selection,
    set_backend_selection,
)
from nova_coding_agent.tools_common.fs_layer import (
    FsEntry,
    FsStat,
    WalkItem,
    WalkResult,
)

_REMOTE_CWD = "/work/proj"


class MemLayer:
    """内存 FileSystemLayer（accelerates_search=False——便携引擎路径）。"""

    accelerates_search = False

    def __init__(self):
        self.files = {}
        # 逐方法记录收到的 signal（(方法名, signal)——工具 → layer 透传链验证用）
        self.seen_signals = []

    def _record(self, method, signal):
        self.seen_signals.append((method, signal))

    def _dirs(self):
        dirs = {"/"}
        for path in self.files:
            parts = path.split("/")
            for i in range(2, len(parts)):
                dirs.add("/".join(parts[:i]))
        return dirs

    async def read_bytes(self, path, signal=None):
        self._record("read_bytes", signal)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def read_range(self, path, offset, length, signal=None):
        return (await self.read_bytes(path, signal=signal))[offset : offset + length]

    async def write_bytes(self, path, data, signal=None):
        self._record("write_bytes", signal)
        self.files[path] = data

    async def metadata(self, path, signal=None):
        self._record("metadata", signal)
        if path in self.files:
            return FsStat(exists=True, is_file=True, size=len(self.files[path]))
        if path in self._dirs():
            return FsStat(exists=True, is_dir=True)
        return FsStat(exists=False)

    async def list_dir(self, path, signal=None):
        self._record("list_dir", signal)
        meta = await self.metadata(path, signal=signal)
        if not meta.exists:
            raise FileNotFoundError(path)
        if not meta.is_dir:
            raise NotADirectoryError(path)
        prefix = path.rstrip("/") + "/"
        dirs = self._dirs()
        names = {}
        for candidate in list(self.files) + list(dirs):
            if candidate.startswith(prefix):
                rest = candidate[len(prefix) :]
                if rest and "/" not in rest:
                    names[rest] = candidate in dirs and candidate not in self.files
        return [FsEntry(name=name, is_dir=is_dir) for name, is_dir in names.items()]

    async def create_dir(self, path, signal=None):
        self._record("create_dir", signal)

    async def walk(self, path, *, max_entries=100_000, signal=None):
        self._record("walk", signal)
        meta = await self.metadata(path, signal=signal)
        if not meta.exists:
            raise FileNotFoundError(path)
        if meta.is_file:
            return WalkResult(entries=(WalkItem(path=path, is_dir=False),))
        prefix = path.rstrip("/") + "/"
        items = [
            WalkItem(path=d, is_dir=True)
            for d in sorted(self._dirs())
            if d.startswith(prefix) and d != path
        ] + [
            WalkItem(path=f, is_dir=False)
            for f in sorted(self.files)
            if f.startswith(prefix)
        ]
        return WalkResult(entries=tuple(items))

    async def check_writable(self, path, signal=None):
        if not (await self.metadata(path, signal=signal)).exists:
            raise FileNotFoundError(path)


@pytest.fixture(autouse=True)
def _remote_backend():
    """翻转为远程 executor 后端（真实模式格——resolve_backend_path 全真）。"""
    set_backend_selection(
        BackendSelection(
            backend="executor",
            url="ws://u@remote",
            remote_cwd=_REMOTE_CWD,
            remote_home="/home/u",
        )
    )
    yield
    reset_backend_selection()


def _make_tool(name, layer):
    tool_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "tools", f"{name}.py"
    )
    spec = importlib.util.spec_from_file_location(f"_test_remote_{name}", tool_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.backend_file_layer = lambda: layer
    # 便携引擎路径：runner 恒 None（远程 rg 链另有专项测试）
    module.backend_process_runner = lambda: None
    from nova_harness.resources.tools import NULL_TOOL_SETTINGS, ToolContext

    return module.Tool(ToolContext(cwd="/local/cwd", settings=NULL_TOOL_SETTINGS))


def _run(coro):
    return asyncio.run(coro)


def test_write_then_read_roundtrip_remote():
    layer = MemLayer()
    write_tool = _make_tool("write", layer)
    result = _run(
        write_tool.execute(
            "id1", {"path": "notes/a.txt", "content": "hello remote"}, None
        )
    )
    assert not result.is_error
    # 相对路径以 remote_cwd 为根写入（不是本地 cwd）
    assert layer.files[f"{_REMOTE_CWD}/notes/a.txt"] == b"hello remote"

    read_tool = _make_tool("read", layer)
    result = _run(read_tool.execute("id2", {"path": "notes/a.txt"}, None))
    assert "hello remote" in result.content[0].text


def test_read_missing_remote():
    read_tool = _make_tool("read", MemLayer())
    result = _run(read_tool.execute("id", {"path": "nope.txt"}, None))
    assert result.is_error
    assert "文件不存在" in result.content[0].text


def test_edit_remote():
    layer = MemLayer()
    layer.files[f"{_REMOTE_CWD}/a.py"] = b"def old_name():\n    pass\n"
    edit_tool = _make_tool("edit", layer)
    result = _run(
        edit_tool.execute(
            "id",
            {
                "path": "a.py",
                "edits": [{"oldText": "old_name", "newText": "new_name"}],
            },
            None,
        )
    )
    assert not result.is_error
    assert layer.files[f"{_REMOTE_CWD}/a.py"] == b"def new_name():\n    pass\n"


def test_ls_remote():
    layer = MemLayer()
    layer.files[f"{_REMOTE_CWD}/b.txt"] = b"x"
    layer.files[f"{_REMOTE_CWD}/sub/c.txt"] = b"y"
    ls_tool = _make_tool("ls", layer)
    result = _run(ls_tool.execute("id", {"path": "."}, None))
    text = result.content[0].text
    assert "b.txt" in text and "sub/" in text


def test_grep_remote_portable_engine():
    layer = MemLayer()
    layer.files[f"{_REMOTE_CWD}/a.py"] = b"def alpha():\n    pass\n"
    layer.files[f"{_REMOTE_CWD}/b.md"] = b"no match here\n"
    grep_tool = _make_tool("grep", layer)
    result = _run(grep_tool.execute("id", {"pattern": "alpha", "path": "."}, None))
    assert "a.py:1: def alpha():" in result.content[0].text
    assert "b.md" not in result.content[0].text


def test_find_remote_portable_engine():
    layer = MemLayer()
    layer.files[f"{_REMOTE_CWD}/src/a.py"] = b"x"
    layer.files[f"{_REMOTE_CWD}/src/b.md"] = b"y"
    find_tool = _make_tool("find", layer)
    result = _run(find_tool.execute("id", {"path": ".", "pattern": "*.py"}, None))
    text = result.content[0].text
    assert "src/a.py" in text and "b.md" not in text


def test_tool_signal_reaches_file_layer():
    """工具的 AbortSignal 经 operations 透传到 fs 层（取消词汇链全程不断）。

    write/read/ls/grep/find 五链各跑一次（edit 链由 write/read 同层覆盖）：
    layer 每个被触达的原语收到的 signal 必须就是工具入参那个对象。
    """
    from nova_async import AbortController

    ctl = AbortController()
    layer = MemLayer()
    layer.files[f"{_REMOTE_CWD}/a.py"] = b"def alpha():\n    pass\n"

    write_tool = _make_tool("write", layer)
    result = _run(
        write_tool.execute("w", {"path": "b.txt", "content": "x"}, signal=ctl.signal)
    )
    assert not result.is_error

    read_tool = _make_tool("read", layer)
    result = _run(read_tool.execute("r", {"path": "a.py"}, signal=ctl.signal))
    assert not result.is_error

    ls_tool = _make_tool("ls", layer)
    result = _run(ls_tool.execute("l", {"path": "."}, signal=ctl.signal))
    assert not result.is_error

    grep_tool = _make_tool("grep", layer)
    result = _run(
        grep_tool.execute("g", {"pattern": "alpha", "path": "."}, signal=ctl.signal)
    )
    assert not result.is_error

    find_tool = _make_tool("find", layer)
    result = _run(
        find_tool.execute("f", {"path": ".", "pattern": "*"}, signal=ctl.signal)
    )
    assert not result.is_error

    touched = {method for method, _ in layer.seen_signals}
    # 五链触达的原语面：write(metadata/create_dir/write_bytes)、
    # read(metadata/read_bytes)、ls(list_dir)、grep/find(walk)
    assert {"metadata", "write_bytes", "read_bytes", "list_dir", "walk"} <= touched
    # 每一次触达收到的都是工具入参的同一个 signal 对象（断链即 None 混入）
    assert layer.seen_signals
    assert all(signal is ctl.signal for _, signal in layer.seen_signals)
