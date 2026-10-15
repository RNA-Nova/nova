"""fs.walk 的 -32601（旧服务端无端点）原语合成回退测试：对位 Rust
`RemoteFileSystem::walk` 的 `walk_via_directory_reads` 回退——
getMetadata/canonicalize/readDirectory 组合出等价语义（BFS、fileName 排序、
深度/目录数/条目数/响应字节四帽截断、单目录失败收编 WalkError）"""

from __future__ import annotations

import pytest
from fake_transport import FakeTransport
from nova_protocol import FileSystemSandboxContext, WalkOptions

from nova_exec_server_client import FileSystemError, ProtocolError
from nova_exec_server_client.errors import (
    EXECUTOR_NOT_FOUND,
    JSON_RPC_INTERNAL_ERROR,
    JSON_RPC_METHOD_NOT_FOUND,
)
from nova_exec_server_client.fs import FileSystemManager


def _meta(is_dir=False, is_file=False, is_symlink=False, size=0) -> dict:
    return {
        "isDirectory": is_dir,
        "isFile": is_file,
        "isSymlink": is_symlink,
        "size": size,
        "createdAtMs": 0,
        "modifiedAtMs": 0,
    }


class FakeLegacyServer(FakeTransport):
    """无 fs/walk 端点的旧服务端（walk 回 -32601）：getMetadata/readDirectory/
    canonicalize 原语按注册的内存目录树应答"""

    def __init__(self, *, walk_error: Exception | None = None):
        super().__init__()
        self._dirs: dict[str, list[dict]] = {}
        self._meta: dict[str, dict] = {}
        self._canonical: dict[str, str] = {}
        self._walk_error = walk_error or ProtocolError(
            "method not found: fs/walk", code=JSON_RPC_METHOD_NOT_FOUND
        )
        #: 原语调用日志（sandbox 透传/canonicalize 调用断言用）
        self.primitive_calls: list[tuple[str, dict]] = []

    def add_dir(self, path: str, children: list[tuple[str, str]]) -> None:
        """注册目录：children 为 [(fileName, kind)]，kind ∈ file/dir/symlink-dir/
        symlink-file；子项元数据一并注册（子目录的 listing 另需 add_dir）"""
        self._meta[path] = _meta(is_dir=True)
        listing = []
        for name, kind in children:
            child = f"{path}/{name}"
            is_dir = kind in ("dir", "symlink-dir")
            listing.append(
                {"fileName": name, "isDirectory": is_dir, "isFile": kind == "file"}
            )
            self._meta[child] = _meta(
                is_dir=is_dir,
                is_file=kind in ("file", "symlink-file"),
                is_symlink=kind.startswith("symlink"),
            )
        self._dirs[path] = listing

    def add_file(self, path: str, size: int = 0) -> None:
        self._meta[path] = _meta(is_file=True, size=size)

    def add_canonical(self, path: str, canonical: str) -> None:
        self._canonical[path] = canonical

    def alias_dir(self, alias: str, target: str) -> None:
        """让 alias 路径共享 target 的目录 listing 与子项元数据（符号链接
        目录场景：readDirectory/getMetadata 经链接路径照常应答）"""
        self._dirs[alias] = self._dirs[target]
        for path, meta in list(self._meta.items()):
            if path.startswith(target + "/"):
                self._meta[alias + path[len(target) :]] = meta

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False
    ):
        params = params or {}
        if method == "fs/walk":
            raise self._walk_error
        if method == "fs/getMetadata":
            self.primitive_calls.append((method, params))
            meta = self._meta.get(params["path"])
            if meta is None:
                raise ProtocolError("not found", code=EXECUTOR_NOT_FOUND)
            return meta
        if method == "fs/readDirectory":
            self.primitive_calls.append((method, params))
            listing = self._dirs.get(params["path"])
            if listing is None:
                raise ProtocolError("not a directory", code=EXECUTOR_NOT_FOUND)
            return {"entries": listing}
        if method == "fs/canonicalize":
            self.primitive_calls.append((method, params))
            return {"path": self._canonical.get(params["path"], params["path"])}
        return await super().send_request(method, params, channel=channel)


def make_tree() -> FakeLegacyServer:
    """标准目录树：root 下 a.txt/b.txt/sub(c.txt)"""
    server = FakeLegacyServer()
    server.add_dir(
        "file:///root",
        [("b.txt", "file"), ("a.txt", "file"), ("sub", "dir")],
    )
    server.add_dir("file:///root/sub", [("c.txt", "file")])
    return server


@pytest.mark.asyncio
async def test_walk_fallback_on_method_not_found():
    """fs/walk 回 -32601 → 原语合成遍历：BFS、每目录按 fileName 排序"""
    server = make_tree()
    fs = FileSystemManager(server)

    outcome = await fs.walk("file:///root")

    assert [(e.path, e.kind) for e in outcome.entries] == [
        ("file:///root/a.txt", "file"),
        ("file:///root/b.txt", "file"),
        ("file:///root/sub", "directory"),
        ("file:///root/sub/c.txt", "file"),
    ]
    assert outcome.errors == []
    assert outcome.truncated is False


@pytest.mark.asyncio
async def test_walk_fallback_respects_max_entries():
    """条目数帽：达到 maxEntries 即截断（truncated=True，对位 Rust 的
    `entry_count == max_entries → truncated` 提前返回）"""
    server = make_tree()
    fs = FileSystemManager(server)

    outcome = await fs.walk("file:///root", WalkOptions(max_entries=2))

    assert [e.path for e in outcome.entries] == [
        "file:///root/a.txt",
        "file:///root/b.txt",
    ]
    assert outcome.truncated is True


@pytest.mark.asyncio
async def test_walk_fallback_respects_max_depth():
    """深度帽：maxDepth=0 只列根目录直接子项（子目录列出但不下降）"""
    server = make_tree()
    fs = FileSystemManager(server)

    outcome = await fs.walk("file:///root", WalkOptions(max_depth=0))

    assert [e.path for e in outcome.entries] == [
        "file:///root/a.txt",
        "file:///root/b.txt",
        "file:///root/sub",
    ]
    assert outcome.truncated is False


@pytest.mark.asyncio
async def test_walk_fallback_prune_hidden_directories():
    """pruneHiddenDirectories：隐藏目录仍列出为条目但不下降（对位 Rust 的
    `entry.file_name.starts_with('.')` 剪枝点）"""
    server = FakeLegacyServer()
    server.add_dir(
        "file:///root",
        [(".hidden", "dir"), ("visible", "dir")],
    )
    server.add_dir("file:///root/.hidden", [("d.txt", "file")])
    server.add_dir("file:///root/visible", [("e.txt", "file")])
    fs = FileSystemManager(server)

    outcome = await fs.walk("file:///root", WalkOptions(prune_hidden_directories=True))

    assert [e.path for e in outcome.entries] == [
        "file:///root/.hidden",
        "file:///root/visible",
        "file:///root/visible/e.txt",
    ]


@pytest.mark.asyncio
async def test_walk_fallback_directory_error_collected_not_fatal():
    """单目录 readDirectory 失败收编为 WalkError（对位 Rust push_walk_error），
    遍历继续而非整请求失败"""
    server = FakeLegacyServer()
    server.add_dir(
        "file:///root",
        [("a.txt", "file"), ("broken", "dir")],
    )
    # broken 未注册 listing → readDirectory 失败
    fs = FileSystemManager(server)

    outcome = await fs.walk("file:///root")

    assert [e.path for e in outcome.entries] == [
        "file:///root/a.txt",
        "file:///root/broken",
    ]
    assert len(outcome.errors) == 1
    assert outcome.errors[0].path == "file:///root/broken"
    assert outcome.truncated is False


@pytest.mark.asyncio
async def test_walk_fallback_non_directory_root_returns_empty():
    """根不是目录（或根是指向文件的符号链接且不跟随）→ 空结果（对位 Rust
    `!is_directory || (is_symlink && !follow)` 早退）"""
    server = FakeLegacyServer()
    server.add_file("file:///root/f.txt", size=3)
    fs = FileSystemManager(server)

    outcome = await fs.walk("file:///root/f.txt")

    assert outcome.entries == [] and outcome.errors == []
    # 早退：只查了一次根 metadata，没有 readDirectory
    assert [m for m, _ in server.primitive_calls] == ["fs/getMetadata"]


@pytest.mark.asyncio
async def test_walk_fallback_zero_limits_rejected():
    """零上限即参数错误（对位 Rust InvalidInput——本地执法，不打线上）"""
    server = make_tree()
    fs = FileSystemManager(server)

    with pytest.raises(FileSystemError, match="greater than zero"):
        await fs.walk("file:///root", WalkOptions(max_entries=0))
    with pytest.raises(FileSystemError, match="greater than zero"):
        await fs.walk("file:///root", WalkOptions(max_directories=0))


@pytest.mark.asyncio
async def test_walk_fallback_over_cap_limits_rejected():
    """超服务端上限同样本地拒（对位 Rust 的 MAX_WALK_* 检查）"""
    server = make_tree()
    fs = FileSystemManager(server)

    with pytest.raises(FileSystemError, match="exceed maximums"):
        await fs.walk("file:///root", WalkOptions(max_depth=65))


@pytest.mark.asyncio
async def test_walk_fallback_symlink_rules():
    """符号链接：默认不跟随（link 不进条目）；followDirectorySymlinks=True 时
    跟随且经 canonicalize 判重——同一目标只下降一次（环防护）"""
    server = FakeLegacyServer()
    server.add_dir(
        "file:///root",
        [("link", "symlink-dir"), ("real", "dir")],
    )
    server.add_dir("file:///root/real", [("c.txt", "file")])
    server.alias_dir("file:///root/link", "file:///root/real")
    server.add_canonical("file:///root/link", "file:///root/real")
    server.add_canonical("file:///root/real", "file:///root/real")
    fs = FileSystemManager(server)

    # 默认不跟随：link 被跳过（不进条目，对位 Rust 的 symlink continue）
    outcome = await fs.walk("file:///root")
    assert [e.path for e in outcome.entries] == [
        "file:///root/real",
        "file:///root/real/c.txt",
    ]

    # 跟随：link 进条目并下降；real 与 link 同目标——visited 判重只下降一次
    outcome = await fs.walk("file:///root", WalkOptions(follow_directory_symlinks=True))
    assert [(e.path, e.kind) for e in outcome.entries] == [
        ("file:///root/link", "directory"),
        ("file:///root/real", "directory"),
        ("file:///root/link/c.txt", "file"),
    ]
    assert "fs/canonicalize" in [m for m, _ in server.primitive_calls]


@pytest.mark.asyncio
async def test_walk_fallback_other_errors_propagate():
    """非 -32601 的 fs/walk 错误原样上抛（不回退——对位 Rust 的 Err(error)
    直通分支）"""
    server = FakeLegacyServer(
        walk_error=ProtocolError("internal boom", code=JSON_RPC_INTERNAL_ERROR)
    )
    fs = FileSystemManager(server)

    with pytest.raises(ProtocolError, match="internal boom"):
        await fs.walk("file:///root")
    # 未走原语合成
    assert server.primitive_calls == []


@pytest.mark.asyncio
async def test_walk_fallback_sandbox_passes_through():
    """回退路径的原语调用同样透传沙箱载荷（句柄族全透传纪律）"""
    server = make_tree()
    fs = FileSystemManager(server)
    ctx = FileSystemSandboxContext.read_only("/tmp/proj")
    wire = ctx.model_dump(by_alias=True, exclude_none=True)

    await fs.walk("file:///root", sandbox=ctx)

    assert server.primitive_calls, "回退路径应产生原语调用"
    for _method, params in server.primitive_calls:
        assert params["sandbox"] == wire


@pytest.mark.asyncio
async def test_walk_direct_success_not_fallen_back():
    """直连 fs/walk 成功（服务端有端点）不走回退：原语零调用"""
    server = make_tree()

    async def direct_walk(method, params=None, *, channel=None, for_cleanup=False):
        if method == "fs/walk":
            return {
                "entries": [{"path": "file:///root/x", "kind": "file"}],
                "errors": [],
                "truncated": False,
            }
        return await FakeLegacyServer.send_request(
            server, method, params, channel=channel
        )

    server.send_request = direct_walk  # type: ignore[method-assign]
    fs = FileSystemManager(server)

    outcome = await fs.walk("file:///root")

    assert [e.path for e in outcome.entries] == ["file:///root/x"]
    assert server.primitive_calls == []
