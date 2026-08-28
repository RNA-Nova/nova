"""进程管理"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .errors import ProcessError
from .protocol import (
    PROCESS_READ,
    PROCESS_SIGNAL,
    PROCESS_START,
    PROCESS_TERMINATE,
    PROCESS_WRITE,
    ProcessReadParams,
    ProcessReadResponse,
    ProcessSignalParams,
    ProcessStartParams,
    ProcessStartResponse,
    ProcessTerminateParams,
    ProcessTerminateResponse,
    ProcessWriteParams,
    ProcessWriteResponse,
)
from .transport import Transport


@dataclass
class ProcessOutput:
    """进程输出"""

    chunks: list[bytes]
    exited: bool
    exit_code: int | None
    closed: bool


@dataclass
class ProcessHandle:
    """进程句柄"""

    process_id: str
    client: ProcessManager

    async def read(
        self, wait_ms: int = 1000, max_bytes: int | None = None
    ) -> ProcessOutput:
        """读取进程输出"""
        return await self.client.read(
            self.process_id, wait_ms=wait_ms, max_bytes=max_bytes
        )

    async def write(self, data: bytes) -> None:
        """写入进程 stdin"""
        await self.client.write(self.process_id, data)

    async def terminate(self) -> None:
        """终止进程"""
        await self.client.terminate(self.process_id)

    async def signal(self, signal: str) -> None:
        """发送信号"""
        await self.client.signal(self.process_id, signal)

    async def wait(self, timeout: float | None = None) -> int:
        """等待进程退出，返回退出码"""
        start = asyncio.get_event_loop().time()
        while True:
            output = await self.read(wait_ms=1000)
            if output.exited and output.exit_code is not None:
                return output.exit_code
            if timeout and (asyncio.get_event_loop().time() - start) > timeout:
                raise ProcessError(f"process {self.process_id} wait timed out")
            await asyncio.sleep(0.1)

    async def output(self) -> AsyncIterator[bytes]:
        """流式读取进程输出"""
        after_seq = None
        while True:
            result = await self.client._transport.send_request(
                PROCESS_READ,
                ProcessReadParams(
                    processId=self.process_id,
                    afterSeq=after_seq,
                    waitMs=1000,
                ).model_dump(by_alias=True),
            )
            response = ProcessReadResponse.model_validate(result)
            for chunk in response.chunks:
                yield chunk.chunk
                after_seq = chunk.seq
            if response.exited:
                break

    async def output_with_stream(self) -> AsyncIterator[tuple[str, bytes]]:
        """流式读取进程输出（带流标签——stdout/stderr 分离消费用）"""
        after_seq = None
        while True:
            result = await self.client._transport.send_request(
                PROCESS_READ,
                ProcessReadParams(
                    processId=self.process_id,
                    afterSeq=after_seq,
                    waitMs=1000,
                ).model_dump(by_alias=True),
            )
            response = ProcessReadResponse.model_validate(result)
            for chunk in response.chunks:
                yield chunk.stream, chunk.chunk
                after_seq = chunk.seq
            if response.exited:
                break


class ProcessManager:
    """进程管理器"""

    def __init__(self, transport: Transport):
        self._transport = transport

    async def start(
        self,
        argv: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        tty: bool = False,
        process_id: str | None = None,
        pipe_stdin: bool = False,
    ) -> ProcessHandle:
        """启动进程（pipe_stdin=True 才可用 write 写 stdin，否则服务端 stdinClosed）"""
        if not argv:
            raise ProcessError("argv cannot be empty")

        params = ProcessStartParams(
            processId=process_id
            or f"proc-{id(self)}-{asyncio.get_event_loop().time()}",
            argv=argv,
            cwd=cwd,
            env=env or {},
            tty=tty,
            pipeStdin=pipe_stdin,
        )
        result = await self._transport.send_request(
            PROCESS_START, params.model_dump(by_alias=True)
        )
        response = ProcessStartResponse.model_validate(result)
        return ProcessHandle(process_id=response.process_id, client=self)

    async def read(
        self,
        process_id: str,
        wait_ms: int = 1000,
        max_bytes: int | None = None,
        after_seq: int | None = None,
    ) -> ProcessOutput:
        """读取进程输出"""
        params = ProcessReadParams(
            processId=process_id,
            afterSeq=after_seq,
            maxBytes=max_bytes,
            waitMs=wait_ms,
        )
        result = await self._transport.send_request(
            PROCESS_READ, params.model_dump(by_alias=True)
        )
        response = ProcessReadResponse.model_validate(result)
        return ProcessOutput(
            chunks=[c.chunk for c in response.chunks],
            exited=response.exited,
            exit_code=response.exit_code,
            closed=response.closed,
        )

    async def write(self, process_id: str, data: bytes) -> None:
        """写入进程 stdin"""
        params = ProcessWriteParams(
            processId=process_id,
            chunk=data,
            writeId=f"w-{id(data)}",
        )
        result = await self._transport.send_request(
            PROCESS_WRITE, params.model_dump(by_alias=True)
        )
        response = ProcessWriteResponse.model_validate(result)
        if response.status != "accepted":
            raise ProcessError(
                f"write to process {process_id} failed: {response.status}"
            )

    async def terminate(self, process_id: str) -> None:
        """终止进程"""
        params = ProcessTerminateParams(processId=process_id)
        result = await self._transport.send_request(
            PROCESS_TERMINATE, params.model_dump(by_alias=True)
        )
        response = ProcessTerminateResponse.model_validate(result)
        if response.running:
            raise ProcessError(f"failed to terminate process {process_id}")

    async def signal(self, process_id: str, signal: str) -> None:
        """发送信号"""
        params = ProcessSignalParams(processId=process_id, signal=signal)
        await self._transport.send_request(
            PROCESS_SIGNAL, params.model_dump(by_alias=True)
        )
