"""Ls tool executor —— 列出目录条目。"""

import os
from typing import Any, Dict, List, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent
from nova_harness.core.tools_common.path_utils import is_path_traversal, resolve_path


class ToolExecutor:
    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ):
        path = params.get("path") or "."
        limit = params.get("limit", 100)

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
            entries: List[str] = []
            for name in sorted(os.listdir(path)):
                full = os.path.join(path, name)
                suffix = "/" if os.path.isdir(full) else ""
                entries.append(f"{name}{suffix}")

            total = len(entries)
            displayed = entries[:limit]
            truncated = total > limit

            lines = [
                f"## 📁 目录列表\n\n**路径**: `{path}`\n**条目数**: {total}{'（已截断）' if truncated else ''}\n"
            ]
            lines.extend(displayed)

            msg = "\n".join(lines)
            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={"path": path, "total": total, "displayed": len(displayed)},
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
