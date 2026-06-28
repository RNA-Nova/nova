"""Bash tool executor —— 执行 shell 命令。"""

import asyncio
import os
import shutil
from typing import Any, Dict, Optional

from nova_agent import AbortSignal, AgentToolResult
from nova_ai import TextContent
from nova_harness.core.tools_common.truncate import truncate_output

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_OUTPUT_CHARS = 10000


class ToolExecutor:
    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ):
        command = params.get("command", "")
        cwd = params.get("cwd") or os.getcwd()
        timeout = params.get("timeout", DEFAULT_TIMEOUT)
        env_extra = params.get("env") or {}

        if not command:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text", text="## ❌ 参数错误\n\n必须提供 command 参数"
                    )
                ],
                details={"error": "Missing required parameter: command"},
            )

        if not os.path.isabs(cwd):
            cwd = os.path.abspath(cwd)

        # 优先使用系统 bash，fallback 到 /bin/sh
        shell = shutil.which("bash") or "/bin/sh"
        env = {**os.environ, **env_extra}

        try:
            proc = await asyncio.create_subprocess_exec(
                shell,
                "-c",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            stdout_chunks = []
            stderr_chunks = []

            async def read_stream(stream, chunks):
                while True:
                    if signal and signal.aborted:
                        proc.kill()
                        return
                    try:
                        line = await asyncio.wait_for(stream.readline(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace")
                    chunks.append(text)
                    if on_update:
                        on_update(text)

            await asyncio.gather(
                read_stream(proc.stdout, stdout_chunks),
                read_stream(proc.stderr, stderr_chunks),
            )

            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return AgentToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"## ⏱️ 命令超时\n\n命令: `{command}`\n超过 {timeout} 秒未完成",
                        )
                    ],
                    details={
                        "error": "Timeout",
                        "command": command,
                        "timeout": timeout,
                    },
                )

            stdout_text = "".join(stdout_chunks)
            stderr_text = "".join(stderr_chunks)

            # 合并并按字符数截断
            full_output = stdout_text + stderr_text
            truncated_output, was_truncated = truncate_output(
                full_output, DEFAULT_MAX_OUTPUT_CHARS
            )

            output_parts = []
            if stdout_text:
                output_parts.append(f"**stdout**:\n```\n{stdout_text}\n```")
            if stderr_text:
                output_parts.append(f"**stderr**:\n```\n{stderr_text}\n```")

            status = "成功" if proc.returncode == 0 else "失败"
            output_body = "\n\n".join(output_parts) if output_parts else "（无输出）"
            if was_truncated:
                output_body += f"\n\n> ⚠️ 输出过长，已按字符截断显示（原始 {len(full_output)} 字符）"

            msg = f"""## {'✅' if proc.returncode == 0 else '❌'} 命令执行{status}

**命令**: `{command}`
**工作目录**: `{cwd}`
**退出码**: {proc.returncode}

{output_body}
"""
            return AgentToolResult(
                content=[TextContent(type="text", text=msg)],
                details={
                    "command": command,
                    "cwd": cwd,
                    "returncode": proc.returncode,
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "truncated": was_truncated,
                },
            )
        except Exception as e:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"## ❌ 执行失败\n\n命令: `{command}`\n错误: {e}",
                    )
                ],
                details={"error": str(e), "command": command},
            )
