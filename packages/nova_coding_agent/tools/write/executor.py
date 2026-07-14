"""Write tool executor —— 写入本地文件。"""

from typing import Any, Dict, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent

from nova_coding_agent.tools_common.file_queue import with_file_write_lock
from nova_coding_agent.tools_common.operations import (
    WriteOperations,
    create_local_write_operations,
)
from nova_coding_agent.tools_common.path_utils import is_path_traversal, resolve_path


class ToolExecutor:
    def __init__(
        self,
        operations: Optional[WriteOperations] = None,
    ):
        self.operations = operations or create_local_write_operations()

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ):
        path = params.get("path", "")
        content = params.get("content", "")
        encoding = params.get("encoding", "utf-8")

        if not path:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text="## ❌ 参数错误\n\n必须提供 path 参数"
                    )
                ],
                details={"error": "Missing required parameter: path"},
            )

        if is_path_traversal(path):
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text="## ❌ 路径不安全\n\n路径包含 `..` 或目录遍历"
                    )
                ],
                details={"error": "Path traversal detected", "path": path},
            )

        path = resolve_path(path)

        try:
            self.operations.ensure_parent_dir(path)
            existed = self.operations.exists(path)

            async with with_file_write_lock(path):
                result = await self.operations.write_file(
                    path, content, encoding=encoding
                )

            if result.error:
                raise RuntimeError(result.error)

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
            )
