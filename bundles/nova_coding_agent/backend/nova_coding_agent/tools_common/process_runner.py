"""进程运行缝（grep/find 的 fd/rg 加速链统一 spawn 原语——本地实现）。

（executor 集成已从本线切除：远程 ``ExecutorProcessRunner`` 随之移除。
本地形态 = asyncio 子进程 + ``resolve_binary`` 三级解析。）

会话面（``ProcessSession``）：stdout 行流（跨 chunk 拼行、去 \\r）+
terminate + 退出码 + stderr 收集。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Optional, Protocol

from nova_coding_agent.tools_common.streams import read_lines

from nova_harness.core.utils.binaries import resolve_binary


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
        """本层可用的 fd 路径。"""
        ...

    async def spawn(self, argv: List[str], cwd: str) -> ProcessSession:
        """无壳 argv 直启进程。"""
        ...


class _LocalSession:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._stderr = b""

    async def stdout_lines(self) -> AsyncIterator[str]:
        assert self._proc.stdout is not None
        # readline 的 64KB 单行上限会炸大行（如 cat 巨型单行文件）——
        # 委托无上限的共享实现
        async for line in read_lines(self._proc.stdout):
            yield line
        if self._proc.stderr is not None:
            self._stderr = await self._proc.stderr.read()

    async def terminate(self) -> None:
        if self._proc.returncode is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass

    async def wait(self) -> int:
        return await self._proc.wait()

    async def stderr_text(self) -> str:
        return self._stderr.decode("utf-8", errors="replace").strip()


class LocalProcessRunner:
    """本机 spawn（fd/rg 经 resolve_binary 三级解析）。"""

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
