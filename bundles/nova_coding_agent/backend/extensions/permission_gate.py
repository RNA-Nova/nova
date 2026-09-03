"""权限门扩展（permission gate）。

对齐 pi 的 permission-gate.ts 与 protected-paths.ts 两个示例扩展（合并为一）：

- bash 危险命令（``rm -rf`` / ``sudo`` / ``chmod|chown 777``）：
  有 UI 时弹选择器询问，拒绝/取消即 block；无 UI（headless/print）
  直接 block（fail-closed，与 pi 同款语义）；
- write/edit 写保护路径（``.env`` / ``.git/`` / ``node_modules/``）：
  命中直接 block 并 notify，不询问（pi 同款）；
- 超集一项：会话级 "Always" 记忆——同一**精确命令串**在扩展实例
  生命周期内（reload 即失效）不再重复询问。

block 的 reason 会作为错误工具结果回给 LLM（框架 loop 行为），模型
可据此调整方案。规则硬编码对齐 pi；需要自定义时按 pi 惯例走扩展自有
配置（本扩展即代码，可替换），不进框架 settings。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from nova_harness.core.extensions.api import NovaExtensionAPI
from nova_harness.core.types.events.results import ToolCallEventResult

from nova_coding_agent.ui_primitives import notify_message, select

# bash 危险命令模式（pi permission-gate.ts 同款三条）
_DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+(-rf?|--recursive)", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\b(chmod|chown)\b.*777", re.IGNORECASE),
]

# write/edit 保护路径片段（pi protected-paths.ts 同款三个，子串匹配）
_PROTECTED_PATH_PARTS = [".env", ".git/", "node_modules/"]

_CHOICE_YES = "Yes"
_CHOICE_ALWAYS = "Always"
_CHOICE_NO = "No"


def _is_dangerous_command(command: str) -> bool:
    return any(pattern.search(command) for pattern in _DANGEROUS_PATTERNS)


def _protected_path_hit(path: str) -> Optional[str]:
    for part in _PROTECTED_PATH_PARTS:
        if part in path:
            return part
    return None


def extension(nova: NovaExtensionAPI) -> None:
    """注册 tool_call 拦截 handler。"""
    # 会话级 Always 记忆：精确命令串（闭包状态，随扩展实例生命周期）
    allowed_commands: set[str] = set()

    async def _gate_bash(command: str, ctx: Any) -> Optional[ToolCallEventResult]:
        if not _is_dangerous_command(command):
            return None
        if command in allowed_commands:
            return None
        if not ctx.has_ui:
            return ToolCallEventResult(
                block=True,
                reason="Dangerous command blocked (no UI for confirmation)",
            )

        choice = await select(
            ctx.ui,
            f"⚠️ Dangerous command:\n\n  {command}\n\nAllow?",
            [_CHOICE_YES, _CHOICE_ALWAYS, _CHOICE_NO],
        )
        if choice == _CHOICE_YES:
            return None
        if choice == _CHOICE_ALWAYS:
            allowed_commands.add(command)
            return None
        return ToolCallEventResult(block=True, reason="Blocked by user")

    def _gate_write_path(path: str, ctx: Any) -> Optional[ToolCallEventResult]:
        hit = _protected_path_hit(path)
        if hit is None:
            return None
        if ctx.has_ui:
            notify_message(
                ctx.ui, f"Blocked write to protected path: {path}", "warning"
            )
        return ToolCallEventResult(block=True, reason=f'Path "{path}" is protected')

    async def _on_tool_call(event: Any, ctx: Any) -> Optional[ToolCallEventResult]:
        args = event.args if isinstance(event.args, dict) else {}

        if event.tool_name == "bash":
            command = args.get("command")
            if isinstance(command, str) and command:
                return await _gate_bash(command, ctx)
            return None

        if event.tool_name in ("write", "edit"):
            path = args.get("path")
            if isinstance(path, str) and path:
                return _gate_write_path(path, ctx)
            return None

        return None

    nova.on("tool_call", _on_tool_call)
