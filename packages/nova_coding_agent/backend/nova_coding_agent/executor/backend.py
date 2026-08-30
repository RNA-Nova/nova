"""ExecutorBashOperations：经 nova-executor 执行 bash 的后端。

实现 ``BashOperations`` 协议（与 ``LocalBashOperations`` 同缝切换）：
命令经 ``process/start`` 下达到 executor（本地回环或远程），输出流式回灌
accumulator + on_chunk，abort 经 terminate 升级。

设计纪律：
- 环境变量只传 ``env_extra``（调用方显式给的）——本地 ``os.environ`` 不往
  远程灌（路径语义不同）；
- cwd 以 ``file://`` URI 传递（executor 按主机规则解释）；
- 沙箱/网络策略经 ``policy``（SpawnPolicy）下发——策略归 Nova 设置组装，
  执行归 executor，本层零理解纯透传。
"""

from __future__ import annotations

import asyncio
import codecs
from typing import Any, Callable, Dict, Optional

from nova_coding_agent.bash.engine import BashResult
from nova_coding_agent.tools_common.output_accumulator import (
    OutputAccumulator,
    OutputAccumulatorOptions,
)
from nova_coding_agent.tools_common.shell import sanitize_shell_output
from nova_coding_agent.tools_common.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES

from .manager import ExecutorClientManager


def _is_aborted(signal: Any) -> bool:
    if signal is None:
        return False
    if isinstance(signal, asyncio.Event):
        return signal.is_set()
    return bool(getattr(signal, "aborted", False))


class ExecutorBashOperations:
    """经 executor 执行的 bash 后端（本地回环或远程同形）。

    ``remote_cwd``：SSH 远程后端的执行 cwd——远程文件系统与本地无关，
    调用方传入的本地 cwd 不能用（设计定案：按会话隔离的远程工作区，
    /executor 切换时确定并记忆）。
    """

    def __init__(
        self,
        manager: ExecutorClientManager,
        url: Optional[str] = None,
        policy: Optional[Any] = None,
        remote_cwd: Optional[str] = None,
    ) -> None:
        self._manager = manager
        self._url = url
        self._policy = policy
        self._remote_cwd = remote_cwd

    async def execute(
        self,
        command: str,
        cwd: str,
        options: Dict[str, Any],
    ) -> BashResult:
        on_chunk: Optional[Callable[[str], None]] = options.get("on_chunk")
        sig: Any = options.get("signal")
        env_extra: Optional[Dict[str, str]] = options.get("env_extra")

        accumulator: OutputAccumulator = options.get("accumulator") or (
            OutputAccumulator(
                OutputAccumulatorOptions(
                    max_lines=DEFAULT_MAX_LINES,
                    max_bytes=DEFAULT_MAX_BYTES,
                    temp_file_prefix="nova-bash",
                )
            )
        )
        owns_accumulator = "accumulator" not in options

        try:
            client = await self._manager.get_client(self._url)
        except Exception as exc:
            return BashResult(
                output=f"executor 连接失败：{exc}",
                exit_code=-1,
            )

        # 环境信息与 shell：远程按 executor 上报的 shell，本地默认 bash
        shell = "bash"
        try:
            info = await client.environment_info()
            shell = info.shell.name or "bash"
        except Exception:
            pass

        # 执行 cwd：SSH 远程用 remote_cwd（远程文件系统与本地无关）；
        # 本地/本地沙箱用调用方 cwd
        effective_cwd = self._remote_cwd or cwd
        start_params: Dict[str, Any] = {
            "argv": [shell, "-c", command],
            "cwd": f"file://{effective_cwd}",
            "env": env_extra or {},
        }
        if self._policy is not None:
            start_params.update(self._policy.start_kwargs())

        try:
            handle = await client.process.start(**start_params)
        except Exception as exc:
            return BashResult(output=f"executor 进程启动失败：{exc}", exit_code=-1)

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def handle_chunk_bytes(data: bytes) -> None:
            text = sanitize_shell_output(decoder.decode(data))
            if not text:
                return
            accumulator.append(text.encode("utf-8"))
            if on_chunk is not None:
                try:
                    on_chunk(text)
                except Exception:
                    pass

        aborted = False
        try:
            aborted = await self._pump_until_exit(handle, sig, handle_chunk_bytes)
        except Exception as exc:
            return BashResult(output=f"executor 执行中断：{exc}", exit_code=-1)

        if aborted:
            try:
                await handle.terminate()
            except Exception:
                pass

        tail = sanitize_shell_output(decoder.decode(b"", final=True))
        if tail:
            accumulator.append(tail.encode("utf-8"))

        accumulator.finish()
        snapshot = accumulator.snapshot(persist_if_truncated=True)
        if owns_accumulator:
            accumulator.close_temp_file()

        return BashResult(
            output=snapshot.content,
            exit_code=(
                None
                if aborted
                else (self._last_exit_code if self._last_exit_code is not None else -1)
            ),
            cancelled=aborted,
            truncated=snapshot.truncation.truncated,
            full_output_path=snapshot.full_output_path,
        )

    async def _pump_until_exit(
        self,
        handle: Any,
        sig: Any,
        handle_chunk_bytes: Callable[[bytes], None],
    ) -> bool:
        """读泵：经 ``handle.output()`` 流式读取（SDK 内部按 afterSeq 续读，
        不重复）；abort 竞速中断。返回是否被 abort。"""
        self._last_exit_code: Optional[int] = None
        stream = handle.output()
        abort_task = (
            asyncio.create_task(sig.wait())
            if sig is not None and hasattr(sig, "wait")
            else None
        )
        try:
            while True:
                next_chunk = asyncio.ensure_future(stream.__anext__())
                wait_set: set = {next_chunk}
                if abort_task is not None:
                    wait_set.add(abort_task)
                done, _pending = await asyncio.wait(
                    wait_set, return_when=asyncio.FIRST_COMPLETED
                )
                if abort_task is not None and abort_task in done:
                    next_chunk.cancel()
                    return True
                if next_chunk in done:
                    try:
                        chunk = next_chunk.result()
                    except StopAsyncIteration:
                        # 流结束：读一次拿最终退出码
                        output = await handle.read(wait_ms=1)
                        self._last_exit_code = output.exit_code
                        return False
                    handle_chunk_bytes(chunk)
                elif _is_aborted(sig):
                    next_chunk.cancel()
                    return True
        finally:
            if abort_task is not None and not abort_task.done():
                abort_task.cancel()
