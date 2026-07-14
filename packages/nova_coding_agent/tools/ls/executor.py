"""Ls tool executor —— 列出目录条目。"""

import os
from typing import Any, Dict, List, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent

from nova_coding_agent.tools_common.operations import (
    LsOperations,
    LsOptions,
    create_local_ls_operations,
)
from nova_coding_agent.tools_common.path_utils import is_path_traversal, resolve_path


class ToolExecutor:
    def __init__(
        self,
        operations: Optional[LsOperations] = None,
    ):
        self.operations = operations or create_local_ls_operations()

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ):
        path = params.get("path") or "."
        limit = params.get("limit", 500)

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

        if not os.path.exists(path):
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 目录不存在\n\n路径: `{path}`")
                ],
                details={"error": "Directory not found", "path": path},
            )

        if not os.path.isdir(path):
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 不是目录\n\n路径: `{path}`")
                ],
                details={"error": "Not a directory", "path": path},
            )

        try:
            entries, truncated = await self.operations.list_dir(
                LsOptions(path=path, limit=limit)
            )

            lines = [
                f"## 📁 目录列表\n\n**路径**: `{path}`\n**条目数**: {len(entries) + (1 if truncated else 0)}{'（已截断）' if truncated else ''}\n"
            ]
            for entry in entries:
                suffix = "/" if entry.is_directory else ""
                lines.append(f"{entry.name}{suffix}")

            msg = "\n".join(lines)
            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={
                    "path": path,
                    "total": len(entries) + (1 if truncated else 0),
                    "displayed": len(entries),
                    "truncated": truncated,
                },
            )
        except Exception as e:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text=f"## ❌ 列出失败\n\n路径: `{path}`\n错误: {e}"
                    )
                ],
                details={"error": str(e), "path": path},
            )
