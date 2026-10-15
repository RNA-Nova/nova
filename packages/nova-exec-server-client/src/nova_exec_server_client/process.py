"""进程管理"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nova_protocol import (
    PROCESS_READ,
    PROCESS_SIGNAL,
    PROCESS_START,
    PROCESS_TERMINATE,
    PROCESS_WRITE,
    ExecEnvPolicy,
    ExecMetadata,
    FileSystemSandboxContext,
    ManagedNetworkSandboxContext,
    ProcessReadParams,
    ProcessReadResponse,
    ProcessSignalParams,
    ProcessStartParams,
    ProcessStartResponse,
    ProcessTerminateParams,
    ProcessTerminateResponse,
    ProcessWriteParams,
    ProcessWriteResponse,
    ShellSnapshotRequest,
)

from .errors import ProcessError
from .recovery import is_transport_class_error
from .transport import Transport

if TYPE_CHECKING:
    from .recovery import ManagedTransport


@dataclass
class ProcessOutput:
    """进程输出"""

    chunks: list[bytes]
    exited: bool
    exit_code: int | None
    closed: bool
    #: 进程级失败原因（服务端输出缓冲随会话终结等场景的回填）
    failure: str | None = None
    #: 退出是否因沙箱拒绝（exited=True 时有意义）
    sandbox_denied: bool = False


def _policy_decision_timeout(network_proxy: Any) -> float | None:
    """从 start 的 networkProxy 参数取裁决回调时限（秒；对位 Rust
    start_process 从 params.network_proxy.policy_decision_timeout_ms
    逐进程取 controller.timeout）；未声明/非正数 → None（回退全局时限）"""
    if network_proxy is None:
        return None
    if isinstance(network_proxy, dict):
        raw = network_proxy.get(
            "policyDecisionTimeoutMs", network_proxy.get("policy_decision_timeout_ms")
        )
    else:
        raw = getattr(network_proxy, "policy_decision_timeout_ms", None)
    if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw <= 0:
        return None
    return raw / 1000.0


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
        """等待进程退出，返回退出码

        轮询粒度自适应单次 read 的阻塞时长（read 最多等 wait_ms 毫秒），
        timeout 计时含阻塞期；None（默认）无限等。"""
        start = time.monotonic()
        while True:
            output = await self.read(wait_ms=1000)
            if output.exited and output.exit_code is not None:
                return output.exit_code
            elapsed = time.monotonic() - start
            if timeout is not None and elapsed > timeout:
                raise ProcessError(f"process {self.process_id} wait timed out")

    async def output(self) -> AsyncIterator[bytes]:
        """流式读取进程输出"""
        async for chunk in self.client.iter_output(self.process_id):
            yield chunk

    async def output_with_stream(self) -> AsyncIterator[tuple[str, bytes]]:
        """流式读取进程输出（带流标签——stdout/stderr 分离消费用）"""
        async for item in self.client.iter_output_with_stream(self.process_id):
            yield item


class ProcessManager:
    """进程管理器

    `control`：控制面恢复状态源（ExecutorClient 装配时注入；对位 Rust
    `client.inner.is_failed()` 检查）——read/write 的传输关闭重试臂据此
    判终态；缺省（独立使用）无重试能力，传输错误原样上抛。
    """

    def __init__(
        self, transport: Transport, *, control: ManagedTransport | None = None
    ):
        self._transport = transport
        self._control = control
        #: 活进程登记表（对位 Rust Inner.sessions 会话表——本客户端起的
        #: 活进程）：process_id → 裁决回调时限覆盖（秒，来自 start
        #: networkProxy 的 policyDecisionTimeoutMs；None = 回退全局时限）。
        #: start 成功登记；read/iter 见 exited 或 terminate 后移除；恢复
        #: 跨重连保留（会话随连接恢复，服务端进程表随会话存活）
        self._live_processes: dict[str, float | None] = {}

    @property
    def live_processes(self) -> dict[str, float | None]:
        """活进程登记表（网络裁决的会话表查找用——读 membership/时限覆盖）"""
        return self._live_processes

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
        metadata: ExecMetadata | None = None,
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
        - metadata：可选归因元数据（threadId/toolCallId，executor 遥测归因用，
          非授权依据——v1.7 起；旧客户端省略、旧 executor 忽略）
        """
        if not argv:
            raise ProcessError("argv cannot be empty")

        params = ProcessStartParams(
            processId=process_id or f"proc-{uuid.uuid4().hex}",
            metadata=metadata,
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
        self._live_processes[response.process_id] = _policy_decision_timeout(
            network_proxy
        )
        return ProcessHandle(process_id=response.process_id, client=self)

    async def _with_transport_retry(self, send: Callable[[], Awaitable[Any]]) -> Any:
        """传输关闭重试臂（对位 Rust Session::read/write 的
        is_transport_closed_error 臂循环）：传输类错误（连接/超时/服务端
        -32000 传输关闭——判别同 recovery is_transport_class_error）且控制面
        未到 failed/disconnected 终态时重试——断线瞬间在飞请求被旧传输判死，
        Wait 策略恢复成功后重发落到同一会话；其余错误原样上抛"""
        while True:
            try:
                return await send()
            except Exception as e:
                if not is_transport_class_error(e):
                    raise
                if self._control is None or self._control.state in (
                    "failed",
                    "disconnected",
                ):
                    raise

    async def _read_raw(
        self,
        process_id: str,
        *,
        after_seq: int | None,
        max_bytes: int | None = None,
        wait_ms: int,
    ) -> ProcessReadResponse:
        """单次 process/read（含传输关闭重试臂——重试带原 afterSeq，恢复
        成功后续拉不丢输出：服务端输出缓冲随会话存活，seq 由客户端跟踪）"""
        params = ProcessReadParams(
            processId=process_id,
            afterSeq=after_seq,
            maxBytes=max_bytes,
            waitMs=wait_ms,
        )

        async def send() -> ProcessReadResponse:
            result = await self._transport.send_request(
                PROCESS_READ, params.model_dump(by_alias=True)
            )
            return ProcessReadResponse.model_validate(result)

        return await self._with_transport_retry(send)

    async def read(
        self,
        process_id: str,
        wait_ms: int = 1000,
        max_bytes: int | None = None,
        after_seq: int | None = None,
    ) -> ProcessOutput:
        """读取进程输出（断线瞬间的传输关闭带原 afterSeq 重试，恢复成功不丢输出）"""
        response = await self._read_raw(
            process_id, after_seq=after_seq, max_bytes=max_bytes, wait_ms=wait_ms
        )
        if response.exited:
            # 终态即移出活进程登记表（对位 Rust finish_process_event
            # Ok(true) → remove_session_if）
            self._live_processes.pop(process_id, None)
        return ProcessOutput(
            chunks=[c.chunk for c in response.chunks],
            exited=response.exited,
            exit_code=response.exit_code,
            closed=response.closed,
            failure=response.failure,
            sandbox_denied=response.sandbox_denied,
        )

    async def iter_output(
        self, process_id: str, wait_ms: int = 1000
    ) -> AsyncIterator[bytes]:
        """流式读取进程输出（afterSeq 逐块跟踪，exited 收尾）

        断线恢复语义：恢复期间底层调用等待重连（recovery.ManagedTransport
        的 Wait 策略）；断线瞬间在飞 read 被判死时带原 afterSeq 重试，
        resume 成功后续拉不丢输出（服务端输出缓冲随会话存活，afterSeq 由
        客户端跟踪）；恢复失败才向上抛 ConnectionError。
        """
        after_seq = None
        while True:
            response = await self._read_raw(
                process_id, after_seq=after_seq, wait_ms=wait_ms
            )
            for chunk in response.chunks:
                yield chunk.chunk
                after_seq = chunk.seq
            if response.exited:
                self._live_processes.pop(process_id, None)
                break

    async def iter_output_with_stream(
        self, process_id: str, wait_ms: int = 1000
    ) -> AsyncIterator[tuple[str, bytes]]:
        """流式读取进程输出（带流标签——stdout/stderr 分离消费用；
        断线重试语义同 iter_output）"""
        after_seq = None
        while True:
            response = await self._read_raw(
                process_id, after_seq=after_seq, wait_ms=wait_ms
            )
            for chunk in response.chunks:
                yield chunk.stream, chunk.chunk
                after_seq = chunk.seq
            if response.exited:
                self._live_processes.pop(process_id, None)
                break

    async def write(self, process_id: str, data: bytes) -> None:
        """写入进程 stdin（断线瞬间的传输关闭带同一 writeId 重试——单次
        逻辑写内 writeId 固定，服务端 writeId 去重保证重试不双写；
        对位 Rust Session::write 的 next_write_id + 重试循环）"""
        params = ProcessWriteParams(
            processId=process_id,
            chunk=data,
            writeId=f"w-{uuid.uuid4().hex}",
        )

        async def send() -> ProcessWriteResponse:
            result = await self._transport.send_request(
                PROCESS_WRITE, params.model_dump(by_alias=True)
            )
            return ProcessWriteResponse.model_validate(result)

        response = await self._with_transport_retry(send)
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
        try:
            result = await self._transport.send_request(
                PROCESS_TERMINATE, params.model_dump(by_alias=True)
            )
            response = ProcessTerminateResponse.model_validate(result)
            if response.running:
                raise ProcessError(f"failed to terminate process {process_id}")
        finally:
            # 终止已请求即移出活进程登记表（对位 Rust terminate 后
            # cancel_network_policy_decisions——后续裁决请求 fail-closed）
            self._live_processes.pop(process_id, None)

    async def signal(self, process_id: str, signal: str) -> None:
        """发送信号"""
        params = ProcessSignalParams(processId=process_id, signal=signal)
        await self._transport.send_request(
            PROCESS_SIGNAL, params.model_dump(by_alias=True)
        )
