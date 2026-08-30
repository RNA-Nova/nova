"""ProcessRunner 缝测试：Local/Executor 双实现 + grep/find 的 runner 调度。

Executor 侧经假 manager/client/handle（不触真实 ssh/WS）；调度层验证
rg 路径存在时走 rg 链、缺失时落便携引擎。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from nova_coding_agent.executor.process_runner import (
    ExecutorProcessRunner,
    LocalProcessRunner,
)
from nova_coding_agent.tools_common.fs_layer import FsStat, WalkItem, WalkResult
from nova_coding_agent.tools_common.operations import (
    GrepOptions,
    LocalGrepOperations,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# LocalProcessRunner（真实子进程）
# ---------------------------------------------------------------------------


class TestLocalProcessRunner:
    def test_spawn_lines_wait_stderr(self):
        runner = LocalProcessRunner()

        async def go():
            session = await runner.spawn(
                ["sh", "-c", "printf 'a\\nb\\n'; printf 'oops\\n' >&2"], "."
            )
            lines = [line async for line in session.stdout_lines()]
            code = await session.wait()
            return lines, code, await session.stderr_text()

        lines, code, stderr = _run(go())
        assert lines == ["a", "b"]
        assert code == 0
        assert stderr == "oops"

    def test_terminate_kills(self):
        runner = LocalProcessRunner()

        async def go():
            session = await runner.spawn(["sleep", "30"], ".")
            await session.terminate()
            return await session.wait()

        assert _run(go()) != 0

    def test_rg_fd_path_resolution(self):
        runner = LocalProcessRunner()
        # 开发环境装了 rg/fd（binary 依赖），断言解析链可用即可
        assert _run(runner.rg_path()) is not None
        assert _run(runner.fd_path()) is not None


# ---------------------------------------------------------------------------
# ExecutorProcessRunner（假 client/handle）
# ---------------------------------------------------------------------------


class _FakeHandle:
    def __init__(self, chunks, exit_code=0):
        self._chunks = chunks
        self._exit_code = exit_code
        self.terminated = False

    async def output_with_stream(self):
        for stream, chunk in self._chunks:
            yield stream, chunk

    async def terminate(self):
        self.terminated = True

    async def read(self, wait_ms=0):
        return SimpleNamespace(exit_code=self._exit_code)


class _FakeProcess:
    def __init__(self, handle):
        self._handle = handle
        self.started = []

    async def start(self, **kw):
        self.started.append(kw)
        return self._handle


class _FakeClient:
    def __init__(self, handle):
        self.process = _FakeProcess(handle)


class _FakeManager:
    def __init__(self, client=None, ssh_handle=None):
        self._client = client
        self._ssh_handle = ssh_handle

    async def get_client(self, url=None):
        return self._client

    def get_ssh_handle(self, target):
        return self._ssh_handle


class TestExecutorProcessRunner:
    def test_rg_path_from_ssh_handle(self):
        handle = SimpleNamespace(rg_path="/usr/bin/rg")
        runner = ExecutorProcessRunner(_FakeManager(ssh_handle=handle), "ssh://u@h")
        assert _run(runner.rg_path()) == "/usr/bin/rg"

    def test_rg_path_none_for_ws_url_and_missing_handle(self):
        runner = ExecutorProcessRunner(_FakeManager(), "ws://127.0.0.1:1")
        assert _run(runner.rg_path()) is None
        runner2 = ExecutorProcessRunner(
            _FakeManager(ssh_handle=SimpleNamespace(rg_path="")), "ssh://u@h"
        )
        assert _run(runner2.rg_path()) is None

    def test_fd_path_remote_none(self):
        runner = ExecutorProcessRunner(_FakeManager(), "ssh://u@h")
        assert _run(runner.fd_path()) is None

    def test_spawn_session_line_split_and_stderr(self):
        chunks = [
            ("stdout", b'{"a":1}\n{"b":'),
            ("stderr", b"warn: something\n"),
            ("stdout", b"2}\n"),
        ]
        handle = _FakeHandle(chunks, exit_code=0)
        client = _FakeClient(handle)
        runner = ExecutorProcessRunner(_FakeManager(client=client), "ssh://u@h")

        async def go():
            session = await runner.spawn(["rg", "--json"], "/remote/work")
            lines = [line async for line in session.stdout_lines()]
            code = await session.wait()
            return lines, code, await session.stderr_text()

        lines, code, stderr = _run(go())
        # argv 与 cwd（file:// 包装）按约传递
        assert client.process.started == [
            {"argv": ["rg", "--json"], "cwd": "file:///remote/work", "env": {}}
        ]
        assert lines == ['{"a":1}', '{"b":2}']  # 跨 chunk 拼行
        assert stderr == "warn: something"  # 流标签分离
        assert code == 0


# ---------------------------------------------------------------------------
# grep/find 的 runner 调度（假 runner + 内存 layer）
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, lines, stderr="", exit_code=0):
        self._lines = lines
        self._stderr = stderr
        self._exit_code = exit_code
        self.terminated = False

    async def stdout_lines(self):
        for line in self._lines:
            yield line

    async def terminate(self):
        self.terminated = True

    async def wait(self):
        return self._exit_code

    async def stderr_text(self):
        return self._stderr


class _FakeRunner:
    def __init__(self, rg_path="/usr/bin/rg", session=None):
        self._rg_path = rg_path
        self.session = session or _FakeSession([])
        self.spawned = []

    async def rg_path(self):
        return self._rg_path

    async def fd_path(self):
        return None

    async def spawn(self, argv, cwd):
        self.spawned.append((argv, cwd))
        return self.session


class _MemLayer:
    def __init__(self, files=None, is_dir=True):
        self.files = files or {}
        self._is_dir = is_dir

    async def metadata(self, path):
        return FsStat(exists=True, is_dir=self._is_dir, is_file=not self._is_dir)

    async def walk(self, path, *, max_entries=50_000):
        return WalkResult(
            entries=tuple(WalkItem(path=p, is_dir=False) for p in self.files)
        )

    async def read_bytes(self, path):
        return self.files[path]


def _rg_line(path, line_no, text):
    return json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": path},
                "line_number": line_no,
                "lines": {"text": text + "\n"},
            },
        }
    )


class TestGrepRunnerDispatch:
    def test_rg_path_collects_json_matches(self):
        session = _FakeSession(
            [_rg_line("/w/a.py", 3, "def foo():"), _rg_line("/w/b.py", 7, "foo = 1")]
        )
        runner = _FakeRunner(session=session)
        ops = LocalGrepOperations(_MemLayer(), runner)
        matches, limit_reached = _run(
            ops._collect_with_rg("/usr/bin/rg", "/w", True, GrepOptions(pattern="foo"))
        )
        assert [(m.path, m.line, m.text) for m in matches] == [
            ("/w/a.py", 3, "def foo():"),
            ("/w/b.py", 7, "foo = 1"),
        ]
        assert limit_reached is False
        argv, cwd = runner.spawned[0]
        assert argv[0] == "/usr/bin/rg" and "--json" in argv and "foo" in argv
        assert cwd == "/w"

    def test_rg_limit_terminates(self):
        lines = [_rg_line("/w/a.py", i, "foo") for i in range(1, 10)]
        session = _FakeSession(lines)
        runner = _FakeRunner(session=session)
        ops = LocalGrepOperations(_MemLayer(), runner)
        matches, limit_reached = _run(
            ops._collect_with_rg(
                "/usr/bin/rg", "/w", True, GrepOptions(pattern="foo", limit=2)
            )
        )
        assert len(matches) == 2 and limit_reached is True
        assert session.terminated is True

    def test_rg_error_stderr_propagates(self):
        session = _FakeSession(
            [], stderr="regex parse error: unclosed group", exit_code=2
        )
        runner = _FakeRunner(session=session)
        ops = LocalGrepOperations(_MemLayer(), runner)
        with pytest.raises(RuntimeError, match="unclosed group"):
            _run(
                ops._collect_with_rg(
                    "/usr/bin/rg", "/w", True, GrepOptions(pattern="(")
                )
            )

    def test_no_rg_falls_back_to_portable(self):
        files = {"/w/a.py": b"foo\n"}
        runner = _FakeRunner(rg_path=None)
        ops = LocalGrepOperations(_MemLayer(files), runner)
        result = _run(ops.grep("/w", GrepOptions(pattern="foo")))
        assert result.match_count == 1
        assert runner.spawned == []  # 未走 rg 链


# ---------------------------------------------------------------------------
# 停读+杀 竞态回归 pin（_LocalSession.wait 轮询兜底）
# ---------------------------------------------------------------------------


def test_local_session_wait_survives_early_stop_and_kill():
    """回归 pin：消费端停读后 terminate/wait 不再永久挂起。

    大输出子进程只读一行即终止——管道写满 + 暂停态传输 + kill 的退出
    通知相撞时，修复前实测（Python 3.12 macOS）~1/10 概率 wait 丢唤
    永久挂起；修复后 returncode 轮询兜底必能在期限内返回。"""
    import os
    import sys

    runner = LocalProcessRunner()
    argv = [
        sys.executable,
        "-c",
        "import sys\nwhile True:\n    sys.stdout.write('x' * 200 + '\\n')\n"
        "    sys.stdout.flush()\n",
    ]

    async def scenario():
        session = await runner.spawn(argv, cwd=os.getcwd())
        first_line = await asyncio.wait_for(session.stdout_lines().__anext__(), 5)
        assert first_line.startswith("x")
        await session.terminate()
        # 修复前此处 ~1/10 概率永久挂起（wait_for 让竞态表现为红而非冻结）
        return await asyncio.wait_for(session.wait(), 5)

    rc = asyncio.run(asyncio.wait_for(scenario(), 15))
    assert isinstance(rc, int)
