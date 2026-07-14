"""Find tool executor —— 查找文件或目录。"""

from typing import Any, Dict, List, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent

from nova_coding_agent.tools_common.operations import (
    FindOperations,
    FindOptions,
    create_local_find_operations,
)
from nova_coding_agent.tools_common.path_utils import is_path_traversal, resolve_path


class ToolExecutor:
    def __init__(
        self,
        operations: Optional[FindOperations] = None,
    ):
        self.operations = operations or create_local_find_operations()

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ):
        path = params.get("path", "")
        pattern = params.get("pattern", "")
        find_type = params.get("type", "file")
        limit = params.get("limit", 1000)

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
            options = FindOptions(
                pattern=pattern,
                path=path,
                find_type=find_type,
                limit=limit,
            )
            results: List[str] = await self.operations.find(options)

            if not results:
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text", text=f"## 🔍 未找到结果\n\n路径: `{path}`"
                        )
                    ],
                    details={"path": path, "count": 0},
                )

            lines = [f"## 🔍 查找结果（共 {len(results)} 条）\n"]
            lines.extend(results)

            msg = "\n".join(lines)
            if len(results) >= limit:
                msg += f"\n\n> ⚠️ 结果超过 {limit} 条，已截断"

            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={"path": path, "count": len(results), "results": results},
            )
        except Exception as e:
            return AgentToolResult(
                content=[TextContent(type="text", text=f"## ❌ 查找失败\n\n错误: {e}")],
                details={"error": str(e), "path": path},
            )
