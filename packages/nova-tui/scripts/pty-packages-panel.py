#!/usr/bin/env python3
"""/packages 面板装卸载的 PTY 端到端验证。

沙盒装双官方包 → /packages 面板 → 详情动作选"卸载" nova-coding-agent →
断言：卸载通知出现、重开面板只剩 nova-base、列表里没有已卸包。

用法：python3 scripts/pty-packages-panel.py（无需 API key——面板操作不调模型）
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

smoke = import_module("pty-smoke")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✔' if ok else '✘'} {name}" + (f" —— {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    sandbox = tempfile.mkdtemp(prefix="nova-pty-pkg-")
    cwd = tempfile.mkdtemp(prefix="nova-pty-pkg-cwd-")
    with open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "default_project_trust": "always",
                "packages": [
                    {
                        "source": "path:/Users/liujinming/agent/nova-backup-20260824/bundles/nova_base",
                        "editable": True,
                    },
                    {
                        "source": "path:/Users/liujinming/agent/nova-backup-20260824/bundles/nova_coding_agent",
                        "editable": True,
                    },
                ],
            },
            f,
        )
    shutil.copytree(
        os.path.expanduser("~/.nova/agent/packages"),
        os.path.join(sandbox, "packages"),
        symlinks=True,
    )

    os.environ["NOVA_AGENT_DIR"] = sandbox
    tui = smoke.TuiSession(cwd)
    try:
        tui._drain(12.0)

        # 1. /packages 打开选择器（双包都在）
        checkpoint = len(tui.buffer)
        tui.send("/packages\r", 4.0)
        delta = tui.buffer[checkpoint:]
        check("包列表含双官方包", "nova-base" in delta and "nova-coding-agent" in delta)

        # 2. 选中 nova-coding-agent（输入即过滤）
        tui.send("coding", 2.0)
        tui.send("\r", 3.0)

        # 3. 动作列表选"卸载"（详情/更新/卸载——下移到第三项）
        tui.send("\x1b[B", 1.2)
        tui.send("\x1b[B", 1.2)
        checkpoint = len(tui.buffer)
        tui.send("\r", 6.0)
        delta = tui.buffer[checkpoint:]
        check(
            "卸载通知出现（资源已移除）",
            "已卸载 nova-coding-agent" in delta or "已卸载" in delta and "nova-coding-agent" in delta,
        )

        # 4. 重开面板：只剩 nova-base
        tui._drain(2.0)
        checkpoint = len(tui.buffer)
        tui.send("/packages\r", 4.0)
        delta = tui.buffer[checkpoint:]
        check(
            "重开面板只剩 nova-base",
            "nova-base" in delta and "nova-coding-agent" not in delta,
        )
        tui.send("\x1b", 1.5)
    finally:
        tui.close()
        os.environ.pop("NOVA_AGENT_DIR", None)
        shutil.rmtree(sandbox, ignore_errors=True)
        shutil.rmtree(cwd, ignore_errors=True)

    print()
    if FAILURES:
        print(f"✘ {len(FAILURES)} 项失败: {', '.join(FAILURES)}")
        return 1
    print("✔ pty-packages-panel 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
