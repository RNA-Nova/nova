"""Read tool executor —— 读取本地文件内容。"""

import base64
import mimetypes
import os
from typing import Any, Dict, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import ImageContent, TextContent
from nova_harness.core.tools_common.path_utils import is_path_traversal, resolve_path
from nova_harness.core.tools_common.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    truncate_bytes,
    truncate_lines,
)

SUPPORTED_IMAGE_TYPES = {"png", "jpeg", "jpg", "gif", "webp", "bmp"}


_IMAGE_MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": (b"RIFF", b"WEBP"),
    "bmp": (b"BM",),
}


def _is_image_file(path: str) -> bool:
    """通过扩展名与文件魔数判断是否为图片文件。"""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in SUPPORTED_IMAGE_TYPES:
        return True

    try:
        with open(path, "rb") as f:
            header = f.read(12)
    except Exception:
        return False

    for img_type, magics in _IMAGE_MAGIC.items():
        for magic in magics:
            if header.startswith(magic):
                return True
        # webp 魔数在偏移 8 处
        if img_type == "webp" and len(header) >= 12 and header[8:12] == b"WEBP":
            return True
    return False


class ToolExecutor:
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

        if not os.path.exists(path):
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 文件不存在\n\n路径: `{path}`")
                ],
                details={"error": "File not found", "path": path},
            )

        if not os.path.isfile(path):
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 不是文件\n\n路径: `{path}`")
                ],
                details={"error": "Not a file", "path": path},
            )

        try:
            # 图片文件：直接返回图片内容
            if _is_image_file(path):
                mime, _ = mimetypes.guess_type(path)
                if mime is None:
                    mime = "image/png"
                with open(path, "rb") as f:
                    data = f.read()
                b64 = base64.b64encode(data).decode("utf-8")
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"## 🖼️ 图片读取成功\n\n路径: `{path}`\n大小: {len(data)} 字节",
                        ),
                        ImageContent(type="image", mime_type=mime, data=b64),
                    ],
                    details={"path": path, "size": len(data), "mime": mime},
                )

            # 文本文件：读取并截断
            with open(path, "rb") as f:
                raw = f.read()

            text = raw.decode(encoding, errors="replace")
            text, _ = truncate_bytes(text, DEFAULT_MAX_BYTES, encoding)

            lines = text.splitlines()
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
        except UnicodeDecodeError as e:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text=f"## ❌ 解码失败\n\n路径: `{path}`\n错误: {e}"
                    )
                ],
                details={"error": "UnicodeDecodeError", "path": path},
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
