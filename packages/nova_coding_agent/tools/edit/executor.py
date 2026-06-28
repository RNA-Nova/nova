"""Edit tool executor —— 批量替换文件中的文本片段。"""

import difflib
import os
from typing import Any, Dict, List, Optional, Tuple

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent
from nova_harness.core.tools_common.file_queue import with_file_write_lock
from nova_harness.core.tools_common.path_utils import is_path_traversal, resolve_path


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


def _apply_edits(text: str, edits: List[Dict[str, str]]) -> Tuple[str, int, List[str]]:
    """依次应用 edits，返回 (新内容, 总替换次数, diff 列表)。"""
    total = 0
    diffs = []
    for edit in edits:
        old_text = edit.get("oldText", "")
        new_text = edit.get("newText", "")
        if old_text == "":
            diffs.append("⚠️ 跳过空 oldText 的编辑项")
            continue
        count = text.count(old_text)
        if count == 0:
            diffs.append(f"❌ 未找到: {old_text[:40]!r}")
            continue
        text = text.replace(old_text, new_text)
        total += count
        diffs.append(f"✅ {count} 处替换: {old_text[:40]!r} -> {new_text[:40]!r}")
    return text, total, diffs


class ToolExecutor:
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

        if not os.path.exists(path):
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 文件不存在\n\n路径: `{path}`")
                ],
                details={"error": "File not found", "path": path},
            )

        try:
            async with with_file_write_lock(path):
                with open(path, "r", encoding=encoding) as f:
                    original = f.read()

                original, bom = _strip_bom(original)
                line_sep = _detect_line_endings(original)

                # 统一用 \n 处理，编辑完成后再恢复
                normalized = original.replace("\r\n", "\n")
                new_text, total, diffs = _apply_edits(normalized, edits)

                if total == 0:
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
                            "details": diffs,
                        },
                    )

                # 恢复换行符与 BOM
                if line_sep == "\r\n":
                    new_text = new_text.replace("\n", "\r\n")
                final_text = bom + new_text

                with open(path, "w", encoding=encoding) as f:
                    f.write(final_text)

            # 生成 diff 摘要
            diff_lines = list(
                difflib.unified_diff(
                    original.splitlines(),
                    new_text.splitlines(),
                    lineterm="",
                    n=2,
                )
            )
            diff_summary = "\n".join(diff_lines[:40])
            if len(diff_lines) > 40:
                diff_summary += "\n...（diff 过长，已截断）"

            edits_summary = "\n".join(diffs)
            msg = f"""## ✅ 文件编辑成功

**路径**: `{path}`
**总替换次数**: {total}
**字符变化**: {len(final_text) - len(bom + original):+d}

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
                    "replacements": total,
                    "delta": len(final_text) - len(bom + original),
                    "details": diffs,
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
