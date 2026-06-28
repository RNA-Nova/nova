"""Subagent 执行引擎。

支持 single、parallel、chain 三种模式。
"""

import asyncio
from typing import Any, Callable, List, Optional

from nova_agent import AbortSignal

from .types import SubagentCall, SubagentResult, SubagentUsage

MAX_PARALLEL_TASKS = 8
MAX_CONCURRENCY = 4
PER_TASK_OUTPUT_CAP = 50 * 1024


def _get_final_output(messages: List[Any]) -> str:
    """从子 agent 消息列表中提取最后一条 assistant 文本。"""
    for msg in reversed(messages):
        if getattr(msg, "role", None) == "assistant":
            for part in getattr(msg, "content", []):
                if getattr(part, "type", None) == "text":
                    return part.text or ""
    return ""


def _extract_usage(messages: List[Any]) -> SubagentUsage:
    """从 assistant 消息中累加用量。"""
    usage = SubagentUsage()
    for msg in messages:
        if getattr(msg, "role", None) != "assistant":
            continue
        usage.turns += 1
        msg_usage = getattr(msg, "usage", None)
        if msg_usage is None:
            continue
        usage.input_tokens += getattr(msg_usage, "input", 0) or 0
        usage.output_tokens += getattr(msg_usage, "output", 0) or 0
        usage.cache_read += getattr(msg_usage, "cacheRead", 0) or 0
        usage.cache_write += getattr(msg_usage, "cacheWrite", 0) or 0
        usage.cost += getattr(msg_usage, "cost", {}).get("total", 0) or 0
        usage.context_tokens = getattr(msg_usage, "totalTokens", 0) or 0
    return usage


def _truncate_output(output: str, cap: int = PER_TASK_OUTPUT_CAP) -> str:
    """按字节长度截断输出，保留前缀。"""
    encoded = output.encode("utf-8")
    if len(encoded) <= cap:
        return output
    truncated_bytes = encoded[:cap]
    # 避免截断在多字节字符中间
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


async def _run_single(
    call: SubagentCall,
    create_session: Callable[[str, Optional[Any]], Any],
    signal: Optional[AbortSignal] = None,
) -> SubagentResult:
    """执行单个子 agent 调用。"""
    result = SubagentResult(agent=call.agent, task=call.task)

    try:
        runtime = await create_session(call.agent, call.cwd)
        # 向子 agent 发送任务并等待完成
        await runtime.prompt(call.task)

        agent = getattr(runtime, "agent", None)
        messages = getattr(agent, "state", None)
        messages = getattr(messages, "messages", []) if messages else []

        result.output = _get_final_output(messages)
        result.usage = _extract_usage(messages)
        result.model = getattr(agent, "state", None) and getattr(
            agent.state, "model", None
        )
        result.stop_reason = _last_stop_reason(messages)
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)

    return result


def _last_stop_reason(messages: List[Any]) -> Optional[str]:
    for msg in reversed(messages):
        if getattr(msg, "role", None) == "assistant":
            return getattr(msg, "stop_reason", None)
    return None


async def _map_with_concurrency_limit(
    items: List[SubagentCall],
    concurrency: int,
    fn: Callable[[SubagentCall], Any],
) -> List[SubagentResult]:
    """限制并发执行一组子 agent 调用。"""
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
            results[idx] = await fn(items[idx])

    await asyncio.gather(*[worker() for _ in range(limit)])
    return [r for r in results if r is not None]


async def run_subagent_single(
    call: SubagentCall,
    create_session: Callable[[str, Optional[Any]], Any],
    signal: Optional[AbortSignal] = None,
) -> SubagentResult:
    """Single 模式：执行一个子 agent。"""
    return await _run_single(call, create_session, signal)


async def run_subagent_parallel(
    calls: List[SubagentCall],
    create_session: Callable[[str, Optional[Any]], Any],
    signal: Optional[AbortSignal] = None,
) -> List[SubagentResult]:
    """Parallel 模式：并发执行多个子 agent。"""
    if len(calls) > MAX_PARALLEL_TASKS:
        raise ValueError(
            f"Too many parallel tasks: {len(calls)}. Max is {MAX_PARALLEL_TASKS}."
        )

    return await _map_with_concurrency_limit(
        calls, MAX_CONCURRENCY, lambda c: _run_single(c, create_session, signal)
    )


async def run_subagent_chain(
    calls: List[SubagentCall],
    create_session: Callable[[str, Optional[Any]], Any],
    signal: Optional[AbortSignal] = None,
) -> List[SubagentResult]:
    """Chain 模式：顺序执行，支持 {previous} 占位符。"""
    results: List[SubagentResult] = []
    previous_output = ""

    for call in calls:
        task_with_context = call.task.replace("{previous}", previous_output)
        current_call = SubagentCall(
            agent=call.agent, task=task_with_context, cwd=call.cwd
        )
        result = await _run_single(current_call, create_session, signal)
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
