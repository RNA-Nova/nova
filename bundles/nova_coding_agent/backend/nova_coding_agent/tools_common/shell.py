"""Shell 输出清洗与 shell 解析纯函数。

对齐 pi 的 ``utils/ansi.ts``（stripAnsi）、``utils/shell.ts``
（sanitizeBinaryOutput / getShellConfig）：会话级 bash 输出在记录前剥离
ANSI 转义序列、过滤二进制控制字符；shell 解析跨平台（POSIX / Git Bash /
WSL / sh 兜底）。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import List, Literal, Optional

# 对齐 pi ansi.ts：OSC 序列（ESC ] ... ST）与 CSI 序列
_ST = r"(?:\x07|\x1b\\|\x9c)"
_OSC = rf"(?:\x1b\][\s\S]*?{_ST})"
_CSI = r"[\x1b\x9b][\[\]()#;?]*(?:\d{1,4}(?:[;:]\d{0,4})*)?[\dA-PR-TZcf-nq-uy=><~]"
_ANSI_PATTERN = re.compile(rf"{_OSC}|{_CSI}")


def strip_ansi(value: str) -> str:
    """剥离 ANSI 转义序列。"""
    # 快速路径：没有 ESC/CSI 引导符直接返回
    if "\x1b" not in value and "\x9b" not in value:
        return value
    return _ANSI_PATTERN.sub("", value)


def sanitize_binary_output(value: str) -> str:
    """过滤会导致渲染/宽度计算崩溃的字符。

    保留 ``\\t``/``\\n``/``\\r``；过滤其余 C0 控制字符与 Unicode
    格式字符（0xFFF9-0xFFFB）。
    """
    return "".join(
        char
        for char in value
        if (code := ord(char)) in (0x09, 0x0A, 0x0D)
        or (code > 0x1F and not (0xFFF9 <= code <= 0xFFFB))
    )


def sanitize_shell_output(value: str) -> str:
    """会话 bash 输出的标准清洗：strip ANSI → 消毒 → 归一换行。"""
    return sanitize_binary_output(strip_ansi(value)).replace("\r", "")


# ---------------------------------------------------------------------------
# Shell 解析（对齐 pi getShellConfig）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShellConfig:
    """Shell 启动配置。

    ``command_transport``：``argv`` 表示命令作为参数传入（``shell -c cmd``）；
    ``stdin`` 表示命令经标准输入传入（WSL bash.exe 经 argv 传命令会被
    Windows 侧转义破坏，必须走 stdin）。
    """

    shell: str
    args: List[str]
    command_transport: Literal["argv", "stdin"] = "argv"


def _is_legacy_wsl_bash_path(path: str) -> bool:
    """判断是否为 WSL 的 bash.exe 路径（C:\\Windows\\System32\\bash.exe）。"""
    normalized = path.replace("/", "\\").lower()
    return (
        re.match(r"^[a-z]:\\windows\\(?:system32|sysnative)\\bash\.exe$", normalized)
        is not None
    )


def _bash_shell_config(shell: str) -> ShellConfig:
    if _is_legacy_wsl_bash_path(shell):
        return ShellConfig(shell=shell, args=["-s"], command_transport="stdin")
    return ShellConfig(shell=shell, args=["-c"])


def get_shell_config(custom_shell_path: Optional[str] = None) -> ShellConfig:
    """解析 shell 启动配置（跨平台）。

    顺序：显式 shell 路径 → Windows：Git Bash 已知位置 → PATH 上的 bash →
    POSIX：/bin/bash → PATH 上的 bash → sh 兜底。
    """
    if custom_shell_path:
        if os.path.exists(custom_shell_path):
            return _bash_shell_config(custom_shell_path)
        raise FileNotFoundError(f"Custom shell path not found: {custom_shell_path}")

    if sys.platform == "win32":
        candidates: List[str] = []
        for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_key)
            if base:
                candidates.append(os.path.join(base, "Git", "bin", "bash.exe"))
        for path in candidates:
            if os.path.exists(path):
                return _bash_shell_config(path)
        bash_on_path = shutil.which("bash.exe") or shutil.which("bash")
        if bash_on_path:
            return _bash_shell_config(bash_on_path)
        raise FileNotFoundError(
            "No bash shell found. Options:\n"
            "  1. Install Git for Windows: https://git-scm.com/download/win\n"
            "  2. Add your bash to PATH (Cygwin, MSYS2, etc.)\n"
            "  3. Set shell_path in settings.json\n"
            "Searched Git Bash in:\n" + "\n".join(f"  {p}" for p in candidates)
        )

    if os.path.exists("/bin/bash"):
        return _bash_shell_config("/bin/bash")
    bash_on_path = shutil.which("bash")
    if bash_on_path:
        return _bash_shell_config(bash_on_path)
    return ShellConfig(shell="sh", args=["-c"])


__all__ = [
    "ShellConfig",
    "get_shell_config",
    "strip_ansi",
    "sanitize_binary_output",
    "sanitize_shell_output",
]
