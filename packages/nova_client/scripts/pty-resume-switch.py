#!/usr/bin/env python3
"""一次性验证：/resume 真实切换会话（transcript 全量重同步）。

流程：命名当前会话 + 写入唯一标记消息 → /new 新建 → /resume 选回旧会话
→ 断言切换后的**增量输出**里出现标记与旧会话名（重绘即证明 transcript
已替换；扫描增量而非累积 buffer，避免启动期旧帧误判）。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

smoke = import_module("pty-smoke")

MARKER = "RESUME_MARKER_AAA"
NAME = "pty-resume-target"


def main() -> int:
    tui = smoke.TuiSession("/tmp")
    try:
        tui._drain(8.0)  # 启动

        # 1. 命名 + 写入标记消息（Esc 立即中止 LLM 轮次，用户消息已落盘）
        tui.send(f"/name {NAME}\r", 4.0)
        tui.send(f"{MARKER}\r", 3.0)
        tui.send("\x1b", 2.0)

        # 2. /new 新建（非空会话可能弹确认框——出现则回车选"是"）
        tui.send("/new\r", 3.0)
        delta = tui.buffer
        if re.search(r"确认|确定|新建会话", delta):
            tui.send("\r", 4.0)
        checkpoint = len(tui.buffer)

        # 3. /resume 打开选择器
        tui.send("/resume\r", 4.0)
        delta = tui.buffer[checkpoint:]
        if not re.search(r"恢复|会话|Resume", delta):
            print("FAIL: /resume 选择器未出现")
            print(delta[-2000:])
            return 1
        if NAME not in delta:
            print("FAIL: 选择器列表里没有刚命名的旧会话")
            print(delta[-2000:])
            return 1

        # 4. 选第一个（按修改时间倒序，刚离开的命名会话在最上）并切换；
        #    confirm_destructive 会再弹一次离开确认（默认 Yes）
        checkpoint = len(tui.buffer)
        tui.send("\r", 3.0)
        delta = tui.buffer[checkpoint:]
        if re.search(r"切换会话|继续", delta):
            checkpoint = len(tui.buffer)
            tui.send("\r", 6.0)
            delta = tui.buffer[checkpoint:]

        ok_marker = MARKER in delta
        ok_name = NAME in delta
        print(f"marker 回显: {ok_marker}, 会话名回显: {ok_name}")
        if not (ok_marker and ok_name):
            print("FAIL: 切换后 transcript 未恢复旧会话内容")
            print(delta[-3000:])
            return 1
        print("PASS: /resume 真实切换成功")
        return 0
    finally:
        tui.close()


if __name__ == "__main__":
    sys.exit(main())
