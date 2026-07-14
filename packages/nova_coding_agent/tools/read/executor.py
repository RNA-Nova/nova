"""Read tool executor —— 读取本地文件内容。"""

import os
from typing import Any, Dict, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import ImageContent, TextContent

from nova_coding_agent.tools_common.operations import (
    ReadOperations,
    create_local_read_operations,
)
from nova_coding_agent.tools_common.path_utils import is_path_traversal, resolve_path
from nova_coding_agent.tools_common.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationOptions,
    truncate_head,
    truncate_lines,
)


class ToolExecutor:
    def __init__(
        self,
        operations: Optional[ReadOperations] = None,
    ):
        self.operations = operations or create_local_read_operations()

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ):
        path = params.get("path", "")
        encoding = params.get("encoding", "utf-8")
        offset = params.get("offset")
        limit = params.get("limit")

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

        if not self.operations.exists(path):
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 文件不存在\n\n路径: `{path}`")
                ],
                details={"error": "File not found", "path": path},
            )

        if not self.operations.is_file(path):
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 不是文件\n\n路径: `{path}`")
                ],
                details={"error": "Not a file", "path": path},
            )

        try:
            # 图片文件：直接返回图片内容
            if self.operations.is_image_file(path):
                result = await self.operations.read_image(path)
                if result.error:
                    raise RuntimeError(result.error)
                mime = result.mime_type or "image/png"
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"## 🖼️ 图片读取成功\n\n路径: `{path}`\n大小: {result.size} 字节",
                        ),
                        ImageContent(
                            type="image", mime_type=mime, data=result.bytes_data
                        ),
                    ],
                    details={"path": path, "size": result.size, "mime": mime},
                )

            # 文本文件：读取并截断
            result = await self.operations.read_text(path, encoding=encoding)
            if result.error:
                raise RuntimeError(result.error)

            text = result.text or ""
            truncated = truncate_head(
                text,
                TruncationOptions(
                    max_lines=DEFAULT_MAX_LINES, max_bytes=DEFAULT_MAX_BYTES
                ),
            )
            lines = truncated.content.splitlines()
            lines = truncate_lines(lines, DEFAULT_MAX_LINES)[0]

            if offset is not None:
                start = max(0, offset - 1)
                lines = lines[start:]
            if limit is not None:
                lines = lines[:limit]

            content = "\n".join(lines)
            ext = os.path.splitext(path)[1].lstrip(".") or "text"

            line_info = ""
            if offset is not None or limit is not None:
                start_line = offset or 1
                end_line = start_line + len(lines) - 1
                line_info = f"\n**行范围**: {start_line}-{end_line}"

            msg = f"""## ✅ 文件读取成功

**路径**: `{path}`{line_info}

```{ext}
{content}
```"""
            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={"path": path, "lines": len(lines)},
            )
        except Exception as e:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text=f"## ❌ 读取失败\n\n路径: `{path}`\n错误: {e}"
                    )
                ],
                details={"error": str(e), "path": path},
            )
