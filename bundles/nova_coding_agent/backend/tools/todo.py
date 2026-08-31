"""Todo 工具执行器（全量替换语义）。

模型每次调用发送**完整清单**原子替换（对齐 Claude Code TodoWrite 的实践：
模型不必维护 id，一次调用即一次快照）。工具本身零服务端状态——状态的
单一事实源是会话历史中最新一条 todo 工具结果的 ``details``，因此分支
/fork/树导航天然正确（每个工具结果都是该历史点的完整快照）。

渲染器（``frontend/tui/tools/todo.ts``）与 ``/todos`` 命令都通过扫描会话
条目取最新 details 派生展示，不依赖任何执行期内存状态。
"""

from typing import Any, Callable, Dict, List, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_harness.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)

VALID_STATUSES = ("pending", "in_progress", "completed")

_STATUS_LABELS = {
    "pending": "○",
    "in_progress": "◐",
    "completed": "✓",
}


class Tool:
    """Todo 清单管理（全量替换）。"""

    name = "todo"
    label = "Todo"
    description = (
        "Manage a task checklist for the current work. Send the COMPLETE list "
        "on every call — it atomically replaces the previous state. Use it to "
        "plan multi-step work, track progress, and show the user where you are. "
        "Each item: {content, status} with status pending | in_progress | "
        "completed. Keep exactly one item in_progress at a time; mark items "
        "completed as soon as they are done. Send an empty list to clear."
    )
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": (
                    "The complete todo list (replaces previous state). "
                    "Empty array clears the list."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Task description (imperative, e.g. 'Fix login bug').",
                        },
                        "status": {
                            "type": "string",
                            "enum": list(VALID_STATUSES),
                            "description": "Task status.",
                        },
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["todos"],
    }
    prompt_snippet = (
        "Use the `todo` tool to plan and track multi-step work: send the full "
        "list each call (it replaces state atomically), keep one item "
        "in_progress, and mark items completed immediately when done."
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
        """校验并回显全量清单（状态由 details 承载，无服务端内存态）。"""
        raw = params.get("todos")
        if not isinstance(raw, list):
            return _error_result(
                "Missing or invalid required parameter: todos (array expected)"
            )

        todos: List[Dict[str, str]] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                return _error_result(f"todos[{index}] must be an object")
            content = item.get("content")
            status = item.get("status")
            if not isinstance(content, str) or not content.strip():
                return _error_result(
                    f"todos[{index}].content must be a non-empty string"
                )
            if status not in VALID_STATUSES:
                return _error_result(
                    f"todos[{index}].status must be one of: {', '.join(VALID_STATUSES)}"
                )
            todos.append({"content": content.strip(), "status": status})

        completed = sum(1 for t in todos if t["status"] == "completed")
        in_progress = sum(1 for t in todos if t["status"] == "in_progress")

        if not todos:
            text = "Todo list cleared."
        else:
            lines = [f"{_STATUS_LABELS[t['status']]} {t['content']}" for t in todos]
            text = (
                f"Todo list updated: {completed}/{len(todos)} completed"
                + (f", {in_progress} in progress" if in_progress else "")
                + "\n"
                + "\n".join(lines)
            )

        return AgentToolResult(
            content=[TextContent(type="text", text=text)],
            details={"todos": todos},
        )


def _error_result(message: str) -> AgentToolResult:
    """参数校验失败的统一回执（details 带 error 供渲染器标红）。"""
    return AgentToolResult(
        content=[TextContent(type="text", text=f"Error: {message}")],
        details={"error": message},
        is_error=True,
    )
