"""Write tool executor —— 写入本地文件。"""

from typing import Any, Dict, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_coding_agent.tools_common.file_queue import with_file_write_lock
from nova_coding_agent.tools_common.operations import (
    WriteOperations,
    create_local_write_operations,
)
from nova_coding_agent.tools_common.path_utils import resolve_path

from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)


class Tool:
    name = "write"
    description = (
        "将内容写入本地文件。如果文件不存在则创建，存在则覆盖。自动创建缺失的父目录。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要写入的文件路径（相对或绝对路径）",
            },
            "content": {"type": "string", "description": "要写入的文件内容"},
            "encoding": {
                "type": "string",
                "description": "文件编码，默认为 utf-8",
                "default": "utf-8",
            },
        },
        "required": ["path", "content"],
    }

    def __init__(
        self,
        context: ToolContext,
        operations: Optional[WriteOperations] = None,
    ):
        self._context = context
        self.operations = operations or create_local_write_operations()

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
        ctx: ToolExecContext = NULL_TOOL_EXEC_CONTEXT,
    ):
        path = params.get("path", "")
        content = params.get("content")
        encoding = params.get("encoding", "utf-8")

        if not path:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text="## ❌ 参数错误\n\n必须提供 path 参数"
                    )
                ],
                details={"error": "Missing required parameter: path"},
                is_error=True,
            )

        if not isinstance(content, str):
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="## ❌ 参数错误\n\n必须提供 content 参数（字符串）",
                    )
                ],
                details={"error": "Missing required parameter: content"},
                is_error=True,
            )

        if signal is not None and getattr(signal, "aborted", False):
            return AgentToolResult(
                content=[TextContent(type="text", text="Operation aborted")],
                details={"error": "Operation aborted"},
                is_error=True,
            )

        path = resolve_path(path, getattr(self._context, "cwd", None))
        operations = self.operations

        def _throw_if_aborted() -> None:
            # abort 检查全部在写锁内逐步进行，锁持有到
            # 当前操作 settle 为止。不在 abort 事件回调里直接 reject——
            # 那样会在在途写操作尚未完成时就释放队列。
            if signal is not None and getattr(signal, "aborted", False):
                raise RuntimeError("Operation aborted")

        try:
            # mkdir/exists/写入整体入锁：同一路径的并发 mutation 完整串行，而不是只串行
            # 写入那一步——否则并发首写时 existed 判定与父目录创建会交错
            async with with_file_write_lock(path):
                _throw_if_aborted()
                await operations.ensure_parent_dir(path)
                existed = await operations.exists(path)
                _throw_if_aborted()
                result = await operations.write_file(path, content, encoding=encoding)
                if result.error:
                    raise RuntimeError(result.error)
                _throw_if_aborted()

            action = "覆盖" if existed else "创建"
            msg = f"""## ✅ 文件写入成功

**路径**: `{path}`
**操作**: {action}
**大小**: {result.chars} 字符
"""
            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={"path": path, "action": action.lower(), "chars": result.chars},
            )
        except Exception as e:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text=f"## ❌ 写入失败\n\n路径: `{path}`\n错误: {e}"
                    )
                ],
                details={"error": str(e), "path": path},
                is_error=True,
            )
