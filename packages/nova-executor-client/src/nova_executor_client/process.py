"""进程管理"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any
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
    ExecEnvPolicy,
    ManagedNetworkSandboxContext,
    ProcessSignalParams,
    ProcessStartParams,
    ProcessStartResponse,
    FileSystemSandboxContext,
    ShellSnapshotRequest,
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
        start = time.monotonic()
        while True:
            output = await self.read(wait_ms=1000)
            if output.exited and output.exit_code is not None:
                return output.exit_code
            if timeout and (time.monotonic() - start) > timeout:
                raise ProcessError(f"process {self.process_id} wait timed out")
            await asyncio.sleep(0.1)

    async def output(self) -> AsyncIterator[bytes]:
        """流式读取进程输出"""
        async for chunk in self.client.iter_output(self.process_id):
            yield chunk

    async def output_with_stream(self) -> AsyncIterator[tuple[str, bytes]]:
        """流式读取进程输出（带流标签——stdout/stderr 分离消费用）"""
        async for item in self.client.iter_output_with_stream(self.process_id):
            yield item


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
        *,
        arg0: str | None = None,
        env_policy: ExecEnvPolicy | dict[str, Any] | None = None,
        shell_snapshot: ShellSnapshotRequest | dict[str, Any] | None = None,
        sandbox: FileSystemSandboxContext | dict[str, Any] | None = None,
        enforce_managed_network: bool = False,
        managed_network: ManagedNetworkSandboxContext | dict[str, Any] | None = None,
        network_proxy: dict[str, Any] | None = None,
    ) -> ProcessHandle:
        """启动进程（pipe_stdin=True 才可用 write 写 stdin，否则服务端 stdinClosed）

        沙箱与网络策略参数（wire：process/start 的 ExecParams 可选字段）：
        - sandbox：文件系统沙箱上下文（见 protocol.FileSystemSandboxContext，
          可用其 read_only/workspace_write 便捷构造）
        - enforce_managed_network/managed_network：托管网络强制与 loopback
          代理细节（fail-closed：无细节时服务端拒绝放行）
        - env_policy/shell_snapshot/arg0：环境策略、shell 快照、argv[0] 覆盖
        """
        if not argv:
            raise ProcessError("argv cannot be empty")

        params = ProcessStartParams(
            processId=process_id or f"proc-{uuid.uuid4().hex}",
            argv=argv,
            cwd=cwd,
            env=env or {},
            tty=tty,
            pipeStdin=pipe_stdin,
            arg0=arg0,
            envPolicy=env_policy,
            shellSnapshot=shell_snapshot,
            sandbox=sandbox,
            enforceManagedNetwork=enforce_managed_network,
            managedNetwork=managed_network,
            networkProxy=network_proxy,
        )
        result = await self._transport.send_request(
            PROCESS_START, params.model_dump(by_alias=True, exclude_none=True)
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

    async def iter_output(
        self, process_id: str, wait_ms: int = 1000
    ) -> AsyncIterator[bytes]:
        """流式读取进程输出（afterSeq 逐块跟踪，exited 收尾）

        断线恢复语义：恢复期间底层调用等待重连（recovery.ManagedTransport
        的 Wait 策略），resume 成功后续拉不丢输出（服务端输出缓冲随会话
        存活，afterSeq 由客户端跟踪）；恢复失败才向上抛 ConnectionError。
        """
        after_seq = None
        while True:
            result = await self._transport.send_request(
                PROCESS_READ,
                ProcessReadParams(
                    processId=process_id,
                    afterSeq=after_seq,
                    waitMs=wait_ms,
                ).model_dump(by_alias=True),
            )
            response = ProcessReadResponse.model_validate(result)
            for chunk in response.chunks:
                yield chunk.chunk
                after_seq = chunk.seq
            if response.exited:
                break

    async def iter_output_with_stream(
        self, process_id: str, wait_ms: int = 1000
    ) -> AsyncIterator[tuple[str, bytes]]:
        """流式读取进程输出（带流标签——stdout/stderr 分离消费用）"""
        after_seq = None
        while True:
            result = await self._transport.send_request(
                PROCESS_READ,
                ProcessReadParams(
                    processId=process_id,
                    afterSeq=after_seq,
                    waitMs=wait_ms,
                ).model_dump(by_alias=True),
            )
            response = ProcessReadResponse.model_validate(result)
            for chunk in response.chunks:
                yield chunk.stream, chunk.chunk
                after_seq = chunk.seq
            if response.exited:
                break

    async def write(self, process_id: str, data: bytes) -> None:
        """写入进程 stdin"""
        params = ProcessWriteParams(
            processId=process_id,
            chunk=data,
            writeId=f"w-{uuid.uuid4().hex}",
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
        """终止进程

        语义说明（既有行为，有意保持）：terminate 是请求-确认式——服务端
        响应瞬间进程仍 running 即视为"终止未生效"抛 ProcessError。对长驻
        进程（cat / 交互 shell）终止异步生效，响应时大概率仍 running，调用
        方需容忍该错误（或先 signal 再轮询 read）；短进程与已退出进程不受
        影响。会话随连接断开时服务端兜底清理进程。
        """
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
