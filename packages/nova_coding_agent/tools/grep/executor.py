"""Grep tool executor —— 搜索文件内容。"""

from typing import Any, Dict, List, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent

from nova_coding_agent.tools_common.operations import (
    GrepMatch,
    GrepOperations,
    GrepOptions,
    create_local_grep_operations,
)
from nova_coding_agent.tools_common.path_utils import is_path_traversal, resolve_path


class ToolExecutor:
    def __init__(
        self,
        operations: Optional[GrepOperations] = None,
    ):
        self.operations = operations or create_local_grep_operations()

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ):
        path = params.get("path", "")
        regex = params.get("regex", "")
        file_pattern = params.get("file_pattern")
        case_sensitive = params.get("case_sensitive", False)
        literal = params.get("literal", False)
        context_lines = params.get("context_lines", 0)
        limit = params.get("limit", 100)

        if not path or not regex:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="## ❌ 参数错误\n\n必须提供 path 和 regex 参数",
                    )
                ],
                details={"error": "Missing required parameter: path or regex"},
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
            options = GrepOptions(
                regex=regex,
                file_pattern=file_pattern,
                case_sensitive=case_sensitive,
                literal=literal,
                context_lines=context_lines,
                limit=limit,
            )
            results: List[GrepMatch] = await self.operations.grep(path, options)

            if not results:
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"## 🔍 未找到匹配\n\n路径: `{path}`\n正则: `{regex}`",
                        )
                    ],
                    details={"path": path, "regex": regex, "count": 0},
                )

            lines = [f"## 🔍 搜索结果（共 {len(results)} 条）\n"]
            for r in results:
                lines.append(f"`{r.path}:{r.line}`: {r.text}")

            msg = "\n".join(lines)
            if len(results) >= limit:
                msg += f"\n\n> ⚠️ 结果超过 {limit} 条，已截断"

            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={
                    "path": path,
                    "regex": regex,
                    "count": len(results),
                    "results": [
                        {"path": r.path, "line": r.line, "text": r.text}
                        for r in results
                    ],
                },
            )
        except Exception as e:
            return AgentToolResult(
                content=[TextContent(type="text", text=f"## ❌ 搜索失败\n\n错误: {e}")],
                details={"error": str(e), "path": path, "regex": regex},
            )
