"""ProcessRunner 缝测试：Local/Executor 双实现 + grep/find 的 runner 调度。

Executor 侧经假 manager/client/handle（不触真实 WS）；调度层验证
远程加速路径缺省时落便携引擎。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from nova_coding_agent.executor.process_runner import (
    ExecutorProcessRunner,
    LocalProcessRunner,
    _ExecutorSession,
)
from nova_coding_agent.tools_common.fs_layer import FsStat, WalkItem, WalkResult
from nova_coding_agent.tools_common.operations import GrepOptions, LocalGrepOperations


def _run(coro):
    return asyncio.run(coro)


async def _collect_lines(session):
    return [line async for line in session.stdout_lines()]


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
        # 开发环境装了 rg/fd（binary 依赖），断言解析链可用即可；
        # 无二进制的环境（CI runner/纯 Python 兜底）自行跳过——
        # 加速链缺失属环境属性，不算测试失败
        if _run(runner.rg_path()) is None or _run(runner.fd_path()) is None:
            pytest.skip("rg/fd 未安装（纯 Python 兜底环境）")


# ---------------------------------------------------------------------------
# ExecutorProcessRunner（假 client/handle）
# ---------------------------------------------------------------------------


class _FakeHandle:
    def __init__(self, chunks, exit_code=0):
        self._chunks = chunks
        self._exit_code = exit_code
        self.terminated = False
        self.stream_signals = []  # output_with_stream 收到的 signal（透传验证用）

    async def output_with_stream(self, *, signal=None):
        self.stream_signals.append(signal)
        for stream, chunk in self._chunks:
            yield stream, chunk

    async def terminate(self):
        self.terminated = True

    async def read(self, wait_ms=0):
        return SimpleNamespace(exit_code=self._exit_code)

    async def wait(self, timeout=None, *, signal=None):
        # 对齐 SDK ProcessHandle.wait 形状（轮询在 SDK 侧——fake 直接给码）
        return self._exit_code


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
    def __init__(self, client=None):
        self._client = client

    async def get_client(self, url=None):
        return self._client


class TestExecutorProcessRunner:
    def test_rg_path_none_without_ssh_provisioning(self):
        runner = ExecutorProcessRunner(_FakeManager(), "ws://127.0.0.1:1")
        assert _run(runner.rg_path()) is None

    def test_fd_path_remote_none(self):
        runner = ExecutorProcessRunner(_FakeManager(), "ws://127.0.0.1:1")
        assert _run(runner.fd_path()) is None

    def test_spawn_session_line_split_and_stderr(self):
        chunks = [
            ("stdout", b'{"a":1}\n{"b":'),
            ("stderr", b"warn: something\n"),
            ("stdout", b"2}\n"),
        ]
        handle = _FakeHandle(chunks, exit_code=0)
        client = _FakeClient(handle)
        runner = ExecutorProcessRunner(_FakeManager(client=client), "ws://127.0.0.1:1")

        async def go():
            session = await runner.spawn(["rg", "--json"], "/remote/work")
            lines = [line async for line in session.stdout_lines()]
            code = await session.wait()
            return lines, code, await session.stderr_text()

        lines, code, stderr = _run(go())
        # argv 与 cwd（file:// 包装）按约传递
        assert client.process.started == [
            {
                "argv": ["rg", "--json"],
                "cwd": "file:///remote/work",
                "env": {},
                "signal": None,
            }
        ]
        assert lines == ['{"a":1}', '{"b":2}']  # 跨 chunk 拼行
        assert stderr == "warn: something"  # 流标签分离
        assert code == 0

    def test_spawn_passes_signal_to_start_and_session(self):
        """spawn 的 signal 透传 SDK process/start 并随会话持有（流读取用）。"""
        from nova_async import AbortController

        handle = _FakeHandle([], exit_code=0)
        client = _FakeClient(handle)
        runner = ExecutorProcessRunner(_FakeManager(client=client), "ws://127.0.0.1:1")
        ctl = AbortController()
        session = _run(runner.spawn(["rg", "x"], "/w", signal=ctl.signal))
        assert client.process.started[0]["signal"] is ctl.signal
        assert session._signal is ctl.signal

    def test_stdout_lines_passes_signal_to_stream(self):
        """会话行流把 signal 透进 SDK 流式读取（流停由 signal 的分工）。"""
        from nova_async import AbortController

        handle = _FakeHandle([("stdout", b"a\n")], exit_code=0)
        ctl = AbortController()
        session = _ExecutorSession(handle, signal=ctl.signal)
        lines = _run(_collect_lines(session))
        assert lines == ["a"]
        assert handle.stream_signals == [ctl.signal]

    def test_wait_polls_until_exit_code(self):
        """wait() 轮询到真退出码（回归：单次 read(wait_ms=1) 快照对未退出
        进程恒返 -1——修复后消费 SDK ProcessHandle.wait 的轮询循环）。

        真 SDK ``ProcessHandle.wait`` + 脚本化 read 响应序列（Event 受控
        完成序：响应即脚本，零 sleep）——前两次快照未退出（exit_code=None）
        必须继续轮询，第三次拿到退出码。
        """
        from nova_exec_server_client.process import ProcessHandle, ProcessOutput

        class _ScriptedReadClient:
            """SDK ProcessHandle.wait 的最小 client 缝（read 脚本化）。"""

            def __init__(self, responses):
                self._responses = list(responses)
                self.read_calls = []

            async def read(self, process_id, wait_ms=1000, max_bytes=None, signal=None):
                self.read_calls.append(wait_ms)
                return self._responses.pop(0)

        client = _ScriptedReadClient(
            [
                ProcessOutput(chunks=[], exited=False, exit_code=None, closed=False),
                ProcessOutput(chunks=[], exited=False, exit_code=None, closed=False),
                ProcessOutput(chunks=[], exited=True, exit_code=3, closed=False),
            ]
        )
        session = _ExecutorSession(ProcessHandle(process_id="proc-x", client=client))
        assert _run(session.wait()) == 3  # 不是旧行为的 -1
        assert client.read_calls == [1000, 1000, 1000]  # SDK 轮询粒度


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

    async def spawn(self, argv, cwd, *, signal=None):
        self.spawned.append((argv, cwd))
        return self.session


class _MemLayer:
    def __init__(self, files=None, is_dir=True):
        self.files = files or {}
        self._is_dir = is_dir

    async def metadata(self, path, signal=None):
        return FsStat(exists=True, is_dir=self._is_dir, is_file=not self._is_dir)

    async def walk(self, path, *, max_entries=50_000, signal=None):
        return WalkResult(
            entries=tuple(WalkItem(path=p, is_dir=False) for p in self.files)
        )

    async def read_bytes(self, path, signal=None):
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
