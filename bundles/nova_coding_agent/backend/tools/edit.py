"""Edit tool executor —— 精确文本替换（对齐 pi ``core/tools/edit.ts``）。

语义（经 ``tools_common/edit_engine`` 保证）：
- 每个 ``edits[].oldText`` 必须在原文中唯一（出现多次报错）；
- 全部 edit 针对同一份原文匹配，重叠报错；
- 找不到/空 oldText/无变化 → 整个调用报错，不写盘（原子性）；
- fuzzy 匹配兜底（智能引号/破折号/行尾空白归一）。
"""

import errno
import json
from typing import Any, Dict, List, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_harness.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)

from nova_coding_agent.executor import backend_file_layer, resolve_backend_path
from nova_coding_agent.tools_common.edit_engine import (
    Edit,
    apply_edits_to_normalized_content,
    detect_line_ending,
    generate_diff_string,
    generate_unified_patch,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)
from nova_coding_agent.tools_common.file_queue import with_file_write_lock
from nova_coding_agent.tools_common.operations import (
    EditOperations,
    create_local_edit_operations,
)


def _throw_if_aborted(signal: Optional[AbortSignal]) -> None:
    """步骤间检查 abort（对齐 pi：不在事件监听里 reject，保住队列锁）。"""
    if signal is not None and getattr(signal, "aborted", False):
        raise RuntimeError("Operation aborted")


def _error_detail(exc: Exception) -> str:
    """提取错误细节（对齐 pi：OSError 透出 errno 错误码如 ENOENT/EACCES）。"""
    err_no = getattr(exc, "errno", None)
    if err_no is not None:
        return f"Error code: {errno.errorcode.get(err_no, err_no)}"
    return str(exc)


def _prepare_edits(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """归一化 edits 入参（对齐 pi prepareEditArguments）。

    - 部分模型把 edits 作为 JSON 字符串发送 → 尝试解析；
    - 旧式顶层 ``oldText``/``newText`` → 并入 edits 列表。
    """
    edits = params.get("edits")
    if isinstance(edits, str):
        try:
            parsed = json.loads(edits)
            if isinstance(parsed, list):
                edits = parsed
        except Exception:
            pass

    result: List[Dict[str, Any]] = (
        [e for e in edits if isinstance(e, dict)] if isinstance(edits, list) else []
    )
    old_text = params.get("oldText")
    new_text = params.get("newText")
    if isinstance(old_text, str) and isinstance(new_text, str):
        result.append({"oldText": old_text, "newText": new_text})
    return result


class Tool:
    name = "edit"
    description = (
        "Edit a single file using exact text replacement. Every edits[].oldText "
        "must match a unique, non-overlapping region of the original file. If two "
        "changes affect the same block or nearby lines, merge them into one edit "
        "instead of emitting overlapping edits. Do not include large unchanged "
        "regions just to connect distant changes."
    )
    prompt_snippet = (
        "Make precise file edits with exact text replacement, including "
        "multiple disjoint edits in one call"
    )
    prompt_guidelines = [
        "Use edit for precise changes (edits[].oldText must match exactly)",
        "When changing multiple separate locations in one file, use one edit "
        "call with multiple entries in edits[] instead of multiple edit calls",
        "Each edits[].oldText is matched against the original file, not after "
        "earlier edits are applied. Do not emit overlapping or nested edits. "
        "Merge nearby changes into one edit.",
        "Keep edits[].oldText as small as possible while still being unique "
        "in the file. Do not pad with large unchanged regions.",
    ]
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对或绝对路径）"},
            "edits": {
                "type": "array",
                "description": (
                    "要执行的编辑项列表。每项 oldText 必须在原文中唯一且不与其他 "
                    "edit 重叠；匹配针对原文而非增量结果。"
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "oldText": {
                            "type": "string",
                            "description": "要替换的原文本（必须唯一匹配）",
                        },
                        "newText": {
                            "type": "string",
                            "description": "替换后的新文本",
                        },
                    },
                    "required": ["oldText", "newText"],
                },
            },
            "encoding": {
                "type": "string",
                "default": "utf-8",
                "description": "文件编码",
            },
        },
        "required": ["path", "edits"],
    }

    def __init__(
        self,
        context: ToolContext,
        operations: Optional[EditOperations] = None,
    ):
        self._context = context
        self.operations = operations or create_local_edit_operations()
        self._remote_cache = None

    def _resolve_operations(self) -> EditOperations:
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
        path = params.get("path", "")
        encoding = params.get("encoding", "utf-8")
        edits = _prepare_edits(params)

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

        if not edits:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="## ❌ 参数错误\n\nedits 必须是非空数组",
                    )
                ],
                details={"error": "edits must contain at least one replacement"},
                is_error=True,
            )

        path = resolve_backend_path(path, self._context)
        operations = self._resolve_operations()

        try:
            async with with_file_write_lock(path):
                _throw_if_aborted(signal)
                # 读写权限 fail-fast（对齐 pi access(R_OK|W_OK)）：不存在/只读
                # 文件在读与匹配之前报错，而不是等写盘才暴露
                try:
                    await operations.access(path)
                except Exception as exc:
                    detail = _error_detail(exc)
                    return AgentToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=f"## ❌ 编辑失败\n\n无法编辑文件: `{path}`\n{detail}",
                            )
                        ],
                        details={"error": detail, "path": path},
                        is_error=True,
                    )
                _throw_if_aborted(signal)
                try:
                    original = await operations.read_text(path, encoding=encoding)
                except Exception as exc:
                    return AgentToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=f"## ❌ 编辑失败\n\n无法读取文件: `{path}`\n{exc}",
                            )
                        ],
                        details={"error": str(exc), "path": path},
                        is_error=True,
                    )
                _throw_if_aborted(signal)

                # BOM 与换行符：匹配前剥离/归一，写回时恢复（对齐 pi 流程）
                bom, content = strip_bom(original)
                original_ending = detect_line_ending(content)
                normalized = normalize_to_lf(content)

                edit_specs = [
                    Edit(
                        old_text=str(e.get("oldText", "")),
                        new_text=str(e.get("newText", "")),
                    )
                    for e in edits
                    if isinstance(e, dict)
                ]
                try:
                    applied = apply_edits_to_normalized_content(
                        normalized, edit_specs, path
                    )
                except ValueError as exc:
                    return AgentToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=f"## ❌ 编辑失败\n\n路径: `{path}`\n\n{exc}",
                            )
                        ],
                        details={"error": str(exc), "path": path},
                        is_error=True,
                    )
                _throw_if_aborted(signal)

                final_text = bom + restore_line_endings(
                    applied.new_content, original_ending
                )
                await operations.write_text(path, final_text, encoding=encoding)

            diff, first_changed_line = generate_diff_string(
                applied.base_content, applied.new_content
            )
            patch = generate_unified_patch(
                path, applied.base_content, applied.new_content
            )

            msg = f"Successfully replaced {len(edit_specs)} block(s) in {path}."
            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={
                    "path": path,
                    "diff": diff,
                    "patch": patch,
                    "first_changed_line": first_changed_line,
                    "old": applied.base_content,
                    "new": applied.new_content,
                },
            )
        except RuntimeError as e:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text=f"## ❌ 编辑失败\n\n路径: `{path}`\n错误: {e}"
                    )
                ],
                details={"error": str(e), "path": path},
                is_error=True,
            )
