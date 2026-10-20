"""PTY 管理"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from nova_protocol import AbortSignal

from .process import ProcessHandle, ProcessManager


@dataclass
class PtyHandle:
    """PTY 句柄（各方法的 `signal` 为取消词汇：透传到底层 RPC 的响应等待，
    触发即抛 AbortedError）"""

    process_id: str
    client: PtyManager
    _process_handle: ProcessHandle

    async def write(self, data: bytes, *, signal: AbortSignal | None = None) -> None:
        """写入 PTY"""
        await self._process_handle.write(data, signal=signal)

    async def read(self, *, signal: AbortSignal | None = None) -> AsyncIterator[bytes]:
        """读取 PTY 输出"""
        async for chunk in self._process_handle.output(signal=signal):
            yield chunk

    async def terminate(self, *, signal: AbortSignal | None = None) -> None:
        """终止 PTY"""
        await self._process_handle.terminate(signal=signal)

    async def wait(
        self, timeout: float | None = None, *, signal: AbortSignal | None = None
    ) -> int:
        """等待退出"""
        return await self._process_handle.wait(timeout, signal=signal)


class PtyManager:
    """PTY 管理器（复用进程管理器——PTY 即 tty=True 的进程）"""

    def __init__(self, process_manager: ProcessManager):
        self._process_manager = process_manager

    async def spawn(
        self,
        argv: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        process_id: str | None = None,
        *,
        signal: AbortSignal | None = None,
    ) -> PtyHandle:
        """启动 PTY 进程（`signal` 为取消词汇——透传 process/start 门控路径）"""
        process_handle = await self._process_manager.start(
            argv=argv,
            cwd=cwd,
            env=env,
            tty=True,
            process_id=process_id,
            signal=signal,
        )
        return PtyHandle(
            process_id=process_handle.process_id,
            client=self,
            _process_handle=process_handle,
        )
