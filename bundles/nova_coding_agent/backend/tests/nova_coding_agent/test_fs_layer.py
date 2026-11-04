"""FileSystemLayer 测试：Local 实现、Executor 实现（fake client.fs）、
resolve_backend_path 与 backend_file_layer 的后端解析。
"""

import asyncio
import errno

import pytest
from nova_coding_agent.executor import (
    BackendSelection,
    reset_backend_selection,
    set_backend_selection,
)
from nova_coding_agent.executor.fs_layer import (
    ExecutorFileSystemLayer,
    reset_executor_file_layers,
)
from nova_coding_agent.executor.runtime import backend_file_layer, resolve_backend_path
from nova_coding_agent.tools_common.fs_layer import (
    FsEntry,
    FsStat,
    LocalFileSystemLayer,
    WalkItem,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_backend_selection()
    reset_executor_file_layers()
    yield
    reset_backend_selection()
    reset_executor_file_layers()


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# LocalFileSystemLayer
# ---------------------------------------------------------------------------


class TestLocalLayer:
    def test_metadata_and_read(self, tmp_path):
        layer = LocalFileSystemLayer()
        target = tmp_path / "a.txt"
        target.write_bytes(b"hello world")
        stat = _run(layer.metadata(str(target)))
        assert stat.exists and stat.is_file and not stat.is_dir
        assert stat.size == 11
        missing = _run(layer.metadata(str(tmp_path / "nope")))
        assert missing.exists is False
        assert _run(layer.read_bytes(str(target))) == b"hello world"
        assert _run(layer.read_range(str(target), 0, 5)) == b"hello"

    def test_list_dir_error_forms(self, tmp_path):
        layer = LocalFileSystemLayer()
        target = tmp_path / "f.txt"
        target.write_text("x")
        with pytest.raises(NotADirectoryError):
            _run(layer.list_dir(str(target)))
        with pytest.raises(FileNotFoundError):
            _run(layer.list_dir(str(tmp_path / "nope")))

    def test_walk_dir_and_file_self(self, tmp_path):
        layer = LocalFileSystemLayer()
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.py").write_text("b")
        (tmp_path / "a.py").write_text("a")
        result = _run(layer.walk(str(tmp_path)))
        paths = {item.path for item in result.entries}
        assert str(tmp_path / "a.py") in paths
        assert str(tmp_path / "sub" / "b.py") in paths
        assert any(item.is_dir for item in result.entries)
        # 单文件路径 walk 即自身
        single = _run(layer.walk(str(tmp_path / "a.py")))
        assert [i.path for i in single.entries] == [str(tmp_path / "a.py")]
        with pytest.raises(FileNotFoundError):
            _run(layer.walk(str(tmp_path / "nope")))

    def test_check_writable_errno(self, tmp_path):
        layer = LocalFileSystemLayer()
        with pytest.raises(FileNotFoundError) as excinfo:
            _run(layer.check_writable(str(tmp_path / "nope")))
        assert excinfo.value.errno == errno.ENOENT


# ---------------------------------------------------------------------------
# ExecutorFileSystemLayer（fake client.fs）
# ---------------------------------------------------------------------------


class _FakeFs:
    def __init__(self):
        self.files = {}
        self.uris = []
        # 逐方法记录收到的 signal（(方法名, signal)——layer → SDK 透传链验证用）
        self.seen_signals = []

    def _track(self, path):
        self.uris.append(path)
        return path

    async def read_file(self, path, signal=None):
        self.seen_signals.append(("read_file", signal))
        path = self._track(path)
        if path not in self.files:
            raise RuntimeError("not found")
        return self.files[path]

    async def write_file(self, path, data, signal=None):
        self.seen_signals.append(("write_file", signal))
        self.files[self._track(path)] = data

    async def open(self, path, signal=None):
        self.seen_signals.append(("open", signal))
        self._track(path)
        return "h1"

    async def read_block(self, handle, offset, length, signal=None):
        self.seen_signals.append(("read_block", signal))
        data = self.files.get("file:///r/f.bin", b"")
        return data[offset : offset + length], True

    async def close(self, handle):
        # 收摊调用：签名有意不收 signal——layer 把 signal 传进来即 TypeError 判红
        pass

    async def metadata(self, path, signal=None):
        from nova_protocol import FileMetadata

        self.seen_signals.append(("metadata", signal))
        # 取消词汇：预触发信号按 SDK 举旗点语义抛 AbortedError（仿真 SDK 行为）
        if signal is not None and getattr(signal, "aborted", False):
            from nova_protocol import AbortedError

            raise AbortedError("Operation aborted: test")
        self._track(path)
        if path in self.files:
            return FileMetadata(
                isDirectory=False,
                isFile=True,
                isSymlink=False,
                size=len(self.files[path]),
                createdAtMs=0,
                modifiedAtMs=42,
            )
        if path == "file:///r":
            return FileMetadata(
                isDirectory=True,
                isFile=False,
                isSymlink=False,
                size=0,
                createdAtMs=0,
                modifiedAtMs=0,
            )
        raise RuntimeError("not found")

    async def read_dir(self, path, signal=None):
        from nova_protocol import DirEntry

        self.seen_signals.append(("read_dir", signal))
        self._track(path)
        return [
            DirEntry(fileName="b.txt", isDirectory=False, isFile=True),
            DirEntry(fileName="sub", isDirectory=True, isFile=False),
        ]

    async def create_dir(self, path, recursive=True, signal=None):
        self.seen_signals.append(("create_dir", signal))
        self._track(path)

    async def walk(self, path, options, signal=None):
        from nova_protocol import WalkOutcome

        self.seen_signals.append(("walk", signal))
        self._track(path)
        assert options.max_entries == 123
        return WalkOutcome(
            entries=[
                {"path": "file:///r/a.py", "kind": "file"},
                {"path": "file:///r/sub", "kind": "directory"},
            ],
            errors=[],
            truncated=False,
        )


class _FakeClient:
    def __init__(self, fs):
        self.fs = fs


class _FakeManager:
    def __init__(self, client):
        self._client = client

    async def get_client(self, url=None):
        return self._client


class TestExecutorLayer:
    def _make(self):
        fs = _FakeFs()
        layer = ExecutorFileSystemLayer(_FakeManager(_FakeClient(fs)), "ssh://u@h")
        return layer, fs

    def test_read_write_uri_wrapping(self):
        layer, fs = self._make()
        _run(layer.write_bytes("/r/a.txt", b"data"))
        assert fs.files["file:///r/a.txt"] == b"data"
        assert _run(layer.read_bytes("/r/a.txt")) == b"data"
        assert "file:///r/a.txt" in fs.uris

    def test_metadata_missing_is_not_error(self):
        layer, _fs = self._make()
        assert _run(layer.metadata("/r/none")).exists is False
        fs = _FakeFs()
        fs.files["file:///r/a.txt"] = b"xyz"
        layer = ExecutorFileSystemLayer(_FakeManager(_FakeClient(fs)), "ssh://u@h")
        stat = _run(layer.metadata("/r/a.txt"))
        assert stat.exists and stat.is_file and stat.size == 3 and stat.mtime_ms == 42

    def test_list_dir_error_forms(self):
        layer, _fs = self._make()
        with pytest.raises(FileNotFoundError):
            _run(layer.list_dir("/r/none"))
        fs = _FakeFs()
        fs.files["file:///r/f.txt"] = b"x"
        layer = ExecutorFileSystemLayer(_FakeManager(_FakeClient(fs)), "ssh://u@h")
        with pytest.raises(NotADirectoryError):
            _run(layer.list_dir("/r/f.txt"))
        entries = _run(layer.list_dir("/r"))
        assert entries == [
            FsEntry(name="b.txt", is_dir=False),
            FsEntry(name="sub", is_dir=True),
        ]

    def test_walk_strips_uri_and_maps_kind(self):
        layer, _fs = self._make()
        result = _run(layer.walk("/r", max_entries=123))
        assert result.entries == (
            WalkItem(path="/r/a.py", is_dir=False),
            WalkItem(path="/r/sub", is_dir=True),
        )
        # 单文件路径 walk 即自身（不发 walk 请求）
        fs = _FakeFs()
        fs.files["file:///r/a.py"] = b"x"
        layer = ExecutorFileSystemLayer(_FakeManager(_FakeClient(fs)), "ssh://u@h")
        single = _run(layer.walk("/r/a.py"))
        assert [i.path for i in single.entries] == ["/r/a.py"]

    def test_signal_passthrough_to_sdk(self):
        """layer 全原语收到的 signal 原样透传进 SDK fs 调用（收摊 close 除外——
        fake close 签名不收 signal，误传即 TypeError 判红）。"""
        layer, fs = self._make()
        sentinel = object()
        fs.files["file:///r/a.txt"] = b"data"
        _run(layer.read_bytes("/r/a.txt", signal=sentinel))
        _run(layer.write_bytes("/r/a.txt", b"x", signal=sentinel))
        _run(layer.metadata("/r/a.txt", signal=sentinel))
        _run(layer.list_dir("/r", signal=sentinel))
        _run(layer.create_dir("/r/d", signal=sentinel))
        _run(layer.walk("/r", max_entries=123, signal=sentinel))
        _run(layer.read_range("/r/f.bin", 0, 4, signal=sentinel))
        _run(layer.check_writable("/r/a.txt", signal=sentinel))
        touched = {method for method, _ in fs.seen_signals}
        assert touched == {
            "read_file",
            "write_file",
            "metadata",
            "read_dir",
            "create_dir",
            "walk",
            "open",
            "read_block",
        }
        assert all(signal is sentinel for _, signal in fs.seen_signals)

    def test_metadata_abort_not_collapsed(self):
        """取消词汇直通：预触发 signal 的 AbortedError 不得收编成
        ``FsStat(exists=False)``（否则用户取消被误报成"文件不存在"）。"""
        from nova_protocol import AbortController, AbortedError

        layer, _fs = self._make()
        ctl = AbortController()
        ctl.abort()
        with pytest.raises(AbortedError):
            _run(layer.metadata("/r/a.txt", signal=ctl.signal))


# ---------------------------------------------------------------------------
# 后端解析（resolve_backend_path / backend_file_layer）
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, cwd):
        self.cwd = cwd


class TestBackendResolution:
    def test_backend_file_layer_none_when_local(self):
        assert backend_file_layer() is None

    def test_backend_file_layer_none_when_local_sandbox(self):
        # 本地沙箱（url=None）：本地盘，不切远程层
        set_backend_selection(BackendSelection(backend="executor", url=None))
        assert backend_file_layer() is None

    def test_backend_file_layer_remote(self):
        set_backend_selection(
            BackendSelection(backend="executor", url="ssh://u@h", remote_cwd="/w")
        )
        layer = backend_file_layer()
        assert isinstance(layer, ExecutorFileSystemLayer)
        # 同 url 复用（缓存）
        assert backend_file_layer() is layer

    def test_resolve_local_relative(self, tmp_path):
        resolved = resolve_backend_path("a/b.txt", _Ctx(str(tmp_path)))
        assert resolved == str(tmp_path / "a" / "b.txt")

    def test_resolve_remote_relative_rooted_at_remote_cwd(self):
        set_backend_selection(
            BackendSelection(
                backend="executor",
                url="ssh://u@h",
                remote_cwd="/work/proj",
                remote_home="/home/u",
            )
        )
        assert resolve_backend_path("src/a.py", _Ctx("/local")) == "/work/proj/src/a.py"
        assert resolve_backend_path("/etc/hosts", _Ctx("/local")) == "/etc/hosts"
        assert resolve_backend_path("~/x.txt", _Ctx("/local")) == "/home/u/x.txt"
        assert resolve_backend_path("@./y.py", _Ctx("/local")) == "/work/proj/y.py"

    def test_resolve_remote_without_cwd_falls_back_home(self):
        set_backend_selection(
            BackendSelection(backend="executor", url="ssh://u@h", remote_home="/home/u")
        )
        assert resolve_backend_path("a.txt", _Ctx("/local")) == "/home/u/a.txt"
