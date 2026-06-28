"""Subagent 官方扩展。

让任意 agent 可以委托任务给其他已安装的 agent。
安装方式：把本文件复制或软链到 ``~/.nova/agent/extensions/subagent.py``。
"""

from typing import Any, Dict, List, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent

from nova_harness.core.agent_session.extensions import NovaExtensionAPI
from nova_harness.core.types.extensions import ExtensionToolDefinition

from .runner import (
    format_parallel_output,
    run_subagent_chain,
    run_subagent_parallel,
    run_subagent_single,
)
from .types import SubagentCall

MAX_PARALLEL_TASKS = 8


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


async def _execute_subagent(
    ctx: Any,
    tool_call_id: str,
    params: Dict[str, Any],
    signal: Optional[AbortSignal] = None,
) -> AgentToolResult:
    """subagent 工具执行体。"""
    mode = _validate_mode(params)
    calls = _build_calls(params, mode)

    async def create_session(name: str, cwd: Optional[str] = None) -> Any:
        """使用 NovaExtensionAPI 创建子 agent 会话。"""
        options = {"cwd": cwd} if cwd else None
        return await ctx.create_subagent_session(name, options)

    if mode == "single":
        result = await run_subagent_single(calls[0], create_session, signal)
        text = result.output if not result.error else f"Subagent failed: {result.error}"
        details = {
            "agent": result.agent,
            "task": result.task,
            "error": result.error,
            "usage": result.usage.__dict__,
            "model": result.model,
            "stop_reason": result.stop_reason,
        }
        return AgentToolResult(
            content=[TextContent(type="text", text=text)],
            details=details,
        )

    if mode == "parallel":
        results = await run_subagent_parallel(calls, create_session, signal)
        text = format_parallel_output(results)
        details = {
            "results": [
                {
                    "agent": r.agent,
                    "task": r.task,
                    "error": r.error,
                    "output": r.output,
                    "usage": r.usage.__dict__,
                }
                for r in results
            ]
        }
        return AgentToolResult(
            content=[TextContent(type="text", text=text)],
            details=details,
        )

    # chain
    results = await run_subagent_chain(calls, create_session, signal)
    failed = next((r for r in results if r.error), None)
    if failed:
        text = f"Chain stopped at {failed.agent}: {failed.error}"
    else:
        text = results[-1].output if results else "(no output)"

    details = {
        "results": [
            {
                "agent": r.agent,
                "task": r.task,
                "error": r.error,
                "output": r.output,
                "usage": r.usage.__dict__,
            }
            for r in results
        ]
    }
    return AgentToolResult(
        content=[TextContent(type="text", text=text)],
        details=details,
    )


def extension(nova: NovaExtensionAPI) -> None:
    """扩展入口：注册 subagent 工具。"""
    nova.register_tool(
        ExtensionToolDefinition(
            name="subagent",
            label="Subagent",
            description="Delegate tasks to other installed agents with isolated context.",
            parameters={
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "description": "Name of the installed agent to invoke (single mode).",
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
                        "description": "Array of {agent, task, cwd?} for chain mode; use {previous} placeholder.",
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
            },
            prompt_snippet="""
Use the `subagent` tool to delegate specialized tasks to other installed agents.
Modes:
- single: { agent, task }
- parallel: { tasks: [...] }
- chain: { chain: [...] } — previous step output is available via {previous} placeholder.
""".strip(),
            execute=_execute_subagent,
        )
    )
