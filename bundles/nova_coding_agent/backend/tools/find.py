"""Find tool executor —— 查找文件或目录。"""

from typing import Any, Dict, List, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_harness.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)

from nova_coding_agent.executor import (
    backend_file_layer,
    backend_process_runner,
    resolve_backend_path,
)
from nova_coding_agent.tools_common.operations import (
    FindOperations,
    FindOptions,
    create_local_find_operations,
)
from nova_coding_agent.tools_common.truncate import (
    UNLIMITED_MAX_LINES,
    TruncationOptions,
    truncate_head,
)


class Tool:
    name = "find"
    description = (
        "在指定路径下递归查找文件或目录。优先使用 fd，未安装时回退到 pathlib。"
    )
    prompt_snippet = "Find files by glob pattern (respects .gitignore)"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "搜索起始目录（相对或绝对路径）"},
            "pattern": {
                "type": "string",
                "description": "可选的文件名 glob 模式，例如 '*.py'",
            },
            "type": {
                "type": "string",
                "enum": ["file", "directory"],
                "default": "file",
                "description": "查找类型：file 或 directory",
            },
            "limit": {
                "type": "integer",
                "default": 1000,
                "description": (
                    "最大返回结果数（默认 1000）。结果数达到上限时输出会提示，"
                    "可加大 limit 或细化 pattern 继续查找"
                ),
            },
        },
        "required": ["path"],
    }

    def __init__(
        self,
        context: ToolContext,
        operations: Optional[FindOperations] = None,
    ):
        self._context = context
        self.operations = operations or create_local_find_operations()
        self._remote_cache = None

    def _resolve_operations(self) -> FindOperations:
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
                is_error=True,
            )

        path = resolve_backend_path(path, self._context)

        try:
            options = FindOptions(
                pattern=pattern,
                path=path,
                find_type=find_type,
                limit=limit,
                signal=signal,
            )
            results: List[str] = await self._resolve_operations().find(options)

            if not results:
                return AgentToolResult(
                    content=[
                        TextContent(type="text", text="No files found matching pattern")
                    ],
                    details={"path": path, "count": 0},
                )

            lines = list(results)
            # 输出拼接后过 truncate_head：只按 50KB 字节截断（对齐 pi 的
            # maxLines=∞；行数已由 limit 收口，叠默认行上限会提前截断）
            truncation = truncate_head(
                "\n".join(lines), TruncationOptions(max_lines=UNLIMITED_MAX_LINES)
            )
            msg = truncation.content
            notices: List[str] = []
            if len(results) >= limit:
                notices.append(
                    f"{limit} results limit reached. "
                    f"Use limit={limit * 2} for more, or refine pattern"
                )
            if truncation.truncated:
                notices.append("50KB limit reached")
            if notices:
                msg += f"\n\n[{'. '.join(notices)}]"

            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={"path": path, "count": len(results), "results": results},
            )
        except Exception as e:
            return AgentToolResult(
                content=[TextContent(type="text", text=f"## ❌ 查找失败\n\n错误: {e}")],
                details={"error": str(e), "path": path},
                is_error=True,
            )
