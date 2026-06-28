"""
Bash 执行抽象。

提供本地/远程统一的 Bash 执行接口，与 TypeScript 版的 ``executeBashWithOperations``
和 ``createLocalBashOperations`` 对齐。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol


@dataclass
class BashResult:
    """Bash 命令执行结果。"""

    output: str
    exit_code: int
    cancelled: bool = False
    truncated: bool = False
    full_output_path: Optional[str] = None


class BashOperations(Protocol):
    """Bash 执行后端协议（本地子进程、远程主机等）。"""

    async def execute(
        self,
        command: str,
        cwd: str,
        options: Dict[str, Any],
    ) -> BashResult:
        """执行命令并返回结果。"""
        ...


@dataclass
class LocalBashOperations:
    """本地子进程 Bash 执行后端。"""

    shell_path: Optional[str] = None
    max_output_length: int = 100_000

    async def execute(
        self,
        command: str,
        cwd: str,
        options: Dict[str, Any],
    ) -> BashResult:
        on_chunk: Optional[Callable[[str], None]] = options.get("on_chunk")
        signal: Any = options.get("signal")

        chunks: List[str] = []
        cancelled = False

        executable = self.shell_path or "/bin/bash"
        # 显式指定 -c 以支持自定义 shell
        cmd_list = [executable, "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_list,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            return BashResult(output=f"Failed to start shell: {exc}", exit_code=-1)

        async def read_stream(stream: asyncio.StreamReader) -> None:
            while True:
                try:
                    data = await stream.read(4096)
                except Exception:
                    break
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                chunks.append(text)
                if on_chunk is not None:
                    try:
                        on_chunk(text)
                    except Exception:
                        pass

        stdout_task = asyncio.create_task(read_stream(proc.stdout))
        stderr_task = asyncio.create_task(read_stream(proc.stderr))

        # 等待子进程结束或被取消
        wait_task = asyncio.create_task(proc.wait())
        abort_event = asyncio.Event()

        def _check_signal() -> None:
            if signal is None:
                return
            if isinstance(signal, asyncio.Event):
                if signal.is_set():
                    abort_event.set()
            elif getattr(signal, "aborted", False):
                abort_event.set()

        check_interval = 0.1
        while not wait_task.done():
            _check_signal()
            try:
                await asyncio.wait_for(abort_event.wait(), timeout=check_interval)
                break
            except asyncio.TimeoutError:
                continue

        if not wait_task.done():
            cancelled = True
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

        output = "".join(chunks)
        truncated = False
        if len(output) > self.max_output_length:
            output = output[: self.max_output_length] + "\n... [output truncated]"
            truncated = True

        return BashResult(
            output=output,
            exit_code=proc.returncode if proc.returncode is not None else -1,
            cancelled=cancelled,
            truncated=truncated,
        )


def create_local_bash_operations(
    shell_path: Optional[str] = None,
) -> LocalBashOperations:
    """创建本地 Bash 执行后端。"""
    return LocalBashOperations(shell_path=shell_path)


async def execute_bash(
    command: str,
    cwd: str,
    operations: BashOperations,
    options: Optional[Dict[str, Any]] = None,
) -> BashResult:
    """使用给定 operations 执行 Bash 命令。"""
    opts = options or {}
    return await operations.execute(command, cwd, opts)
