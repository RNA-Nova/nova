"""Read tool executor —— 读取本地文件内容。"""

import asyncio
import base64
import os
from typing import Any, Dict, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, ImageContent, TextContent
from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)

from nova_coding_agent.tools_common.image import process_image
from nova_coding_agent.tools_common.operations import (
    ReadOperations,
    create_local_read_operations,
)
from nova_coding_agent.tools_common.path_utils import resolve_path
from nova_coding_agent.tools_common.truncate import (
    DEFAULT_MAX_BYTES,
    format_size,
    truncate_head,
)


class Tool:
    name = "read"
    description = (
        "读取本地文件内容。支持文本文件，可指定偏移行数和最大行数。"
        "自动判断文件类型（文本或图片），支持文本文件分页读取。"
    )
    prompt_snippet = "Read file contents"
    prompt_guidelines = ["Use read to examine files instead of cat or sed."]
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要读取的文件路径（相对或绝对路径）",
            },
            "encoding": {
                "type": "string",
                "description": "文本文件编码，默认为 utf-8",
                "default": "utf-8",
            },
            "offset": {
                "type": "integer",
                "description": "起始行号（仅文本文件，1-indexed，不指定则从头开始）",
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "最大行数（仅文本文件，不指定则读取所有行）",
                "minimum": 1,
            },
        },
        "required": ["path"],
    }

    def __init__(
        self,
        context: ToolContext,
        operations: Optional[ReadOperations] = None,
    ):
        self._context = context
        self.operations = operations or create_local_read_operations()

    def _non_vision_image_note(self, ctx: ToolExecContext) -> Optional[str]:
        """当前模型不支持图片输入时返回提示（对齐 pi ``getNonVisionImageNote``）。"""
        model = ctx.model
        if model is None or "image" in getattr(model, "input_types", ["image"]):
            return None
        return (
            "[Current model does not support images. "
            "The image will be omitted from this request.]"
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
        ctx: ToolExecContext = NULL_TOOL_EXEC_CONTEXT,
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
                is_error=True,
            )

        path = resolve_path(path, getattr(self._context, "cwd", None))
        operations = self.operations

        if not await operations.exists(path):
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 文件不存在\n\n路径: `{path}`")
                ],
                details={"error": "File not found", "path": path},
                is_error=True,
            )

        if not await operations.is_file(path):
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 不是文件\n\n路径: `{path}`")
                ],
                details={"error": "Not a file", "path": path},
                is_error=True,
            )

        def _throw_if_aborted() -> None:
            if signal is not None and getattr(signal, "aborted", False):
                raise RuntimeError("Operation aborted")

        try:
            _throw_if_aborted()
            # 图片文件：格式归一 + EXIF 校正 + 预算压缩后返回图片内容
            # （对齐 pi processImage：2000x2000 维度限 + base64 ≤4.5MB 预算）
            if await operations.is_image_file(path):
                result = await operations.read_image(path)
                if result.error:
                    raise RuntimeError(result.error)
                _throw_if_aborted()
                # 非视觉模型：省略图片，仅返回提示（对齐 pi read.ts）
                non_vision_note = self._non_vision_image_note(ctx)
                if non_vision_note is not None:
                    return AgentToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=f"## 🖼️ 图片读取\n\n路径: `{path}`\n\n{non_vision_note}",
                            )
                        ],
                        details={"path": path, "omitted": "non_vision_model"},
                    )
                # 图片处理是阻塞 CPU 活（解码/缩放/编码），挪出事件循环
                # （对齐 operations.py 的并发约定：纯 Python 一律 to_thread）
                processed = await asyncio.to_thread(
                    process_image,
                    result.bytes_data or b"",
                    result.mime_type or "image/png",
                    resize=self._context.settings.get_image_auto_resize(),
                )
                if not processed.ok:
                    # 对齐 pi read.ts：图片无法解码/压不进预算时给提示文本，
                    # 模型可据此继续，不标 is_error
                    note = f"## 🖼️ 图片读取\n\n路径: `{path}`\n\n{processed.message}"
                    return AgentToolResult(
                        content=[TextContent(type="text", text=note)],
                        details={"path": path, "omitted": "image_processing_failed"},
                    )
                size_note = ""
                if processed.width > 0 and processed.height > 0:
                    size_note = f"\n**尺寸**: {processed.width}x{processed.height}" + (
                        "（已缩放）" if processed.resized else ""
                    )
                # hints：格式转换提示 + 缩放坐标映射系数提示（对齐 pi hints）
                hint_text = ""
                if processed.hints:
                    hint_text = "\n\n" + "\n".join(processed.hints)
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"## 🖼️ 图片读取成功\n\n路径: `{path}`{size_note}{hint_text}",
                        ),
                        ImageContent(
                            type="image",
                            mime_type=processed.mime_type,
                            data=base64.b64encode(processed.data).decode("ascii"),
                        ),
                    ],
                    details={
                        "path": path,
                        "size": len(processed.data),
                        "mime": processed.mime_type,
                        "resized": processed.resized,
                    },
                )

            # 文本文件：读取全文，先分页后截断（对齐 TS read.ts 的顺序：
            # offset/limit 切片作用于完整内容，truncate 作用于切片结果；
            # 否则 offset 超过截断线时分页完全失效）
            result = await operations.read_text(path, encoding=encoding)
            if result.error:
                raise RuntimeError(result.error)
            _throw_if_aborted()

            text = result.text or ""
            all_lines = text.split("\n")
            total_file_lines = len(all_lines)

            # offset 转 0-indexed；越界报错（对齐 TS）
            start = max(0, (offset or 1) - 1)
            if start >= total_file_lines:
                raise RuntimeError(
                    f"Offset {offset} is beyond end of file "
                    f"({total_file_lines} lines total)"
                )

            # limit 切片（用户 limit 优先，否则截断决定）
            if limit is not None:
                end = min(start + limit, total_file_lines)
                selected = "\n".join(all_lines[start:end])
            else:
                end = total_file_lines
                selected = "\n".join(all_lines[start:])

            truncation = truncate_head(selected)

            start_display = start + 1
            notices: list[str] = []
            if truncation.first_line_exceeds_limit:
                # 首行即超字节预算：提示带该行实际大小（对齐 pi 文案）
                first_line_size = format_size(len(all_lines[start].encode("utf-8")))
                content = (
                    f"[Line {start_display} is {first_line_size}, exceeds "
                    f"{format_size(DEFAULT_MAX_BYTES)} limit. "
                    f"Use bash: sed -n '{start_display}p' {path} "
                    f"| head -c {DEFAULT_MAX_BYTES}]"
                )
            else:
                content = truncation.content
                if truncation.truncated:
                    end_display = start_display + truncation.output_lines - 1
                    # 字节限截断时标注字节预算（对齐 pi truncatedBy==="bytes" 变体）
                    limit_note = (
                        f" ({format_size(DEFAULT_MAX_BYTES)} limit)"
                        if truncation.truncated_by == "bytes"
                        else ""
                    )
                    notices.append(
                        f"[Showing lines {start_display}-{end_display} of "
                        f"{total_file_lines}{limit_note}. Use offset={end_display + 1} "
                        "to continue.]"
                    )
                elif limit is not None and end < total_file_lines:
                    notices.append(
                        f"[Showing lines {start_display}-{end} of "
                        f"{total_file_lines}. Use offset={end + 1} to continue.]"
                    )

            ext = os.path.splitext(path)[1].lstrip(".") or "text"

            line_info = ""
            if offset is not None or limit is not None:
                line_info = f"\n**行范围**: 自第 {start_display} 行起"
            notice_text = ("\n\n" + "\n".join(notices)) if notices else ""

            msg = f"""## ✅ 文件读取成功

**路径**: `{path}`{line_info}

```{ext}
{content}
```{notice_text}"""
            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={
                    "path": path,
                    "lines": truncation.output_lines,
                    "truncated": truncation.truncated,
                    "truncated_by": truncation.truncated_by,
                    "total_lines": total_file_lines,
                },
            )
        except Exception as e:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text=f"## ❌ 读取失败\n\n路径: `{path}`\n错误: {e}"
                    )
                ],
                details={"error": str(e), "path": path},
                is_error=True,
            )
