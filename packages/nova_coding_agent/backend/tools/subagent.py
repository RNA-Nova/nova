"""Subagent 工具执行器。

作为普通 package tool 加载。**只有 agents，没有 subagents**——工具名保留
subagent 仅指"委派"动作：agent 解析消费**会话注册表**
（``ToolExecContext.agents``——``resource_loader.get_agents()`` 快照，
按名查表），工具侧零发现管线（旧三源发现 / ``agent_scope`` 参数 / 独立
trust 判定已全部删除——发现归资源管线一条管道，项目源安全归发现期
Project Trust 门控，执行前确认归 ``subagent_gate`` 扩展）。

details 契约（渲染器消费，键名 snake 随 wire 原样透传）：
``{mode, results: [...]}``——三模式统一携带全量 ``results`` 列表；
流式更新时 parallel 含 ``exit_code=-1`` 的运行中占位，chain 含已完成
步骤 + 当前步骤。每条 result 字段见 ``_result_to_dict``（含
agent_source / usage / messages / stderr）。
"""

from typing import Any, Callable, Dict, List, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.types.resources.agents import AgentConfig
from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)

from nova_coding_agent.subagent.runner import (
    format_parallel_output,
    run_subagent_chain,
    run_subagent_parallel,
    run_subagent_single,
)
from nova_coding_agent.subagent.types import SubagentCall, SubagentResult

MAX_PARALLEL_TASKS = 8


class Tool:
    """Subagent 工具执行器。"""

    name = "subagent"
    description = (
        "Delegate tasks to specialized agents with isolated context. "
        "Agents come from the session registry (installed packages, "
        "~/.nova/agent/agents and trusted .nova/agents). "
        "Modes: single (agent + task), parallel (tasks array), "
        "chain (sequential with {previous} placeholder)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Name of the agent to invoke (single mode).",
            },
            "task": {
                "type": "string",
                "description": "Task to delegate (single mode).",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the subagent process (single mode).",
            },
            "mode": {
                "type": "string",
                "enum": ["single", "parallel", "chain"],
                "default": "single",
                "description": "Execution mode hint.",
            },
            "tasks": {
                "type": "array",
                "description": "Array of {agent, task, cwd?} for parallel mode.",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string"},
                        "task": {"type": "string"},
                        "cwd": {"type": "string"},
                    },
                    "required": ["agent", "task"],
                },
            },
            "chain": {
                "type": "array",
                "description": (
                    "Array of {agent, task, cwd?} for chain mode; "
                    "use {previous} placeholder."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string"},
                        "task": {"type": "string"},
                        "cwd": {"type": "string"},
                    },
                    "required": ["agent", "task"],
                },
            },
        },
    }
    prompt_snippet = (
        "Use the `subagent` tool to delegate specialized tasks to other agents "
        "listed under '# Available Agents'. Modes: single { agent, task }, "
        "parallel { tasks: [...] }, chain { chain: [...] } — previous step "
        "output is available via {previous} placeholder."
    )

    def __init__(self, context: ToolContext) -> None:
        self._context = context

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update: Optional[Callable[[AgentToolResult], None]] = None,
        ctx: ToolExecContext = NULL_TOOL_EXEC_CONTEXT,
    ) -> AgentToolResult:
        """执行 subagent 工具调用。"""
        mode = _validate_mode(params)
        calls = _build_calls(params, mode)

        agent_dir = str(get_agent_dir())
        registry = ctx.agents or {}
        source_by_name = {name: _agent_source(c) for name, c in registry.items()}
        requested_names = {c.agent for c in calls}
        for name in requested_names:
            if name not in registry:
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=_unknown_agent_message(name, registry),
                        )
                    ],
                    details=self._details(mode, []),
                    is_error=True,
                )

        update_callback = self._make_on_update(mode, source_by_name, on_update)

        if mode == "single":
            result = await run_subagent_single(
                calls[0], agent_dir, signal, update_callback
            )
            result.agent_source = source_by_name.get(result.agent)
            text = (
                result.output
                if not result.error
                else f"Subagent failed: {result.error}"
            )
            return AgentToolResult(
                content=[TextContent(type="text", text=text)],
                details=self._details(mode, [result]),
                is_error=result.error is not None,
            )

        if mode == "parallel":
            results = await run_subagent_parallel(
                calls, agent_dir, signal, update_callback
            )
            for r in results:
                r.agent_source = source_by_name.get(r.agent)
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=format_parallel_output(results))
                ],
                details=self._details(mode, results),
                # 任一子任务失败即整体标记错误（对齐 single/chain 语义）
                is_error=any(r.error is not None for r in results),
            )

        # chain
        results = await run_subagent_chain(calls, agent_dir, signal, update_callback)
        for r in results:
            r.agent_source = source_by_name.get(r.agent)
        failed = next((r for r in results if r.error), None)
        if failed:
            text = f"Chain stopped at {failed.agent}: {failed.error}"
        else:
            text = results[-1].output if results else "(no output)"
        return AgentToolResult(
            content=[TextContent(type="text", text=text)],
            details=self._details(mode, results),
            is_error=failed is not None,
        )

    def _details(
        self,
        mode: str,
        results: List[SubagentResult],
    ) -> Dict[str, Any]:
        """三模式统一的 details 形态（渲染器只认 ``results`` 全量列表）。"""
        return {
            "mode": mode,
            "results": [_result_to_dict(r) for r in results],
        }

    def _make_on_update(
        self,
        mode: str,
        source_by_name: Dict[str, Optional[str]],
        on_update: Optional[Callable[[AgentToolResult], None]],
    ) -> Optional[Callable[[List[SubagentResult]], None]]:
        """把 runner 的聚合回调桥接为工具流式结果（全量 results + 进度文本）。"""
        if on_update is None:
            return None

        def _update(results: List[SubagentResult]) -> None:
            try:
                for r in results:
                    if r.agent_source is None:
                        r.agent_source = source_by_name.get(r.agent)
                on_update(
                    AgentToolResult(
                        content=[
                            TextContent(type="text", text=_progress_text(mode, results))
                        ],
                        details=self._details(mode, results),
                    )
                )
            except Exception:
                pass

        return _update


def _agent_source(config: AgentConfig) -> Optional[str]:
    """agent 来源标签（渲染器展示）：包来源给 ``package``，其余给 scope。"""
    info = config.source_info
    if info is None:
        return None
    if info.origin == "package":
        return "package"
    return info.scope or None


def _progress_text(mode: str, results: List[SubagentResult]) -> str:
    """流式更新的进度文本（内容声道——渲染器另读 details 全量列表）。"""
    if mode == "parallel":
        running = sum(1 for r in results if r.exit_code == -1)
        done = len(results) - running
        return f"Parallel: {done}/{len(results)} done, {running} running..."
    if not results:
        return "(running...)"
    return results[-1].output or "(running...)"


def _unknown_agent_message(name: str, registry: Dict[str, AgentConfig]) -> str:
    """未知 agent 的错误文本：列出注册表可用项（含来源标签）。"""
    available = (
        ", ".join(
            f'"{agent_name}" ({_agent_source(registry[agent_name]) or "unknown"})'
            for agent_name in sorted(registry)
        )
        or "none"
    )
    return f'Unknown agent: "{name}". Available: {available}.'


def _build_calls(params: Dict[str, Any], mode: str) -> List[SubagentCall]:
    """根据调用参数构建 SubagentCall 列表。"""
    if mode == "single":
        return [
            SubagentCall(
                agent=params["agent"],
                task=params["task"],
                cwd=params.get("cwd"),
            )
        ]

    if mode == "parallel":
        return [
            SubagentCall(
                agent=item["agent"],
                task=item["task"],
                cwd=item.get("cwd"),
            )
            for item in params.get("tasks", [])
        ]

    # chain
    return [
        SubagentCall(
            agent=item["agent"],
            task=item["task"],
            cwd=item.get("cwd"),
        )
        for item in params.get("chain", [])
    ]


def _validate_mode(params: Dict[str, Any]) -> str:
    """校验参数并返回确定的执行模式。"""
    has_single = bool(params.get("agent") and params.get("task"))
    has_parallel = bool(params.get("tasks"))
    has_chain = bool(params.get("chain"))

    modes = [has_single, has_parallel, has_chain]
    if sum(modes) != 1:
        raise ValueError(
            "Provide exactly one mode: single (agent+task), parallel (tasks), or chain (chain)."
        )

    if has_parallel and len(params["tasks"]) > MAX_PARALLEL_TASKS:
        raise ValueError(
            f"Too many parallel tasks: {len(params['tasks'])}. Max is {MAX_PARALLEL_TASKS}."
        )

    if has_single:
        return "single"
    if has_parallel:
        return "parallel"
    return "chain"


def _usage_to_dict(usage: Any) -> Dict[str, Any]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read": usage.cache_read,
        "cache_write": usage.cache_write,
        "cost": usage.cost,
        "context_tokens": usage.context_tokens,
        "turns": usage.turns,
    }


def _result_to_dict(result: SubagentResult) -> Dict[str, Any]:
    """把 SubagentResult 序列化为 details 字典。

    保留 runner 累积的过程数据：``messages``（子 agent 消息流）与
    ``stderr``（子进程标准错误，独立字段而非只揉进 error 文本）。
    """
    return {
        "agent": result.agent,
        "agent_source": result.agent_source,
        "task": result.task,
        "output": result.output,
        "error": result.error,
        "error_message": result.error_message,
        "exit_code": result.exit_code,
        "usage": _usage_to_dict(result.usage),
        "model": result.model,
        "stop_reason": result.stop_reason,
        "messages": result.details.get("messages", []),
        "stderr": result.details.get("stderr", ""),
    }
