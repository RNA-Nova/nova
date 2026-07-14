"""Subagent 工具执行器。

对应原来的 ``extensions/subagent/extension.py``，现在作为普通 package tool
加载，不再通过 extension API 注册。
"""

import os
from typing import Any, Callable, Dict, List, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent

from nova_coding_agent.subagent.runner import (
    discover_agents,
    format_parallel_output,
    run_subagent_chain,
    run_subagent_parallel,
    run_subagent_single,
)
from nova_coding_agent.subagent.types import AgentScope, SubagentCall, SubagentResult
from nova_harness.core.config.defaults import get_agent_dir

MAX_PARALLEL_TASKS = 8


class ToolExecutor:
    """Subagent 工具执行器。"""

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update: Optional[Callable[[AgentToolResult], None]] = None,
    ) -> AgentToolResult:
        """执行 subagent 工具调用。"""
        mode = _validate_mode(params)
        calls = _build_calls(params, mode)

        cwd = params.get("cwd") or os.getcwd()
        agent_dir = str(get_agent_dir())
        scope: AgentScope = params.get("agent_scope", "user")
        if scope not in ("user", "project", "both"):
            scope = "user"

        agents = discover_agents(cwd, agent_dir, scope)
        requested_names = {c.agent for c in calls}
        for name in requested_names:
            if _find_agent(agents, name) is None:
                available = ", ".join(f'"{a.name}" ({a.source})' for a in agents) or "none"
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f'Unknown agent: "{name}". Available: {available}',
                        )
                    ],
                    details={"mode": mode, "agent_scope": scope, "results": []},
                )

        def _make_on_update() -> Optional[Callable[[SubagentResult], None]]:
            if on_update is None:
                return None

            def _update(result: SubagentResult) -> None:
                try:
                    on_update(
                        AgentToolResult(
                            content=[
                                TextContent(
                                    type="text", text=result.output or "(running...)"
                                )
                            ],
                            details={
                                "mode": mode,
                                "agent_scope": scope,
                                "results": [_result_to_dict(result)],
                            },
                        )
                    )
                except Exception:
                    pass

            return _update

        update_callback = _make_on_update()

        if mode == "single":
            result = await run_subagent_single(calls[0], agent_dir, signal, update_callback)
            text = result.output if not result.error else f"Subagent failed: {result.error}"
            details = {
                "mode": "single",
                "agent_scope": scope,
                "agent": result.agent,
                "task": result.task,
                "error": result.error,
                "error_message": result.error_message,
                "exit_code": result.exit_code,
                "usage": _usage_to_dict(result.usage),
                "model": result.model,
                "stop_reason": result.stop_reason,
            }
            return AgentToolResult(
                content=[TextContent(type="text", text=text)],
                details=details,
            )

        if mode == "parallel":
            results = await run_subagent_parallel(calls, agent_dir, signal, update_callback)
            text = format_parallel_output(results)
            details = {
                "mode": "parallel",
                "agent_scope": scope,
                "results": [_result_to_dict(r) for r in results],
            }
            return AgentToolResult(
                content=[TextContent(type="text", text=text)],
                details=details,
            )

        # chain
        results = await run_subagent_chain(calls, agent_dir, signal, update_callback)
        failed = next((r for r in results if r.error), None)
        if failed:
            text = f"Chain stopped at {failed.agent}: {failed.error}"
        else:
            text = results[-1].output if results else "(no output)"

        details = {
            "mode": "chain",
            "agent_scope": scope,
            "results": [_result_to_dict(r) for r in results],
        }
        return AgentToolResult(
            content=[TextContent(type="text", text=text)],
            details=details,
        )


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


def _find_agent(agents: List, name: str):
    """按名称查找已发现 agent。"""
    for agent in agents:
        if agent.name == name:
            return agent
    return None


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
    return {
        "agent": result.agent,
        "task": result.task,
        "output": result.output,
        "error": result.error,
        "error_message": result.error_message,
        "exit_code": result.exit_code,
        "usage": _usage_to_dict(result.usage),
        "model": result.model,
        "stop_reason": result.stop_reason,
    }
