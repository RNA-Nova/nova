"""Grep tool executor —— 搜索文件内容（对齐 pi ``core/tools/grep.ts``）。

- rg 优先，纯 Python 兜底；
- ``--hidden`` 包含隐藏文件；context 按行号自渲染（长行截断 500 字符）；
- 匹配数达整体上限即停止；总输出 50KB 截断。
"""

from typing import Any, Dict, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_coding_agent.executor import (
    backend_file_layer,
    backend_process_runner,
    resolve_backend_path,
)
from nova_coding_agent.tools_common.operations import (
    GrepOperations,
    GrepOptions,
    create_local_grep_operations,
)

from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)

DEFAULT_LIMIT = 100


class Tool:
    name = "grep"
    description = (
        "Search file contents for a pattern. Returns matching lines with file "
        "paths and line numbers. Respects .gitignore. Output is truncated to "
        f"{DEFAULT_LIMIT} matches or 50KB (whichever is hit first). Long lines "
        "are truncated to 500 chars."
    )
    prompt_snippet = "Search file contents for patterns (respects .gitignore)"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Search pattern (regex or literal string)",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search (default: current directory)",
            },
            "glob": {
                "type": "string",
                "description": "Filter files by glob pattern, e.g. '*.ts' or '**/*.spec.ts'",
            },
            "ignoreCase": {
                "type": "boolean",
                "description": "Case-insensitive search (default: false)",
            },
            "literal": {
                "type": "boolean",
                "description": "Treat pattern as literal string instead of regex (default: false)",
            },
            "context": {
                "type": "integer",
                "description": "Number of lines to show before and after each match (default: 0)",
            },
            "limit": {
                "type": "integer",
                "description": f"Maximum number of matches to return (default: {DEFAULT_LIMIT})",
            },
        },
        "required": ["pattern"],
    }

    def __init__(
        self,
        context: ToolContext,
        operations: Optional[GrepOperations] = None,
    ):
        self._context = context
        self.operations = operations or create_local_grep_operations()
        self._remote_cache = None

    def _resolve_operations(self) -> GrepOperations:
        """执行期解析 operations（远程 executor 后端换远程 fs 层 +
        远程 ProcessRunner 版）。"""
        layer = backend_file_layer(self._context)
        if layer is None:
            return self.operations
        if self._remote_cache is None or self._remote_cache[0] is not layer:
            runner = backend_process_runner(self._context)
            self._remote_cache = (layer, type(self.operations)(layer, runner))
        return self._remote_cache[1]

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
        ctx: ToolExecContext = NULL_TOOL_EXEC_CONTEXT,
    ):
        pattern = params.get("pattern", "")
        if not pattern:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="## ❌ 参数错误\n\n必须提供 pattern 参数",
                    )
                ],
                details={"error": "Missing required parameter: pattern"},
                is_error=True,
            )

        path = resolve_backend_path(params.get("path") or ".", self._context)

        try:
            result = await self._resolve_operations().grep(
                path,
                GrepOptions(
                    pattern=pattern,
                    glob=params.get("glob"),
                    ignore_case=bool(params.get("ignoreCase", False)),
                    literal=bool(params.get("literal", False)),
                    context=int(params.get("context") or 0),
                    limit=int(params.get("limit") or DEFAULT_LIMIT),
                    signal=signal,
                ),
            )
        except RuntimeError as e:
            return AgentToolResult(
                content=[TextContent(type="text", text=f"## ❌ 搜索失败\n\n错误: {e}")],
                details={"error": str(e), "path": path},
                is_error=True,
            )
        except Exception as e:
            return AgentToolResult(
                content=[TextContent(type="text", text=f"## ❌ 搜索失败\n\n错误: {e}")],
                details={"error": str(e), "path": path},
                is_error=True,
            )

        if result.no_matches:
            return AgentToolResult(
                content=[TextContent(type="text", text="No matches found")],
                details={"path": path, "pattern": pattern, "count": 0},
            )

        details: Dict[str, Any] = {
            "path": path,
            "pattern": pattern,
            "count": result.match_count,
        }
        if result.match_limit_reached:
            details["matchLimitReached"] = True
        if result.truncated:
            details["truncated"] = True
        if result.lines_truncated:
            details["linesTruncated"] = True

        return AgentToolResult(
            content=[TextContent(type="text", text=result.content)],
            details=details,
        )
