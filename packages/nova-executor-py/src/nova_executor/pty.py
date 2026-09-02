"""PTY 管理"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from .process import ProcessHandle, ProcessManager
from .transport import WebSocketTransport


@dataclass
class PtyHandle:
    """PTY 句柄"""

    process_id: str
    client: PtyManager
    _process_handle: ProcessHandle

    async def write(self, data: bytes) -> None:
        """写入 PTY"""
        await self._process_handle.write(data)

    async def read(self) -> AsyncIterator[bytes]:
        """读取 PTY 输出"""
        async for chunk in self._process_handle.output():
            yield chunk

    async def terminate(self) -> None:
        """终止 PTY"""
        await self._process_handle.terminate()

    async def wait(self, timeout: float | None = None) -> int:
        """等待退出"""
        return await self._process_handle.wait(timeout)


class PtyManager:
    """PTY 管理器"""

    def __init__(self, transport: WebSocketTransport, process_manager: ProcessManager):
        self._transport = transport
        self._process_manager = process_manager

    async def spawn(
        self,
        argv: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        process_id: str | None = None,
    ) -> PtyHandle:
        """启动 PTY 进程"""
        process_handle = await self._process_manager.start(
            argv=argv,
            cwd=cwd,
            env=env,
            tty=True,
            process_id=process_id,
        )
        return PtyHandle(
            process_id=process_handle.process_id,
            client=self,
            _process_handle=process_handle,
        )
