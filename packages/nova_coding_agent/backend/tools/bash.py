"""Bash tool executor —— 执行 shell 命令。

建立在共享引擎（``nova_coding_agent.bash.engine.LocalBashOperations``）之上：
进程 spawn/读取/清洗/截断/进程组 kill 全部由引擎负责（与会话 bash 同一引擎）。
本层只保留 LLM 工具面：超时控制、throttled on_update 流式推送、
AgentToolResult 格式化（对齐 TypeScript ``core/tools/bash.ts``）。
"""

import asyncio
import inspect
import math
import os
from typing import Any, Callable, Dict, Optional, Tuple

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_harness.core.types.extensions.process import SpawnHook
from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)

from nova_coding_agent.bash.engine import (
    BashOperations,
    create_local_bash_operations,
)
from nova_coding_agent.tools_common.output_accumulator import (
    OutputAccumulator,
    OutputAccumulatorOptions,
)
from nova_coding_agent.tools_common.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
)

# 对齐 pi bash.ts：timeout 上限（毫秒）。超过即参数非法，显式报错反馈给 LLM。
MAX_TIMEOUT_MS = 2_147_483_647
MAX_TIMEOUT_SECONDS = MAX_TIMEOUT_MS / 1000
BASH_UPDATE_THROTTLE_MS = 100


def _resolve_timeout_seconds(timeout: Any) -> Tuple[Optional[float], Optional[str]]:
    """解析并校验 timeout 入参（对齐 pi resolveTimeoutMs）。

    返回 ``(秒, 错误信息)``，两者互斥；``timeout=None`` 表示不限时（pi 同款：
    LLM 不传则无默认超时，避免误杀长构建命令）。
    """
    if timeout is None:
        return None, None
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        return None, "Invalid timeout: must be a finite number of seconds"
    if timeout * 1000 > MAX_TIMEOUT_MS:
        return None, f"Invalid timeout: maximum is {MAX_TIMEOUT_SECONDS} seconds"
    return float(timeout), None


def _result_details(
    command: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    truncated: bool = False,
    full_output_path: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """构造 bash 结果的结构化 details（平铺数据；渲染形状归前端）。"""
    return {
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "truncated": truncated,
        "full_output_path": full_output_path,
        "duration_ms": duration_ms,
    }


class _CombinedAbortSignal:
    """合并"调用方 signal"与"超时"的中止信号（对齐引擎的 signal 协议）。"""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def fire(self) -> None:
        self._event.set()


class Tool:
    name = "bash"
    # 对齐 pi bash.ts 描述：明确截断上限与截断落盘语义，LLM 可据此用 read 续读全量输出
    description = (
        "在当前工作目录执行 bash 命令，返回 stdout 和 stderr。"
        f"输出截断为最后 {DEFAULT_MAX_LINES} 行或 {DEFAULT_MAX_BYTES // 1024}KB"
        "（先到为准）；截断时全量输出会保存到临时文件，可用 read 工具继续读取。"
        "可选提供 timeout（秒），不提供则不限时。"
    )
    prompt_snippet = "执行 bash 命令（ls、grep、find 等）"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "cwd": {"type": "string", "description": "工作目录，默认当前目录"},
            "timeout": {
                "type": "number",
                "description": "超时时间（秒，可选；不提供则不限时）",
            },
            "env": {"type": "object", "description": "额外的环境变量"},
        },
        "required": ["command"],
    }

    def __init__(
        self,
        context: ToolContext,
        spawn_hook: Optional[SpawnHook] = None,
    ) -> None:
        self._context = context
        self._spawn_hook = spawn_hook
        # 构造期读取 settings（对齐 pi 装配期注入）：shell 路径与命令前缀。
        # 工具随资源 reload 重建，settings 变更在重建后生效。
        self._command_prefix = context.settings.get_shell_command_prefix()
        # 本地后端是唯一执行面（executor 集成已从本线切除）
        self._local_operations = create_local_bash_operations(
            shell_path=context.settings.get_shell_path()
        )

    def set_spawn_hook(self, hook: Optional[SpawnHook]) -> None:
        """注入外部 spawn hook（ToolsManager 聚合的扩展 hook）。"""
        self._spawn_hook = hook

    def _resolve_operations(self) -> BashOperations:
        """执行期解析执行后端（本地引擎——executor 集成已从本线切除）。"""
        return self._local_operations

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
        ctx: ToolExecContext = NULL_TOOL_EXEC_CONTEXT,
    ) -> AgentToolResult:
        command = params.get("command", "")
        cwd = params.get("cwd") or self._context.cwd
        env_extra = params.get("env") or {}
        spawn_hook: Optional[SpawnHook] = params.get("spawn_hook", self._spawn_hook)

        if not command:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text="## ❌ 参数错误\n\n必须提供 command 参数"
                    )
                ],
                details={"error": "Missing required parameter: command"},
            )

        # timeout 校验（对齐 pi resolveTimeoutMs）：非有限值 / ≤0 / 超上限
        # 均显式以 is_error=True 报错反馈给 LLM；缺省即不限时。
        timeout, timeout_error = _resolve_timeout_seconds(params.get("timeout"))
        if timeout_error is not None:
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 参数错误\n\n{timeout_error}")
                ],
                details={"error": timeout_error},
                is_error=True,
            )

        # settings 的 shell 命令前缀（对齐 pi commandPrefix）：拼进每条命令
        if self._command_prefix:
            command = f"{self._command_prefix}\n{command}"

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

        # 引擎写入本 accumulator；本层保留所有权用于中途快照（流式更新）
        # 与最终的截断标注渲染。
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

        def handle_chunk(_text: str) -> None:
            # 引擎已把清洗后的文本写入 accumulator；本层只负责调度更新
            schedule_output_update()

        # 初始空 update（对齐 TS bash.ts）：命令还没产出时先让 UI 渲染工具卡片。
        if on_update:
            maybe_coro = on_update(AgentToolResult(content=[], details={}))
            if asyncio.iscoroutine(maybe_coro):
                await maybe_coro

        # 预 spawn 中止检查（对齐 pi）：signal 已中止则不启动进程，直接报错返回
        if signal is not None and getattr(signal, "aborted", False):
            return AgentToolResult(
                content=[TextContent(type="text", text="命令已取消")],
                details={"error": "aborted", "command": command},
                is_error=True,
            )

        # 组合 signal：调用方 abort 或超时，任一触发即中止引擎（引擎负责
        # SIGTERM→SIGKILL 升级杀整个进程组）。
        combined = _CombinedAbortSignal()
        timed_out = False
        watchers = []

        if signal is not None:

            async def _watch_caller() -> None:
                if getattr(signal, "aborted", False):
                    combined.fire()
                    return
                wait_fn = getattr(signal, "wait", None)
                if callable(wait_fn):
                    result = wait_fn()
                    if inspect.isawaitable(result):
                        await result
                combined.fire()

            watchers.append(asyncio.create_task(_watch_caller()))

        # timeout 缺省（None）即不限时（对齐 pi：无默认超时，避免误杀长构建命令）
        if timeout is not None:

            async def _watch_timeout() -> None:
                nonlocal timed_out
                await asyncio.sleep(timeout)
                timed_out = True
                combined.fire()

            watchers.append(asyncio.create_task(_watch_timeout()))

        started_at = asyncio.get_event_loop().time()
        try:
            result = await self._resolve_operations().execute(
                command,
                cwd,
                {
                    "on_chunk": handle_chunk,
                    "signal": combined,
                    "env_extra": env_extra,
                    "spawn_hook": spawn_hook,
                    "accumulator": output,
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
        finally:
            for watcher in watchers:
                watcher.cancel()
            for watcher in watchers:
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass

        elapsed_ms = int((asyncio.get_event_loop().time() - started_at) * 1000)

        finished = True
        if update_timer is not None:
            update_timer.cancel()
            update_timer = None
        output.finish()
        await emit_output_update()
        snapshot = output.snapshot(persist_if_truncated=True)
        output.close_temp_file()

        # 基础设施失败（cwd 被 spawn hook 改为不存在、shell 启动失败等）：
        # 引擎以 exit_code=-1 + 错误文本返回，直接透出其说明
        if result.exit_code == -1 and not result.cancelled and not timed_out:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"## ❌ 执行失败\n\n{result.output}",
                    )
                ],
                details={"error": result.output, "command": command},
                is_error=True,
            )

        if timed_out:
            text, details = _format_output(snapshot, output, "")
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"{text}\n\n命令超时（超过 {timeout} 秒）",
                    )
                ],
                details={
                    **(details or {}),
                    **_result_details(
                        command=command,
                        stdout=snapshot.content or "",
                        stderr="",
                        exit_code=-1,
                        duration_ms=int(timeout * 1000) if timeout else None,
                        truncated=snapshot.truncation.truncated,
                        full_output_path=snapshot.full_output_path,
                    ),
                },
                is_error=True,
            )

        if result.cancelled:
            text, details = _format_output(snapshot, output, "")
            return AgentToolResult(
                content=[TextContent(type="text", text=f"{text}\n\n命令已取消")],
                details={
                    **(details or {}),
                    **_result_details(
                        command=command,
                        stdout=snapshot.content or "",
                        stderr="",
                        exit_code=-1,
                        truncated=snapshot.truncation.truncated,
                        full_output_path=snapshot.full_output_path,
                    ),
                },
                is_error=True,
            )

        text, details = _format_output(snapshot, output)
        exit_code = result.exit_code if result.exit_code is not None else -1
        status = "成功" if exit_code == 0 else "失败"
        icon = "✅" if exit_code == 0 else "❌"
        msg = f"## {icon} 命令执行{status}\n\n**命令**: `{command}`\n**工作目录**: `{cwd}`\n**退出码**: {exit_code}\n\n{text}"
        return AgentToolResult(
            content=[TextContent(type="text", text=msg)],
            details={
                "cwd": cwd,
                **_result_details(
                    command=command,
                    stdout=snapshot.content or "",
                    stderr="",
                    exit_code=exit_code,
                    duration_ms=elapsed_ms,
                    truncated=snapshot.truncation.truncated,
                    full_output_path=snapshot.full_output_path,
                ),
                **(details or {}),
            },
            # pi 对齐：非零退出 = 结果级错误（驱动 toolResult.is_error 与错误卡片）
            is_error=exit_code != 0,
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
