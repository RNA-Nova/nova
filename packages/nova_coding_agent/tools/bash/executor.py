"""Bash tool executor —— 执行 shell 命令。

对齐 TypeScript ``core/tools/bash.ts`` 的核心行为：
- 使用 ``OutputAccumulator`` 流式收集 stdout/stderr
- 取消时 kill 整个进程组
- 截断时持久化完整输出到临时文件
- throttled on_update 流式推送
"""

import asyncio
import os
import shutil
import signal
from typing import Any, Callable, Dict, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent
from nova_harness.core.types.runtime.bash import BashSpawnContext, BashSpawnHook

from nova_coding_agent.tools_common.output_accumulator import (
    OutputAccumulator,
    OutputAccumulatorOptions,
)
from nova_coding_agent.tools_common.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
)

DEFAULT_TIMEOUT = 60
BASH_UPDATE_THROTTLE_MS = 100


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """结束整个进程组。"""
    if proc.returncode is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


class ToolExecutor:
    def __init__(self, spawn_hook: Optional[BashSpawnHook] = None) -> None:
        self._spawn_hook = spawn_hook

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ) -> AgentToolResult:
        command = params.get("command", "")
        cwd = params.get("cwd") or os.getcwd()
        timeout = params.get("timeout", DEFAULT_TIMEOUT)
        env_extra = params.get("env") or {}
        spawn_hook: Optional[BashSpawnHook] = params.get("spawn_hook", self._spawn_hook)

        if not command:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text="## ❌ 参数错误\n\n必须提供 command 参数"
                    )
                ],
                details={"error": "Missing required parameter: command"},
            )

        if not os.path.isabs(cwd):
            cwd = os.path.abspath(cwd)

        if not os.path.isdir(cwd):
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"## ❌ 工作目录不存在\n\n`{cwd}`",
                    )
                ],
                details={"error": "Working directory does not exist", "cwd": cwd},
            )

        ctx = BashSpawnContext(
            command=command,
            cwd=cwd,
            env={**os.environ, **env_extra},
        )
        if spawn_hook is not None:
            ctx = spawn_hook(ctx)

        shell = shutil.which("bash") or "/bin/sh"
        env = ctx.env
        cwd = ctx.cwd
        command = ctx.command

        if not os.path.isabs(cwd):
            cwd = os.path.abspath(cwd)

        if not os.path.isdir(cwd):
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"## ❌ 工作目录不存在\n\n`{cwd}`",
                    )
                ],
                details={"error": "Working directory does not exist", "cwd": cwd},
            )

        output = OutputAccumulator(
            OutputAccumulatorOptions(
                max_lines=DEFAULT_MAX_LINES,
                max_bytes=DEFAULT_MAX_BYTES,
                temp_file_prefix="nova-bash",
            )
        )

        update_dirty = False
        last_update_at = 0
        update_timer: Optional[asyncio.TimerHandle] = None
        finished = False

        async def emit_output_update() -> None:
            nonlocal update_dirty, last_update_at, update_timer
            if not on_update or not update_dirty or finished:
                return
            update_dirty = False
            last_update_at = _monotonic_ms()
            update_timer = None
            snapshot = output.snapshot(persist_if_truncated=True)
            maybe_coro = on_update(
                AgentToolResult(
                    content=[TextContent(type="text", text=snapshot.content or "")],
                    details={
                        "truncation": (
                            snapshot.truncation.truncated
                            and _truncation_to_dict(snapshot.truncation)
                            or None
                        ),
                        "full_output_path": snapshot.full_output_path,
                    },
                )
            )
            if asyncio.iscoroutine(maybe_coro):
                await maybe_coro

        def schedule_output_update() -> None:
            nonlocal update_dirty, update_timer
            if not on_update:
                return
            update_dirty = True
            if update_timer is not None:
                return
            delay = BASH_UPDATE_THROTTLE_MS - (_monotonic_ms() - last_update_at)
            loop = asyncio.get_event_loop()
            if delay <= 0:
                update_timer = loop.call_soon(_emit_output_update_sync)
                return
            update_timer = loop.call_later(delay / 1000.0, _emit_output_update_sync)

        def _emit_output_update_sync() -> None:
            asyncio.create_task(emit_output_update())

        def handle_data(data: bytes) -> None:
            output.append(data)
            schedule_output_update()

        try:
            proc = await asyncio.create_subprocess_exec(
                shell,
                "-c",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )

            aborted = False

            def on_abort() -> None:
                nonlocal aborted
                aborted = True
                _kill_process_group(proc)

            abort_handler = None
            if signal is not None:
                if signal.aborted:
                    on_abort()
                else:

                    def abort_handler(_sig: Any) -> None:
                        on_abort()

                    signal.add_event_listener(abort_handler)

            timeout_task: Optional[asyncio.Task] = None
            timed_out = False

            if timeout and timeout > 0:

                async def _timeout_watcher() -> None:
                    nonlocal timed_out
                    await asyncio.sleep(timeout)
                    timed_out = True
                    on_abort()

                timeout_task = asyncio.create_task(_timeout_watcher())

            async def read_stream(stream: asyncio.StreamReader) -> None:
                while True:
                    try:
                        chunk = await asyncio.wait_for(stream.read(4096), timeout=0.5)
                    except asyncio.TimeoutError:
                        if aborted or proc.returncode is not None:
                            break
                        continue
                    if not chunk:
                        break
                    handle_data(chunk)

            try:
                await asyncio.gather(
                    read_stream(proc.stdout),
                    read_stream(proc.stderr),
                )
                await proc.wait()
            finally:
                if timeout_task is not None and not timeout_task.done():
                    timeout_task.cancel()
                    try:
                        await timeout_task
                    except asyncio.CancelledError:
                        pass
                if abort_handler is not None and signal is not None:
                    signal.remove_event_listener(abort_handler)

            finished = True
            if update_timer is not None:
                update_timer.cancel()
                update_timer = None
            output.finish()
            await emit_output_update()
            snapshot = output.snapshot(persist_if_truncated=True)
            output.close_temp_file()

            if timed_out:
                text, details = _format_output(snapshot, output, "")
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"{text}\n\n命令超时（超过 {timeout} 秒）",
                        )
                    ],
                    details=details,
                )

            if aborted:
                text, details = _format_output(snapshot, output, "")
                return AgentToolResult(
                    content=[TextContent(type="text", text=f"{text}\n\n命令已取消")],
                    details=details,
                )

            text, details = _format_output(snapshot, output)
            status = "成功" if proc.returncode == 0 else "失败"
            icon = "✅" if proc.returncode == 0 else "❌"
            msg = f"## {icon} 命令执行{status}\n\n**命令**: `{command}`\n**工作目录**: `{cwd}`\n**退出码**: {proc.returncode}\n\n{text}"
            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={
                    "command": command,
                    "cwd": cwd,
                    "returncode": proc.returncode,
                    "truncated": snapshot.truncation.truncated,
                    "full_output_path": snapshot.full_output_path,
                    **(details or {}),
                },
            )
        except Exception as e:
            finished = True
            try:
                output.finish()
                output.close_temp_file()
            except Exception:
                pass
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"## ❌ 执行失败\n\n命令: `{command}`\n错误: {e}",
                    )
                ],
                details={"error": str(e), "command": command},
            )


def _monotonic_ms() -> int:
    return int(asyncio.get_event_loop().time() * 1000)


def _truncation_to_dict(truncation: Any) -> Dict[str, Any]:
    return {
        "truncated": truncation.truncated,
        "truncated_by": truncation.truncated_by,
        "total_lines": truncation.total_lines,
        "total_bytes": truncation.total_bytes,
        "output_lines": truncation.output_lines,
        "output_bytes": truncation.output_bytes,
        "last_line_partial": truncation.last_line_partial,
        "first_line_exceeds_limit": truncation.first_line_exceeds_limit,
        "max_lines": truncation.max_lines,
        "max_bytes": truncation.max_bytes,
    }


def _format_output(
    snapshot: Any,
    accumulator: OutputAccumulator,
    empty_text: str = "（无输出）",
) -> tuple[str, Optional[Dict[str, Any]]]:
    truncation = snapshot.truncation
    text = snapshot.content or empty_text
    details: Optional[Dict[str, Any]] = None
    if truncation.truncated:
        details = {
            "truncation": _truncation_to_dict(truncation),
            "full_output_path": snapshot.full_output_path,
        }
        start_line = truncation.total_lines - truncation.output_lines + 1
        end_line = truncation.total_lines
        if truncation.last_line_partial:
            last_line_size = format_size(accumulator.get_last_line_bytes())
            text += (
                f"\n\n[Showing last {format_size(truncation.output_bytes)} of line {end_line} "
                f"(line is {last_line_size}). Full output: {snapshot.full_output_path}]"
            )
        elif truncation.truncated_by == "lines":
            text += (
                f"\n\n[Showing lines {start_line}-{end_line} of {truncation.total_lines}. "
                f"Full output: {snapshot.full_output_path}]"
            )
        else:
            text += (
                f"\n\n[Showing lines {start_line}-{end_line} of {truncation.total_lines} "
                f"({format_size(DEFAULT_MAX_BYTES)} limit). "
                f"Full output: {snapshot.full_output_path}]"
            )
    return text, details
