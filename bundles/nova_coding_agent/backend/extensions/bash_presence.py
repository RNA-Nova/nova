"""bash_presence —— Windows 无 bash 的首启引导提示。

coding_agent 的 bash 工具在 Windows 上必须有 bash（Git Bash）。安装器路径
（install.ps1）自带供给（管理态 PortableGit），但绕过安装器的进入路径
（手动解压/绿色版/U 盘拷贝）上没有任何引导——用户要到第一次跑 bash 工具
才撞见 FileNotFoundError。

本扩展在 session_start（reason=="start"，reload/agent_change 不重复打扰）
检测：win32 + shell 解析失败（settings shell_path → Git Bash 已知位置 →
PATH 都落空）→ 经 notify_message 引导三选路。纯提示不代装（PortableGit
供给归安装器）。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

from nova_base.ui_primitives import notify_message
from nova_coding_agent.tools_common.shell import get_shell_config

from nova_harness.core.config.defaults import get_agent_dir

_GUIDANCE = (
    "未找到 bash——bash 工具在本机不可用。三选路："
    "1) 装 Git for Windows（https://git-scm.com/download/win）；"
    "2) 把 bash 加入 PATH；"
    "3) settings.json 里设 shell_path 指向已有 bash。"
    "（重跑一次安装器 install.ps1 会自动供给管理态 PortableGit）"
)


def _configured_shell_path(cwd: str) -> Optional[str]:
    """读合并 settings 的 shell_path（全局 + 项目级，项目覆盖全局）。

    扩展上下文没有 settings 访问面——bash 引擎经 ToolContext 取同一键，
    这里直读同一文件源保持一致（读失败按未配置处理，不错过引导）。
    """
    shell_path: Optional[str] = None
    for path in (
        os.path.join(str(get_agent_dir()), "settings.json"),
        os.path.join(cwd, ".nova", "settings.json"),
    ):
        try:
            with open(path, encoding="utf-8") as f:
                value = json.load(f).get("shell_path")
            if isinstance(value, str) and value:
                shell_path = value
        except (OSError, ValueError):
            continue
    return shell_path


def extension(nova: Any) -> None:
    """注册 session_start 钩子。"""

    async def _on_session_start(event: Any, ctx: Any) -> None:
        if sys.platform != "win32":
            return
        # 只在首次启动提示（reload/agent_change/分支导航不重复打扰）
        if getattr(event, "reason", None) != "start":
            return
        if not getattr(ctx, "has_ui", False):
            return
        try:
            get_shell_config(_configured_shell_path(getattr(ctx, "cwd", os.getcwd())))
        except FileNotFoundError:
            notify_message(ctx.ui, _GUIDANCE, "warning")

    nova.on("session_start", _on_session_start)


__all__ = ["extension"]
