#!/usr/bin/env python3
"""最小复现：远程后端下 !ls 的真实行为。"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from importlib import import_module

_mod = import_module("tui-hot-switch-e2e".replace("-", "_")) if False else None

# 复用 tui-hot-switch-e2e 的 TuiPty（同目录脚本，直接当模块加载）
import importlib.util

spec = importlib.util.spec_from_file_location(
    "hot_switch",
    os.path.join(os.path.dirname(__file__), "tui-hot-switch-e2e.py"),
)
hot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hot)


def main() -> int:
    tui = hot.TuiPty()
    try:
        print("== 启动并就绪")
        tui.wait_for(r"! bash", 90, "界面就绪")
        tui.wait_for(r"coding_agent · \S", 60, "会话与模型就绪")

        print("== 切远程")
        tui.send_line("/executor remote ssh://liujinming@180.184.33.245")
        switched = tui.wait_for("执行后端已切换", 120, "切换回执")

        print("== !touch a.txt b.txt && !ls && !ls -la（远程后端）")
        tui.send_line("!touch a.txt b.txt")
        tui.pump(8)
        tui.send_line("!ls")
        tui.pump(10)
        tui.send_line("!ls -la")
        tui.pump(10)
        tail = tui.buf[switched:]
        print("----- 切换后的屏幕内容 -----")
        print(tail[-2500:])
        return 0
    finally:
        tui.kill()


if __name__ == "__main__":
    sys.exit(main())
