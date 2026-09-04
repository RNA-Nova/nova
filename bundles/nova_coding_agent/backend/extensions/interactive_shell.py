"""交互式 shell 扩展。

订阅 ``user_bash`` 事件（只拦用户 ``!`` 命令，不拦 LLM bash 工具）：

- **判定**：命令首 token（strip 后）在交互程序集内，或以 ``i `` 前缀
  强制交互（前缀剥离后作为真实命令）；
- **命中且有面板能力**（``dialog:interactive-shell`` 已注册）：命令交给
  前端在真实终端里执行（TUI 让位），应答 ``{"exitCode": int}``（值键
  camel，TS 侧产出）→ 按 ``_intercept_user_bash`` 的形状返回完整
  ``result`` 替换执行；cancelled → exitCode=130 的取消回执；
- **命中但无能力/无 UI**：``(interactive commands require TUI)``
  exitCode=1（避免无 TTY 环境下挂死）；
- **未命中**：不返回（None），正常执行。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from nova_harness.core.extensions.api import NovaExtensionAPI

# 交互程序集（首 token 精确命中）
_INTERACTIVE_COMMANDS = {
    "vi",
    "vim",
    "nvim",
    "nano",
    "emacs",
    "htop",
    "top",
    "less",
    "more",
    "man",
    "ssh",
    "tig",
    "lazygit",
    "watch",
}

# 强制交互前缀（``i <cmd>``——剥离后作为真实命令）
_FORCE_PREFIX = "i "


def _resolve_interactive(command: str) -> Optional[str]:
    """命中返回真实命令（``i `` 前缀已剥离），未命中返回 None。"""
    stripped = command.strip()
    if not stripped:
        return None
    if stripped.startswith(_FORCE_PREFIX):
        return stripped[len(_FORCE_PREFIX) :].strip() or None
    first = stripped.split(None, 1)[0]
    if first in _INTERACTIVE_COMMANDS:
        return stripped
    return None


async def _on_user_bash(event: Any, ctx: Any) -> Optional[Dict[str, Any]]:
    command = getattr(event, "command", "")
    if not isinstance(command, str):
        return None
    cmd = _resolve_interactive(command)
    if cmd is None:
        return None

    if not ctx.has_ui or not ctx.ui.has_capability("dialog:interactive-shell"):
        # 无前端接管时直接失败（不挂死）
        return {
            "result": {
                "output": "(interactive commands require TUI)",
                "exitCode": 1,
            }
        }

    resp = await ctx.ui.request(
        "dialog:interactive-shell",
        {"command": cmd, "cwd": getattr(event, "cwd", None) or ctx.cwd},
    )
    if resp.cancelled:
        return {
            "result": {
                "output": "(交互式命令已取消)",
                "exitCode": 130,
                "cancelled": True,
            }
        }
    value = resp.value if isinstance(resp.value, dict) else {}
    code = value.get("exitCode")
    if not isinstance(code, int):
        code = 0
    return {"result": {"output": "", "exitCode": code}}


def extension(nova: NovaExtensionAPI) -> None:
    """注册 user_bash 拦截 handler。"""
    nova.on("user_bash", _on_user_bash)
