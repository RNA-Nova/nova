"""
Bash 执行抽象。

提供本地/远程统一的 Bash 执行接口，并支持 spawn hook 在子进程启动前
调整 command、cwd 或 env。
"""

from __future__ import annotations

import asyncio
import inspect
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from nova_harness.core.types.runtime.bash import (
    BashOperations,
    BashResult,
    BashSpawnContext,
    BashSpawnHook,
)


def _is_aborted(signal: Any) -> bool:
    """判断 signal 是否已经触发中断。"""
    if signal is None:
        return False
    if isinstance(signal, asyncio.Event):
        return signal.is_set()
    if getattr(signal, "aborted", False):
        return True
    return False


def _create_signal_wait_task(signal: Any) -> Optional[asyncio.Task]:
    """如果 signal 支持异步等待，返回一个等待它的 Task；否则返回 None 由调用方轮询。"""
    if signal is None:
        return None
    if isinstance(signal, asyncio.Event):
        return asyncio.create_task(signal.wait())
    wait_fn = getattr(signal, "wait", None)
    if wait_fn is not None and callable(wait_fn):
        # AbortSignal.wait() 是 coroutine function
        if inspect.iscoroutinefunction(wait_fn):
            return asyncio.create_task(wait_fn())
        # 某些对象可能提供返回 awaitable 的 wait 方法
        result = wait_fn()
        if inspect.isawaitable(result):
            return asyncio.create_task(result)
    return None


def _resolve_spawn_context(
    command: str,
    cwd: str,
    spawn_hook: Optional[BashSpawnHook] = None,
) -> BashSpawnContext:
    """应用 spawn hook 得到最终启动上下文。"""
    base = BashSpawnContext(
        command=command,
        cwd=cwd,
        env={**os.environ},
    )
    if spawn_hook is None:
        return base
    return spawn_hook(base)


@dataclass
class LocalBashOperations:
    """本地子进程 Bash 执行后端。"""

    shell_path: Optional[str] = None
    max_output_length: int = 100_000
    spawn_hook: Optional[BashSpawnHook] = field(default=None)

    async def execute(
        self,
        command: str,
        cwd: str,
        options: Dict[str, Any],
    ) -> BashResult:
        on_chunk: Optional[Callable[[str], None]] = options.get("on_chunk")
        signal: Any = options.get("signal")
        spawn_hook: Optional[BashSpawnHook] = options.get(
            "spawn_hook", self.spawn_hook
        )

        chunks: List[str] = []
        cancelled = False

        ctx = _resolve_spawn_context(command, cwd, spawn_hook)
        executable = self.shell_path or shutil.which("bash") or "/bin/bash"
        cmd_list = [executable, "-c", ctx.command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_list,
                cwd=ctx.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=ctx.env,
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

        # 等待子进程结束或被中断
        wait_task = asyncio.create_task(proc.wait())
        signal_task = _create_signal_wait_task(signal)

        tasks: set = {wait_task}
        if signal_task is not None:
            tasks.add(signal_task)

        aborted = False
        if tasks == {wait_task}:
            # 没有可等待的 signal，退回到轮询
            check_interval = 0.1
            while not wait_task.done():
                if _is_aborted(signal):
                    aborted = True
                    break
                try:
                    await asyncio.wait_for(
                        asyncio.Event().wait(), timeout=check_interval
                    )
                except asyncio.TimeoutError:
                    continue
        else:
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            aborted = signal_task is not None and signal_task in done

        if aborted:
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
    spawn_hook: Optional[BashSpawnHook] = None,
) -> LocalBashOperations:
    """创建本地 Bash 执行后端。"""
    return LocalBashOperations(shell_path=shell_path, spawn_hook=spawn_hook)


async def execute_bash(
    command: str,
    cwd: str,
    operations: BashOperations,
    options: Optional[Dict[str, Any]] = None,
) -> BashResult:
    """使用给定 operations 执行 Bash 命令。"""
    opts = options or {}
    return await operations.execute(command, cwd, opts)


__all__ = [
    "create_local_bash_operations",
    "execute_bash",
    "LocalBashOperations",
    "BashSpawnContext",
    "BashSpawnHook",
]
