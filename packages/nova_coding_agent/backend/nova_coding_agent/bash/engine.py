"""Bash 执行引擎（本地子进程后端）。

对齐 pi ``core/bash-executor.ts``：
- 输出清洗（strip ANSI + 二进制消毒 + ``\\r`` 归一）
- 滚动缓冲 + 超阈值全量输出落临时文件
- tail 截断（保留尾部，错误与最终结果在最后）
- abort 时 kill 整个进程组

超时不属于本层职责（pi 同款边界）：超时是 LLM 工具层的能力。
"""

from __future__ import annotations

import asyncio
import codecs
import inspect
import os
import signal as signal_module
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from nova_coding_agent.tools_common.output_accumulator import (
    OutputAccumulator,
    OutputAccumulatorOptions,
)
from nova_coding_agent.tools_common.shell import get_shell_config, sanitize_shell_output

from nova_harness.core.types.extensions.process import (
    SpawnContext,
    SpawnHook,
)
from nova_harness.core.utils.binaries import prepend_managed_bins_to_path
from nova_harness.core.utils.child_process import (
    kill_process_tree,
    track_detached_child_pid,
    untrack_detached_child_pid,
)


@dataclass
class BashResult:
    """Bash 命令执行结果。"""

    output: str
    # 被取消时为 None（对齐 pi ``exitCode: number | undefined``）
    exit_code: Optional[int]
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
    spawn_hook: Optional[SpawnHook] = None,
    env_extra: Optional[Dict[str, str]] = None,
) -> SpawnContext:
    """应用 spawn hook 得到最终启动上下文。"""
    base = SpawnContext(
        command=command,
        cwd=cwd,
        # env bin 前置：bash 里直接敲 rg 等能命中 Nova 托管的二进制
        env=prepend_managed_bins_to_path({**os.environ, **(env_extra or {})}),
    )
    if spawn_hook is None:
        return base
    return spawn_hook(base)


def _kill_process_group(
    proc: asyncio.subprocess.Process, sig: int = signal_module.SIGTERM
) -> None:
    """结束整个进程树（子进程以新会话启动，pid 即进程组组长）。"""
    if proc.returncode is not None:
        return
    kill_process_tree(proc.pid, sig)


# abort 升级策略：先 SIGTERM 给清理机会，宽限后仍未退出则 SIGKILL 保证必死
_ABORT_TERM_GRACE_S = 2.0
_ABORT_KILL_GRACE_S = 1.0
# 进程退出后管道的空闲宽限（对齐 pi EXIT_STDIO_GRACE_MS）：计时器随每个
# chunk 重置——积极输出的后台孙进程不会被误截，安静继承管道的孙进程
# 超时释放，避免读循环悬挂（pi#5303）
_EXIT_STDIO_GRACE_S = 0.1


async def _wait_process_exit(proc: asyncio.subprocess.Process) -> None:
    """只等进程退出本身（不等待管道关闭）。

    asyncio 的 ``proc.wait()`` 会同时等进程退出**和所有管道 EOF**
    （``_try_finish`` 要求 pipes 全部 disconnected）——后台孙进程继承
    管道时会一直挂住。``proc.returncode`` 则在子进程被收割时（SIGCHLD）
    立即赋值，与管道状态无关。
    """
    while proc.returncode is None:
        await asyncio.sleep(0.05)


@dataclass
class LocalBashOperations:
    """本地子进程 Bash 执行后端。"""

    shell_path: Optional[str] = None
    spawn_hook: Optional[SpawnHook] = None

    async def execute(
        self,
        command: str,
        cwd: str,
        options: Dict[str, Any],
    ) -> BashResult:
        on_chunk: Optional[Callable[[str], None]] = options.get("on_chunk")
        sig: Any = options.get("signal")
        spawn_hook: Optional[SpawnHook] = options.get("spawn_hook", self.spawn_hook)
        env_extra: Optional[Dict[str, str]] = options.get("env_extra")

        ctx = _resolve_spawn_context(command, cwd, spawn_hook, env_extra)
        if not os.path.isdir(ctx.cwd):
            return BashResult(
                output=(
                    f"Working directory does not exist: {ctx.cwd}\n"
                    "Cannot execute bash commands."
                ),
                exit_code=-1,
            )
        try:
            shell_config = get_shell_config(self.shell_path)
        except FileNotFoundError as exc:
            return BashResult(output=str(exc), exit_code=-1)

        # 调用方可传入自己的 accumulator（LLM 工具需要在中途自行快照做
        # throttled 流式更新）；外部传入时本层不负责关闭。
        accumulator: OutputAccumulator = options.get("accumulator") or (
            OutputAccumulator(OutputAccumulatorOptions(temp_file_prefix="nova-bash"))
        )
        owns_accumulator = "accumulator" not in options
        # 流式 UTF-8 增量解码器：先解码再清洗，accumulator 收到的都是完整字符
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        loop = asyncio.get_running_loop()
        last_chunk_at = [loop.time()]

        def handle_data(data: bytes) -> None:
            text = sanitize_shell_output(decoder.decode(data))
            last_chunk_at[0] = loop.time()
            if not text:
                return
            accumulator.append(text.encode("utf-8"))
            if on_chunk is not None:
                try:
                    on_chunk(text)
                except Exception:
                    pass

        use_stdin = shell_config.command_transport == "stdin"
        cmd_list = [shell_config.shell, *shell_config.args] + (
            [] if use_stdin else [ctx.command]
        )
        popen_kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            # 对齐 pi windowsHide：后台进程不弹控制台窗口
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_list,
                cwd=ctx.cwd,
                stdin=asyncio.subprocess.PIPE if use_stdin else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=ctx.env,
                start_new_session=True,
                **popen_kwargs,
            )
            if use_stdin:
                assert proc.stdin is not None
                proc.stdin.write(ctx.command.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
        except Exception as exc:
            return BashResult(output=f"Failed to start shell: {exc}", exit_code=-1)

        track_detached_child_pid(proc.pid)
        try:
            return await self._run_to_completion(
                proc,
                sig,
                accumulator,
                decoder,
                loop,
                last_chunk_at,
                handle_data,
                owns_accumulator,
            )
        finally:
            untrack_detached_child_pid(proc.pid)

    async def _run_to_completion(
        self,
        proc: asyncio.subprocess.Process,
        sig: Any,
        accumulator: OutputAccumulator,
        decoder: Any,
        loop: asyncio.AbstractEventLoop,
        last_chunk_at: List[float],
        handle_data: Callable[[bytes], None],
        owns_accumulator: bool = True,
    ) -> BashResult:
        """等待子进程完成/中止，收尾读循环并构造结果。"""

        async def read_stream(stream: asyncio.StreamReader) -> None:
            while True:
                try:
                    data = await stream.read(4096)
                except Exception:
                    break
                if not data:
                    break
                handle_data(data)

        stdout_task = asyncio.create_task(read_stream(proc.stdout))
        stderr_task = asyncio.create_task(read_stream(proc.stderr))

        # 等待子进程退出（进程级，不等管道）或被中断
        wait_task = asyncio.create_task(_wait_process_exit(proc))
        signal_task = _create_signal_wait_task(sig)

        aborted = False
        if signal_task is not None:
            done, pending = await asyncio.wait(
                {wait_task, signal_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            aborted = signal_task in done
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        else:
            # 没有可等待的 signal，退回到轮询
            while not wait_task.done():
                if _is_aborted(sig):
                    aborted = True
                    break
                await asyncio.sleep(0.1)

        if aborted:
            _kill_process_group(proc, signal_module.SIGTERM)
            try:
                await asyncio.wait_for(
                    _wait_process_exit(proc), timeout=_ABORT_TERM_GRACE_S
                )
            except asyncio.TimeoutError:
                # SIGTERM 被无视（如 trap），升级 SIGKILL，不留僵尸
                _kill_process_group(proc, signal_module.SIGKILL)
                try:
                    await asyncio.wait_for(
                        _wait_process_exit(proc), timeout=_ABORT_KILL_GRACE_S
                    )
                except asyncio.TimeoutError:
                    pass

        # 读循环收尾：正常情况管道 EOF 结束；若后台孙进程继承了管道
        # （shell 已退出但管道不 EOF），按"空闲宽限"兜底——宽限计时器随
        # 每个 chunk 重置，安静超过宽限即放弃读取（对齐 pi#5303）
        readers = asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        pipes_abandoned = False
        while not readers.done():
            idle = loop.time() - last_chunk_at[0]
            remaining = _EXIT_STDIO_GRACE_S - idle
            if remaining <= 0:
                readers.cancel()
                try:
                    await readers
                except asyncio.CancelledError:
                    pass
                pipes_abandoned = True
                break
            await asyncio.sleep(min(0.02, remaining))

        if pipes_abandoned:
            # 放弃的管道仍被后台孙进程持有：显式关闭 transport（对齐 pi 的
            # child.stdout.destroy()）。否则 GC 时 transport.__del__ 会在
            # 已关闭的事件循环上抛 RuntimeError。
            # 注：CPython 未提供公开的管道关闭 API，_transport.close() 是
            # 社区通行做法。
            proc._transport.close()  # type: ignore[attr-defined]

        # flush 解码器残余
        tail = sanitize_shell_output(decoder.decode(b"", final=True))
        if tail:
            accumulator.append(tail.encode("utf-8"))

        accumulator.finish()
        snapshot = accumulator.snapshot(persist_if_truncated=True)
        if owns_accumulator:
            accumulator.close_temp_file()

        exit_code: Optional[int] = None
        if not aborted:
            exit_code = proc.returncode if proc.returncode is not None else -1

        return BashResult(
            output=snapshot.content,
            exit_code=exit_code,
            cancelled=aborted,
            truncated=snapshot.truncation.truncated,
            full_output_path=snapshot.full_output_path,
        )


def create_local_bash_operations(
    shell_path: Optional[str] = None,
    spawn_hook: Optional[SpawnHook] = None,
) -> LocalBashOperations:
    """创建本地 Bash 执行后端。"""
    return LocalBashOperations(shell_path=shell_path, spawn_hook=spawn_hook)


def compose_spawn_hooks(hooks: List[SpawnHook]) -> SpawnHook:
    """把多个 spawn hook 链式组合成一个。"""

    def _combined(ctx: SpawnContext) -> SpawnContext:
        current = ctx
        for hook in hooks:
            current = hook(current)
        return current

    return _combined


__all__ = [
    "BashOperations",
    "BashResult",
    "LocalBashOperations",
    "compose_spawn_hooks",
    "create_local_bash_operations",
]
