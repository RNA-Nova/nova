"""Edit tool executor —— 批量替换文件中的文本片段。"""

import difflib
from typing import Any, Dict, List, Optional, Tuple

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent

from nova_coding_agent.tools_common.file_queue import with_file_write_lock
from nova_coding_agent.tools_common.operations import (
    EditOperations,
    create_local_edit_operations,
)
from nova_coding_agent.tools_common.path_utils import is_path_traversal, resolve_path


def _detect_line_endings(text: str) -> str:
    """检测文件换行符，优先 CRLF。"""
    if "\r\n" in text:
        return "\r\n"
    return "\n"


def _strip_bom(text: str) -> Tuple[str, str]:
    """移除 BOM，返回 (内容, BOM 字符串)。"""
    if text.startswith("\ufeff"):
        return text[1:], "\ufeff"
    return text, ""


class ToolExecutor:
    def __init__(
        self,
        operations: Optional[EditOperations] = None,
    ):
        self.operations = operations or create_local_edit_operations()

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ):
        path = params.get("path", "")
        edits = params.get("edits") or []
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

        if not isinstance(edits, list) or not edits:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text="## ❌ 参数错误\n\nedits 必须是非空数组"
                    )
                ],
                details={"error": "Missing or invalid edits parameter"},
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
            async with with_file_write_lock(path):
                original = await self.operations.read_text(path, encoding=encoding)

                original_stripped, bom = _strip_bom(original)
                line_sep = _detect_line_endings(original_stripped)

                # 统一用 \n 处理，编辑完成后再恢复
                normalized = original_stripped.replace("\r\n", "\n")
                result = self.operations.apply_edits(normalized, edits)

                if result.error:
                    raise RuntimeError(result.error)

                if result.total_replacements == 0:
                    return AgentToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=f"## ❌ 编辑失败\n\n路径: `{path}`\n\n未找到任何匹配项。",
                            )
                        ],
                        details={
                            "error": "No edits applied",
                            "path": path,
                            "details": result.diffs,
                        },
                    )

                # 恢复换行符与 BOM
                new_text = result.new_text
                if line_sep == "\r\n":
                    new_text = new_text.replace("\n", "\r\n")
                final_text = bom + new_text

                await self.operations.write_text(path, final_text, encoding=encoding)

            # 生成 diff 摘要
            diff_lines = list(
                difflib.unified_diff(
                    original_stripped.splitlines(),
                    result.new_text.splitlines(),
                    lineterm="",
                    n=2,
                )
            )
            diff_summary = "\n".join(diff_lines[:40])
            if len(diff_lines) > 40:
                diff_summary += "\n...（diff 过长，已截断）"

            edits_summary = "\n".join(result.diffs)
            msg = f"""## ✅ 文件编辑成功

**路径**: `{path}`
**总替换次数**: {result.total_replacements}
**字符变化**: {len(final_text) - len(bom + original_stripped):+d}

**编辑详情**:
{edits_summary}

**Diff 摘要**:
```diff
{diff_summary}
```"""
            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={
                    "path": path,
                    "replacements": result.total_replacements,
                    "delta": len(final_text) - len(bom + original_stripped),
                    "details": result.diffs,
                },
            )
        except Exception as e:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text=f"## ❌ 编辑失败\n\n路径: `{path}`\n错误: {e}"
                    )
                ],
                details={"error": str(e), "path": path},
            )
