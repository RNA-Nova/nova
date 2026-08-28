"""Ls tool executor —— 列出目录条目。"""

from typing import Any, Dict, List, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_coding_agent.executor import backend_file_layer, resolve_backend_path
from nova_coding_agent.tools_common.operations import (
    LsOperations,
    LsOptions,
    create_local_ls_operations,
)
from nova_coding_agent.tools_common.truncate import (
    UNLIMITED_MAX_LINES,
    TruncationOptions,
    truncate_head,
)

from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)


class Tool:
    name = "ls"
    description = (
        "列出指定目录下的条目，按字母序排列（大小写不敏感），目录加 / 后缀，"
        "包含 dotfiles。默认上限 500 条。"
    )
    prompt_snippet = "List directory contents"
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要列出的目录路径（相对或绝对路径，默认当前目录）",
            },
            "limit": {
                "type": "integer",
                "default": 500,
                "description": "最大返回条目数",
            },
        },
        "required": [],
    }

    def __init__(
        self,
        context: ToolContext,
        operations: Optional[LsOperations] = None,
    ):
        self._context = context
        self.operations = operations or create_local_ls_operations()
        self._remote_cache = None

    def _resolve_operations(self) -> LsOperations:
        """执行期解析 operations（远程 executor 后端换远程 fs 层版）。"""
        layer = backend_file_layer(self._context)
        if layer is None:
            return self.operations
        if self._remote_cache is None or self._remote_cache[0] is not layer:
            self._remote_cache = (layer, type(self.operations)(layer))
        return self._remote_cache[1]

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
        ctx: ToolExecContext = NULL_TOOL_EXEC_CONTEXT,
    ):
        path = params.get("path") or "."
        limit = params.get("limit", 500)

        if signal is not None and getattr(signal, "aborted", False):
            return AgentToolResult(
                content=[TextContent(type="text", text="Operation aborted")],
                details={"error": "Operation aborted"},
                is_error=True,
            )

        path = resolve_backend_path(path, self._context)

        try:
            # 不存在/非目录的错误形态归 layer（双实现同语义异常类型，
            # 不再在工具侧直调 os 预检——缝外调用已清理）
            entries, truncated = await self._resolve_operations().list_dir(
                LsOptions(path=path, limit=limit)
            )
        except FileNotFoundError:
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 目录不存在\n\n路径: `{path}`")
                ],
                details={"error": "Directory not found", "path": path},
                is_error=True,
            )
        except NotADirectoryError:
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 不是目录\n\n路径: `{path}`")
                ],
                details={"error": "Not a directory", "path": path},
                is_error=True,
            )
        except Exception as e:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text=f"## ❌ 列出失败\n\n路径: `{path}`\n错误: {e}"
                    )
                ],
                details={"error": str(e), "path": path},
                is_error=True,
            )

        if not entries:
            return AgentToolResult(
                content=[TextContent(type="text", text="(empty directory)")],
                details={"path": path, "total": 0},
            )

        lines = [
            f"{entry.name}{'/' if entry.is_directory else ''}" for entry in entries
        ]
        # 输出拼接后过 truncate_head：只按 50KB 字节截断（对齐 pi 的
        # maxLines=∞；行数已由 limit 收口，叠默认行上限会提前截断）
        truncation = truncate_head(
            "\n".join(lines), TruncationOptions(max_lines=UNLIMITED_MAX_LINES)
        )
        msg = truncation.content
        notices: List[str] = []
        if truncated:
            notices.append(
                f"{limit} entries limit reached. Use limit={limit * 2} for more"
            )
        if truncation.truncated:
            notices.append("50KB limit reached")
        if notices:
            msg += f"\n\n[{'. '.join(notices)}]"

        return AgentToolResult(
            content=[TextContent(type="text", text=msg)],
            details={
                "path": path,
                "displayed": len(entries),
                "truncated": truncated,
            },
        )
