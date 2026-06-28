"""Grep tool executor —— 搜索文件内容。

优先调用 ``rg --json``，未安装时使用 ``re`` 回退实现。
"""

import fnmatch
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent
from nova_harness.core.tools_common.path_utils import is_path_traversal, resolve_path

MAX_RESULTS = 100


def _grep_with_rg(
    path: str, regex: str, file_pattern: Optional[str], case_sensitive: bool
) -> List[Dict[str, Any]]:
    """使用 rg 搜索，返回结构化结果。"""
    args = ["rg", "--json", "--line-number", "--max-count", str(MAX_RESULTS)]
    if not case_sensitive:
        args.append("--ignore-case")
    if file_pattern:
        args.extend(["--glob", file_pattern])
    args.extend(["--", regex, path])

    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
    except Exception:
        return []

    results = []
    for line in proc.stdout.splitlines():
        try:
            import json

            data = json.loads(line)
            if data.get("type") != "match":
                continue
            payload = data.get("data", {})
            path_obj = payload.get("path", {})
            file_path = path_obj.get("text", "")
            line_num = payload.get("line_number", 0)
            lines = payload.get("lines", {})
            text = lines.get("text", "")
            results.append(
                {
                    "path": file_path,
                    "line": line_num,
                    "text": text.rstrip("\n"),
                }
            )
        except Exception:
            continue
    return results


def _grep_with_python(
    path: str, regex: str, file_pattern: Optional[str], case_sensitive: bool
) -> List[Dict[str, Any]]:
    """纯 Python 回退实现。"""
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(regex, flags)
    results = []

    targets = [path]
    if os.path.isdir(path):
        targets = []
        for root, _, files in os.walk(path):
            for name in files:
                if file_pattern and not fnmatch.fnmatch(name, file_pattern):
                    continue
                targets.append(os.path.join(root, name))

    for file_path in targets:
        if not os.path.isfile(file_path):
            continue
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    if pattern.search(line):
                        results.append(
                            {
                                "path": file_path,
                                "line": lineno,
                                "text": line.rstrip("\n"),
                            }
                        )
                        if len(results) >= MAX_RESULTS:
                            return results
        except Exception:
            continue
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
        regex = params.get("regex", "")
        file_pattern = params.get("file_pattern")
        case_sensitive = params.get("case_sensitive", False)

        if not path or not regex:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="## ❌ 参数错误\n\n必须提供 path 和 regex 参数",
                    )
                ],
                details={"error": "Missing required parameter: path or regex"},
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
                    TextContent(type="text", text=f"## ❌ 路径不存在\n\n路径: `{path}`")
                ],
                details={"error": "Path not found", "path": path},
            )

        try:
            using = "rg"
            if shutil.which("rg"):
                results = _grep_with_rg(path, regex, file_pattern, case_sensitive)
            else:
                using = "python (rg 未安装，使用 fallback)"
                results = _grep_with_python(path, regex, file_pattern, case_sensitive)

            if not results:
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"## 🔍 未找到匹配\n\n路径: `{path}`\n正则: `{regex}`",
                        )
                    ],
                    details={"path": path, "regex": regex, "count": 0},
                )

            lines = [f"## 🔍 搜索结果（{using}，共 {len(results)} 条）\n"]
            for r in results:
                lines.append(f"`{r['path']}:{r['line']}`: {r['text']}")

            msg = "\n".join(lines)
            if len(results) >= MAX_RESULTS:
                msg += f"\n\n> ⚠️ 结果超过 {MAX_RESULTS} 条，已截断"

            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={
                    "path": path,
                    "regex": regex,
                    "count": len(results),
                    "results": results,
                },
            )
        except Exception as e:
            return AgentToolResult(
                content=[TextContent(type="text", text=f"## ❌ 搜索失败\n\n错误: {e}")],
                details={"error": str(e), "path": path, "regex": regex},
            )
