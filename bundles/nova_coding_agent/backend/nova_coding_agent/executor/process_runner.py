"""进程运行缝（grep/find 的 fd/rg 加速链统一 spawn 原语）。

与 ``FileSystemLayer`` 同哲学：低层原语 + 双实现，上层解析零分叉——

- ``LocalProcessRunner``：asyncio 子进程 + ``resolve_binary`` 三级解析；
- ``ExecutorProcessRunner``：executor ``process/start`` **无壳 argv 直启**
  （正则不经 shell 转义——这是不走 bash 通道的原因）；rg 路径来自 SSH
  供给探测（``command -v rg``）随句柄缓存，fd 远程不解析（远程 find
  走 rg --files 或便携引擎）。

会话面（``ProcessSession``）：stdout 行流（跨 chunk 拼行、去 \\r）+
terminate + 退出码 + stderr 收集（远程经 ``output_with_stream`` 的流
标签分离，错误消息保真——坏正则类报错按原样透出）。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Optional, Protocol

from nova_harness.core.utils.binaries import resolve_binary

from nova_coding_agent.executor.provision import is_ssh_url, parse_ssh_target


class ProcessSession(Protocol):
    """一个活着的进程会话。"""

    def stdout_lines(self) -> AsyncIterator[str]:
        """stdout 行流（按 \\n 切分、去 \\r、utf-8 replace 解码）。"""
        ...

    async def terminate(self) -> None:
        """终止进程（达 limit / abort 时调用；幂等）。"""
        ...

    async def wait(self) -> int:
        """等退出码（terminate 后用来收尾判断是否为正常退出）。"""
        ...

    async def stderr_text(self) -> str:
        """收集的 stderr 文本（错误透传用；无则空串）。"""
        ...


class ProcessRunner(Protocol):
    """grep/find 加速链的 spawn 原语。"""

    async def rg_path(self) -> Optional[str]:
        """本层可用的 rg 路径；不可用（None）时上层落便携引擎。"""
        ...

    async def fd_path(self) -> Optional[str]:
        """本层可用的 fd 路径（远程 v1 恒 None）。"""
        ...

    async def spawn(self, argv: List[str], cwd: str) -> ProcessSession:
        """无壳 argv 直启进程。"""
        ...


# ---------------------------------------------------------------------------
# 本地实现
# ---------------------------------------------------------------------------


class _LocalSession:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._stderr = b""

    async def stdout_lines(self) -> AsyncIterator[str]:
        assert self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            yield line.decode("utf-8", errors="replace").rstrip("\n").removesuffix("\r")
        if self._proc.stderr is not None:
            self._stderr = await self._proc.stderr.read()

    async def terminate(self) -> None:
        if self._proc.returncode is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass

    async def wait(self) -> int:
        """等退出码（杀后停读竞态兜底——见下）。

        消费端读到 limit 中途 break → 下游管道传输停在暂停态 → kill 后
        进程退出通知的管道收尾（EOF/唤醒）与之相撞时实测（Python 3.12
        macOS，~1/10）可能永久丢失唤醒，而 returncode 由 watcher 线程
        waitpid 落地不受影响——短轮询 returncode 兜底。
        """
        for _ in range(250):  # 5s：returncode 已落地即取，不依赖唤醒
            if self._proc.returncode is not None:
                return self._proc.returncode
            await asyncio.sleep(0.02)
        if self._proc.returncode is None:
            self._proc.kill()  # 兜底补刀
        try:
            return await asyncio.wait_for(self._proc.wait(), 2)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("subprocess reap timed out after kill") from exc

    async def stderr_text(self) -> str:
        return self._stderr.decode("utf-8", errors="replace").strip()


class LocalProcessRunner:
    """本机 spawn（现状等价物：fd/rg 经 resolve_binary 三级解析）。"""

    async def rg_path(self) -> Optional[str]:
        return resolve_binary("rg")

    async def fd_path(self) -> Optional[str]:
        return resolve_binary("fd")

    async def spawn(self, argv: List[str], cwd: str) -> ProcessSession:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return _LocalSession(proc)


# ---------------------------------------------------------------------------
# 远程 executor 实现
# ---------------------------------------------------------------------------


class _ExecutorSession:
    def __init__(self, handle) -> None:
        self._handle = handle
        self._stderr_parts: List[bytes] = []

    async def stdout_lines(self) -> AsyncIterator[str]:
        buffer = b""
        async for stream, chunk in self._handle.output_with_stream():
            if stream == "stderr":
                self._stderr_parts.append(chunk)
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                yield line.decode("utf-8", errors="replace").removesuffix("\r")
        if buffer:
            yield buffer.decode("utf-8", errors="replace").removesuffix("\r")

    async def terminate(self) -> None:
        try:
            await self._handle.terminate()
        except Exception:
            pass

    async def wait(self) -> int:
        output = await self._handle.read(wait_ms=1)
        return output.exit_code if output.exit_code is not None else -1

    async def stderr_text(self) -> str:
        return b"".join(self._stderr_parts).decode("utf-8", errors="replace").strip()


class ExecutorProcessRunner:
    """远程 executor spawn（``process/start`` 无壳 argv 直启）。

    ``rg_path`` 来自 SSH 供给探测（``command -v rg``）随句柄缓存；
    非 SSH 端点（ws:// 直连）与未探测到时回 None（上层落便携引擎）。
    ``policy``（SpawnPolicy）随 spawn 透传——与 bash 引擎同一策略缝。
    """

    def __init__(self, manager, url: str, policy: Optional[Any] = None) -> None:
        self._manager = manager
        self._url = url
        self._policy = policy
        self._target = parse_ssh_target(url) if is_ssh_url(url) else None

    async def _client(self):
        return await self._manager.get_client(self._url)

    async def rg_path(self) -> Optional[str]:
        if self._target is None:
            return None
        await self._client()  # 幂等——确保已供给（探测含 rg）
        handle = self._manager.get_ssh_handle(self._target)
        return getattr(handle, "rg_path", None) or None

    async def fd_path(self) -> Optional[str]:
        return None

    async def spawn(self, argv: List[str], cwd: str) -> ProcessSession:
        client = await self._client()
        extra = self._policy.start_kwargs() if self._policy is not None else {}
        handle = await client.process.start(
            argv=argv, cwd=f"file://{cwd}", env={}, **extra
        )
        return _ExecutorSession(handle)
