"""Write tool executor —— 写入本地文件。"""

import os
from typing import Any, Dict, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent
from nova_harness.core.tools_common.file_queue import with_file_write_lock
from nova_harness.core.tools_common.path_utils import is_path_traversal, resolve_path


class ToolExecutor:
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
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            existed = os.path.exists(path)

            async with with_file_write_lock(path):
                with open(path, "w", encoding=encoding) as f:
                    f.write(content)

            action = "覆盖" if existed else "创建"
            msg = f"""## ✅ 文件写入成功

**路径**: `{path}`
**操作**: {action}
**大小**: {len(content)} 字符
"""
            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={"path": path, "action": action.lower(), "chars": len(content)},
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
