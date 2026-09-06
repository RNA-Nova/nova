"""子进程树管理与 detached 进程跟踪。

对齐 pi ``utils/shell.ts``：以新会话（detached）启动的子进程必须被跟踪，
以便父进程收到关闭信号（SIGHUP/SIGTERM）时统一清场，不留孤儿进程。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Dict, Set

_tracked_detached_child_pids: Set[int] = set()


def hidden_console_kwargs() -> Dict[str, int]:
    """Windows 子进程不弹控制台窗口的 Popen kwargs（POSIX 返回空 dict）。

    打包形态下后端自身以隐藏控制台运行（TUI 侧 spawn 带 windowsHide），
    此后端再 spawn 的任何 console 子进程都会新建**可见**控制台——黑窗
    闪烁（子代理、rg/fd、pip/git、!cmd 鉴权命令皆是）。一律经此 helper
    取 kwargs；POSIX 无控制台语义，返回空。
    """
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def track_detached_child_pid(pid: int) -> None:
    """登记一个 detached 子进程 pid。"""
    _tracked_detached_child_pids.add(pid)


def untrack_detached_child_pid(pid: int) -> None:
    """解除登记（进程正常结束后调用）。"""
    _tracked_detached_child_pids.discard(pid)


def kill_tracked_detached_children() -> None:
    """kill 所有被跟踪的 detached 子进程（父进程关闭信号处理用）。"""
    for pid in list(_tracked_detached_child_pids):
        kill_process_tree(pid)
    _tracked_detached_child_pids.clear()


def kill_process_tree(pid: int, sig: "int | None" = None) -> None:
    """kill 一个进程及其整棵子树（跨平台）。

    POSIX：子进程以新会话启动，pid 即进程组组长，直接对组发信号；
    失败（组不存在等）回退单进程。Windows：``taskkill /F /T``
    （无信号语义，总是强制）。
    """
    if sig is None:
        # 缺省强杀信号：Windows 的 signal 模块没有 SIGKILL（其 kill 语义
        # 由上面的 taskkill 分支承载，sig 不被消费）——默认值不能写在
        # 签名上，否则导入期就在 Windows 炸 AttributeError
        sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    if sys.platform == "win32":
        try:
            subprocess.Popen(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            pass
        return
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


__all__ = [
    "kill_process_tree",
    "kill_tracked_detached_children",
    "hidden_console_kwargs",
    "track_detached_child_pid",
    "untrack_detached_child_pid",
]
