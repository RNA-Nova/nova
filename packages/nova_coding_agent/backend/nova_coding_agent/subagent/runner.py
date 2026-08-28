"""Subagent 执行引擎。

支持 single、parallel、chain 三种模式。每个子 agent 都通过
``nova-harness run`` CLI 在独立 Python 子进程中运行，与 pi 的 subagent
扩展保持一致：pi 调用 ``pi`` CLI，Nova 调用 ``nova-harness`` CLI。

**只有 agents，没有 subagents**：agent 解析不在本引擎——调用方（subagent
工具）消费会话注册表（``ToolExecContext.agents``）按名查表，旧工具侧三源
发现 / ``agent_scope`` / 独立 trust 判定已全部删除（发现归资源管线一条
管道，项目源安全归发现期 Project Trust 门控）。

进程管理特性：
- stdout 流式读取，实时解析 JSONL 事件
- on_update 聚合回调：始终携带全量结果列表（parallel 含 exit_code=-1
  运行中占位，chain 含已完成步骤 + 当前步骤），渲染器可见总进度
- 取消时先 SIGTERM，5 秒后未退出再 SIGKILL
- 完整记录 exit_code、stop_reason、error_message、stderr
- parallel 模式限制最大 8 任务、并发 4
- 全局并发信号量限制整个进程内同时运行的 subagent 数量
"""

import asyncio
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

from nova_ai import AbortSignal
from nova_coding_agent.subagent.types import (
    SubagentCall,
    SubagentResult,
    SubagentUsage,
)

# on_update 聚合回调：始终携带**全量结果列表**（parallel 含 exit_code=-1
# 的"运行中"占位，chain 含已完成步骤 + 当前流式步骤），渲染器据此展示
# 总进度（对齐 pi subagent 的聚合 details 流）。
OnUpdate = Callable[[List[SubagentResult]], None]

MAX_PARALLEL_TASKS = 8
MAX_CONCURRENCY = 4
MAX_GLOBAL_CONCURRENCY = 4
PER_TASK_OUTPUT_CAP = 50 * 1024
DEFAULT_TIMEOUT_SECONDS = 600
GRACEFUL_KILL_SECONDS = 5
MAX_STDERR_BYTES = 100 * 1024

_global_subagent_semaphore: Optional[asyncio.Semaphore] = None


def _get_global_concurrency_limit() -> int:
    """读取环境变量中的全局并发限制，默认 4。"""
    try:
        limit = int(
            os.environ.get("NOVA_SUBAGENT_MAX_CONCURRENCY", MAX_GLOBAL_CONCURRENCY)
        )
    except ValueError:
        limit = MAX_GLOBAL_CONCURRENCY
    return max(1, limit)


def _get_global_semaphore() -> asyncio.Semaphore:
    """返回全局 subagent 并发信号量（进程内共享）。

    由于 ``asyncio.Semaphore`` 绑定到创建时的事件循环，若检测到当前事件
    循环发生变化（常见于测试环境），则重新创建信号量。
    """
    global _global_subagent_semaphore
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if (
        _global_subagent_semaphore is None
        or current_loop is None
        or getattr(_global_subagent_semaphore, "_loop", None) is not current_loop
    ):
        _global_subagent_semaphore = asyncio.Semaphore(_get_global_concurrency_limit())
        _global_subagent_semaphore._loop = current_loop  # type: ignore[attr-defined]

    return _global_subagent_semaphore


def _nova_harness_executable() -> str:
    """Return the Python interpreter path; ``nova-harness`` is a module entry."""
    return sys.executable


def _nova_harness_module_args() -> List[str]:
    """Return the module invocation args for ``nova-harness run``."""
    return ["-m", "nova_harness.cli.main", "run"]


def _truncate_output(output: str, cap: int = PER_TASK_OUTPUT_CAP) -> str:
    """按字节长度截断输出，保留前缀。"""
    encoded = output.encode("utf-8")
    if len(encoded) <= cap:
        return output
    truncated_bytes = encoded[:cap]
    while truncated_bytes:
        try:
            truncated = truncated_bytes.decode("utf-8")
            break
        except UnicodeDecodeError:
            truncated_bytes = truncated_bytes[:-1]
    omitted = len(encoded) - len(truncated_bytes)
    return (
        f"{truncated}\n\n[Output truncated: {omitted} bytes omitted. "
        "Full output preserved in tool details.]"
    )


def _usage_from_dict(raw: Dict[str, Any]) -> SubagentUsage:
    """从 worker 返回的字典重建 SubagentUsage。"""
    return SubagentUsage(
        input_tokens=int(raw.get("input_tokens", 0)),
        output_tokens=int(raw.get("output_tokens", 0)),
        cache_read=int(raw.get("cache_read", 0)),
        cache_write=int(raw.get("cache_write", 0)),
        cost=float(raw.get("cost", 0.0)),
        context_tokens=int(raw.get("context_tokens", 0)),
        turns=int(raw.get("turns", 0)),
    )


async def _stream_lines(
    stream: asyncio.StreamReader,
) -> Any:
    """异步逐行读取子进程 stdout。"""
    while True:
        line = await stream.readline()
        if not line:
            break
        yield line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")


def _apply_event_payload(
    result: SubagentResult,
    payload: Dict[str, Any],
    on_update: Optional[OnUpdate] = None,
) -> None:
    """解析单条 JSONL 事件并更新 result；可选触发 on_update 回调（单元素列表形态）。"""
    event_type = payload.get("type")
    msg = payload.get("message") or {}
    if event_type in ("message_end", "tool_result_end"):
        # 累积消息用于计算 usage/output。
        result.details.setdefault("messages", []).append(msg)

        if event_type == "message_end" and msg.get("role") == "assistant":
            result.usage.turns += 1
            usage = msg.get("usage")
            if usage:
                # print 模式 JSON 流是 model_dump 原生形态——snake_case 键。
                result.usage.input_tokens += int(usage.get("input", 0) or 0)
                result.usage.output_tokens += int(usage.get("output", 0) or 0)
                result.usage.cache_read += int(usage.get("cache_read", 0) or 0)
                result.usage.cache_write += int(usage.get("cache_write", 0) or 0)
                result.usage.cost += float(usage.get("cost", {}).get("total", 0) or 0)
                result.usage.context_tokens = int(usage.get("total_tokens", 0) or 0)
            if not result.model:
                result.model = msg.get("model")
            if msg.get("stop_reason"):
                result.stop_reason = msg.get("stop_reason")
            if msg.get("error_message"):
                result.error_message = msg.get("error_message")

        # 尝试提取当前最新输出用于实时反馈。
        for m in reversed(result.details.get("messages", [])):
            if m.get("role") == "assistant":
                for part in m.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "text":
                        result.output = part.get("text") or ""
                        break
                break
        if on_update is not None:
            try:
                on_update([result])
            except Exception:
                # on_update 异常不应中断子 agent 执行
                pass

    elif event_type == "summary":
        result.output = payload.get("output", result.output)
        result.usage = _usage_from_dict(payload.get("usage", {}))
        result.model = payload.get("model") or result.model
        result.stop_reason = payload.get("stop_reason") or result.stop_reason


async def _cancel_tasks(*tasks: asyncio.Task) -> None:
    """取消并等待辅助任务结束。"""
    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


def _build_single_subprocess_cmd(call: SubagentCall) -> List[str]:
    """构造单次 CLI 子进程的命令行参数。"""
    cmd = [
        _nova_harness_executable(),
        *_nova_harness_module_args(),
        call.agent,
        "--task",
        call.task,
        "--json",
        "--no-session",
    ]
    if call.cwd:
        cmd.extend(["--cwd", call.cwd])
    return cmd


async def _run_single(
    call: SubagentCall,
    agent_dir: str,
    signal: Optional[AbortSignal] = None,
    on_update: Optional[OnUpdate] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> SubagentResult:
    """通过 ``nova-harness run`` CLI 执行单个子 agent 调用。

    ``on_update`` 收到的是**单元素列表**（聚合回调形态的统一——调用方
    在 parallel/chain 模式下自行包裹为全量列表）。
    """
    result = SubagentResult(agent=call.agent, task=call.task)
    cmd = _build_single_subprocess_cmd(call)
    env = os.environ.copy()
    env["NOVA_AGENT_DIR"] = agent_dir

    proc: Optional[asyncio.subprocess.Process] = None
    stderr_chunks: List[bytes] = []
    stderr_bytes = 0

    def _record_stderr() -> None:
        """把已捕获的 stderr 写入 result.details（仅非空时记录）。

        stderr 作为独立字段随 details 暴露，非零退出时不再只揉进
        error 文本。
        """
        if not stderr_chunks:
            return
        text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
        if text:
            result.details["stderr"] = text

    def _emit_update(_result: SubagentResult) -> None:
        if on_update is not None:
            try:
                on_update([_result])
            except Exception:
                pass

    def _process_event(payload: Dict[str, Any]) -> None:
        _apply_event_payload(result, payload, _emit_update)

    async def _read_stdout() -> None:
        """读取 stdout 直到 EOF。"""
        if proc is None or proc.stdout is None:
            return
        async for line in _stream_lines(proc.stdout):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            _process_event(payload)

    async def _read_stderr() -> None:
        """读取 stderr 并限制总大小。"""
        nonlocal stderr_bytes
        if proc is None or proc.stderr is None:
            return
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            if stderr_bytes < MAX_STDERR_BYTES:
                stderr_chunks.append(chunk)
                stderr_bytes += len(chunk)

    async def _wait_with_timeout() -> int:
        """等待进程结束，支持超时。"""
        if proc is None:
            return 1
        try:
            return await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return -1

    async with _get_global_semaphore():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=call.cwd or None,
            )

            stdout_task = asyncio.create_task(_read_stdout())
            stderr_task = asyncio.create_task(_read_stderr())

            if signal is None:
                return_code = await _wait_with_timeout()
            else:
                wait_task = asyncio.create_task(_wait_with_timeout())
                aborted = False

                while not wait_task.done():
                    if signal.aborted:
                        aborted = True
                        wait_task.cancel()
                        try:
                            await wait_task
                        except asyncio.CancelledError:
                            pass
                        break
                    try:
                        await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.2)
                    except asyncio.TimeoutError:
                        continue

                if aborted:
                    if proc.returncode is None:
                        proc.terminate()
                        try:
                            await asyncio.wait_for(
                                proc.wait(), timeout=GRACEFUL_KILL_SECONDS
                            )
                        except asyncio.TimeoutError:
                            proc.kill()
                            try:
                                await asyncio.wait_for(proc.wait(), timeout=5)
                            except asyncio.TimeoutError:
                                pass
                    result.exit_code = (
                        proc.returncode if proc.returncode is not None else -1
                    )
                    result.stop_reason = "aborted"
                    result.error = "Subagent aborted by signal."
                    result.error_message = "Subagent aborted by signal."
                    await _cancel_tasks(stdout_task, stderr_task)
                    _record_stderr()
                    return result

                return_code = wait_task.result()

            await asyncio.gather(stdout_task, stderr_task)
            _record_stderr()

            result.exit_code = return_code if return_code is not None else -1

            if return_code == -1:
                result.stop_reason = "timeout"
                result.error = f"Subagent timed out after {timeout} seconds."
                result.error_message = result.error
            elif return_code < 0:
                # 被信号终止（如 SIGKILL）
                result.stop_reason = "killed"
                result.error = (
                    f"Subagent process was terminated by signal {-return_code}."
                )
                result.error_message = result.error
            elif return_code != 0:
                stderr_text = result.details.get("stderr", "")
                result.stop_reason = "error"
                result.error = (
                    f"Subagent process exited with code {return_code}: {stderr_text}"
                )
                result.error_message = result.error_message or stderr_text

            # 成功但 model 返回了 errorMessage。
            if not result.error and result.error_message:
                result.error = result.error_message
                result.stop_reason = result.stop_reason or "error"

        except Exception as exc:  # noqa: BLE001
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
            _record_stderr()
            result.exit_code = -1
            result.stop_reason = "error"
            result.error = str(exc)
            result.error_message = str(exc)

    return result


async def _map_with_concurrency_limit(
    items: List[SubagentCall],
    concurrency: int,
    fn: Callable[[SubagentCall, int], Any],
) -> List[SubagentResult]:
    """限制并发执行一组子 agent 调用（``fn`` 收到 item 与其下标）。"""
    if not items:
        return []

    limit = max(1, min(concurrency, len(items)))
    results: List[Optional[SubagentResult]] = [None] * len(items)
    next_index = 0

    async def worker() -> None:
        nonlocal next_index
        while True:
            idx = next_index
            next_index += 1
            if idx >= len(items):
                return
            results[idx] = await fn(items[idx], idx)

    await asyncio.gather(*[worker() for _ in range(limit)])
    return [r for r in results if r is not None]


async def run_subagent_single(
    call: SubagentCall,
    agent_dir: str,
    signal: Optional[AbortSignal] = None,
    on_update: Optional[OnUpdate] = None,
) -> SubagentResult:
    """Single 模式：执行一个子 agent。"""
    return await _run_single(call, agent_dir, signal, on_update)


async def run_subagent_parallel(
    calls: List[SubagentCall],
    agent_dir: str,
    signal: Optional[AbortSignal] = None,
    on_update: Optional[OnUpdate] = None,
) -> List[SubagentResult]:
    """Parallel 模式：并发执行多个子 agent（聚合流式更新）。

    ``on_update`` 每次收到全量槽位列表：未完成任务以 ``exit_code=-1``
    的占位结果表示（渲染器据此显示运行中状态与 "n/m done" 总进度，
    对齐 pi subagent 的并行流式 details）。
    """
    if len(calls) > MAX_PARALLEL_TASKS:
        raise ValueError(
            f"Too many parallel tasks: {len(calls)}. Max is {MAX_PARALLEL_TASKS}."
        )

    # 槽位：先放"运行中"占位，任务推进时原地替换为最新流式/最终结果。
    slots: List[SubagentResult] = [
        SubagentResult(agent=c.agent, task=c.task, exit_code=-1) for c in calls
    ]

    def _emit() -> None:
        if on_update is not None:
            try:
                on_update(list(slots))
            except Exception:
                pass

    async def _run_at(call: SubagentCall, index: int) -> SubagentResult:
        def _per_task_update(results: List[SubagentResult]) -> None:
            if results:
                slots[index] = results[0]
                _emit()

        result = await _run_single(call, agent_dir, signal, _per_task_update)
        slots[index] = result
        _emit()
        return result

    _emit()  # 首帧：全部占位，渲染器立即显示完整任务清单
    return await _map_with_concurrency_limit(calls, MAX_CONCURRENCY, _run_at)


async def run_subagent_chain(
    calls: List[SubagentCall],
    agent_dir: str,
    signal: Optional[AbortSignal] = None,
    on_update: Optional[OnUpdate] = None,
) -> List[SubagentResult]:
    """Chain 模式：顺序执行，支持 {previous} 占位符（聚合流式更新）。

    ``on_update`` 收到 "已完成步骤 + 当前流式步骤" 的全量列表（对齐 pi
    subagent 的 chain details 形态）。
    """
    results: List[SubagentResult] = []
    previous_output = ""

    for call in calls:
        task_with_context = call.task.replace("{previous}", previous_output)
        current_call = SubagentCall(
            agent=call.agent, task=task_with_context, cwd=call.cwd
        )

        def _step_update(
            step_results: List[SubagentResult], _results: List[SubagentResult] = results
        ) -> None:
            if on_update is not None and step_results:
                try:
                    on_update([*_results, step_results[0]])
                except Exception:
                    pass

        result = await _run_single(current_call, agent_dir, signal, _step_update)
        results.append(result)

        if result.error:
            break
        previous_output = result.output

    return results


def format_parallel_output(results: List[SubagentResult]) -> str:
    """把并行结果格式化为汇总文本。"""
    success_count = sum(1 for r in results if not r.error)
    summaries = []
    for r in results:
        status = "failed" if r.error else "completed"
        output = _truncate_output(r.output or r.error or "(no output)")
        summaries.append(f"### [{r.agent}] {status}\n\n{output}")
    return (
        f"Parallel: {success_count}/{len(results)} succeeded\n\n"
        + "\n\n---\n\n".join(summaries)
    )
