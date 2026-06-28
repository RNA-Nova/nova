"""Find tool executor —— 查找文件或目录。

优先调用 ``fd``，未安装时使用 ``pathlib`` 回退实现。
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent
from nova_harness.core.tools_common.path_utils import is_path_traversal, resolve_path

MAX_RESULTS = 200


def _find_with_fd(path: str, glob: Optional[str], find_type: str) -> List[str]:
    """使用 fd 搜索，返回绝对路径列表。"""
    args = ["fd", "--absolute-path", "--max-results", str(MAX_RESULTS)]
    if find_type == "directory":
        args.extend(["--type", "d"])
    else:
        args.extend(["--type", "f"])
    if glob:
        args.extend(["--glob", glob])
    # fd 的搜索模式：空字符串匹配所有
    args.extend(["", path])

    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        return [line for line in proc.stdout.splitlines() if line]
    except Exception:
        return []


def _find_with_python(path: str, glob: Optional[str], find_type: str) -> List[str]:
    """纯 Python 回退实现。"""
    root = Path(path)
    results = []
    pattern = glob or "*"

    if find_type == "directory":
        candidates = [p for p in root.rglob("*") if p.is_dir()]
    else:
        candidates = [p for p in root.rglob("*") if p.is_file()]

    for candidate in candidates:
        if glob and not candidate.match(pattern):
            continue
        results.append(str(candidate.resolve()))
        if len(results) >= MAX_RESULTS:
            break
    return results


class ToolExecutor:
    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ):
        path = params.get("path", "")
        glob = params.get("glob")
        find_type = params.get("type", "file")

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

        if not os.path.isdir(path):
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"## ❌ 不是目录\n\n路径: `{path}`")
                ],
                details={"error": "Not a directory", "path": path},
            )

        try:
            using = "fd"
            if shutil.which("fd"):
                results = _find_with_fd(path, glob, find_type)
            else:
                using = "python (fd 未安装，使用 fallback)"
                results = _find_with_python(path, glob, find_type)

            if not results:
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text", text=f"## 🔍 未找到结果\n\n路径: `{path}`"
                        )
                    ],
                    details={"path": path, "count": 0},
                )

            lines = [f"## 🔍 查找结果（{using}，共 {len(results)} 条）\n"]
            lines.extend(results)

            msg = "\n".join(lines)
            if len(results) >= MAX_RESULTS:
                msg += f"\n\n> ⚠️ 结果超过 {MAX_RESULTS} 条，已截断"

            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={"path": path, "count": len(results), "results": results},
            )
        except Exception as e:
            return AgentToolResult(
                content=[TextContent(type="text", text=f"## ❌ 查找失败\n\n错误: {e}")],
                details={"error": str(e), "path": path},
            )
