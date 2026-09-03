#!/usr/bin/env python3
"""role_boundary open/strict 视野切换的 PTY 验证。

路径：/agent reviewer（yaml 5 工具，池 10）→ /tools 面板——
open 应见全池（10，含 write/edit/subagent/todo/question 未勾），
strict 应只见 yaml 名单（5）。再经 /settings 切换角色边界验证联动。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

smoke = import_module("pty-smoke")

REVIEWER_FIVE = {"read", "grep", "find", "ls", "bash"}
POOL_EXTRA = {"write", "edit", "subagent", "todo", "question"}

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✔' if ok else '✘'} {name}" + (f" —— {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def visible_tools(buffer: str) -> set:
    """从屏幕文本抓可见工具名（全集范围内匹配）。"""
    universe = REVIEWER_FIVE | POOL_EXTRA
    return {name for name in universe if re.search(rf"\b{name}\b", buffer)}


def main() -> int:
    tui = smoke.TuiSession("/tmp")
    try:
        tui._drain(8.0)
        tui.send("\x1b", 1.0)  # 首次配置引导（无默认模型时弹出）会让路吞输入——先关掉

        # ---- 0. 切到 reviewer（open 默认）----
        checkpoint = len(tui.buffer)
        tui.send("/agent reviewer\r", 6.0)
        delta = tui.buffer[checkpoint:]
        check("/agent reviewer 切换反馈", "reviewer" in delta)
        check("footer 显示 reviewer", "reviewer" in tui.buffer[-300:])

        # ---- 1. open 态：/tools 面板见全池 ----
        checkpoint = len(tui.buffer)
        tui.send("/tools\r", 4.0)
        delta = tui.buffer[checkpoint:]
        seen = visible_tools(delta)
        print(f"  [观察] open 态面板可见: {sorted(seen)}")
        check("open：池外工具可见（write/edit/subagent/todo/question）",
              POOL_EXTRA <= seen)
        # 激活集应为 reviewer 五件（change_agent 重置——不携带 coding_agent 全量）
        check("open：激活的是 reviewer 五件（bash 勾选、write 未勾）",
              re.search(r"\[x\]\s*bash", delta) is not None
              and re.search(r"\[ \]\s*write", delta) is not None)
        tui.send("\x1b", 2.0)

        # ---- 2. /settings 切到 strict ----
        tui.send("/settings\r", 3.5)
        tui.send("角色", 2.0)  # 搜索过滤到角色边界项
        tui.send("\r", 2.0)    # 进二级值选择
        tui.send("strict", 1.5)  # 搜索过滤（顺序无关）
        tui.send("\r", 2.5)    # 选中 strict
        tui.send("\x1b", 1.5)
        tui.send("\x1b", 1.5)

        # ---- 3. strict 态：/tools 只见 yaml 五件 ----
        checkpoint = len(tui.buffer)
        tui.send("/tools\r", 4.0)
        delta = tui.buffer[checkpoint:]
        seen = visible_tools(delta)
        print(f"  [观察] strict 态面板可见: {sorted(seen)}")
        check("strict：只见 reviewer 五件", seen == REVIEWER_FIVE,
              f"实际: {sorted(seen)}")
        check("strict：池外工具不可见", not (seen & POOL_EXTRA))
        tui.send("\x1b", 2.0)

        # ---- 4. 切回 coding_agent（strict 下 yaml=10=池）----
        tui.send("/agent coding_agent\r", 6.0)
        checkpoint = len(tui.buffer)
        tui.send("/tools\r", 4.0)
        delta = tui.buffer[checkpoint:]
        seen = visible_tools(delta)
        check("strict + coding_agent：十件全在（yaml 即全池）",
              seen == REVIEWER_FIVE | POOL_EXTRA, f"实际: {sorted(seen)}")
        tui.send("\x1b", 2.0)

        # ---- 5. 切回 open（还原用户环境）----
        tui.send("/settings\r", 3.5)
        tui.send("角色", 2.0)
        tui.send("\r", 2.0)
        tui.send("open", 1.5)  # 搜索过滤（顺序无关）
        tui.send("\r", 2.5)
        tui.send("\x1b", 1.5)
        tui.send("\x1b", 1.5)
        checkpoint = len(tui.buffer)
        tui.send("/tools\r", 4.0)
        delta = tui.buffer[checkpoint:]
        check("还原 open：面板仍十件", visible_tools(delta) == REVIEWER_FIVE | POOL_EXTRA)
        tui.send("\x1b", 2.0)

        print()
        if FAILURES:
            print(f"FAIL {len(FAILURES)} 项:")
            for f in FAILURES:
                print("  -", f)
            return 1
        print("全部通过")
        return 0
    finally:
        tui.close()


if __name__ == "__main__":
    sys.exit(main())
