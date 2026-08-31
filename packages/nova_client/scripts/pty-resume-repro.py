#!/usr/bin/env python3
"""pty-resume-repro：复现 resume 旧会话时的 AssistantView trim 崩溃。

流程：~ 目录起 TUI → /resume 打开会话选择器 → 回车选最近会话 → 观察 sync 后渲染。
"""

from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import sys
import time

NOVA_REPO = os.environ.get("NOVA_REPO", "/Users/liujinming/agent/nova")
PYTHON = os.environ.get("NOVA_PYTHON", f"{NOVA_REPO}/.pixi/envs/dev/bin/python")
MAIN_JS = f"{NOVA_REPO}/packages/nova-harness/frontend/dist/modes/tui/main.js"
NODE = os.path.expanduser("~/.pixi/bin/node")


def strip_ansi(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    return text.replace("\r", "\n")


def main() -> int:
    master, slave = pty.openpty()
    env = dict(os.environ, NOVA_PYTHON=PYTHON)
    proc = subprocess.Popen(
        [NODE, MAIN_JS],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd="/Users/liujinming",
        env=env,
        close_fds=True,
    )
    os.close(slave)
    buffer = ""

    def drain(timeout: float) -> None:
        nonlocal buffer
        deadline = time.time() + timeout
        while time.time() < deadline:
            r, _, _ = select.select([master], [], [], 0.2)
            if not r:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buffer += strip_ansi(chunk)

    def send(keys: str, wait: float = 3.0) -> None:
        os.write(master, keys.encode())
        drain(wait)

    try:
        print("== 启动 TUI（cwd=~）", flush=True)
        drain(12.0)

        print("== 输入 /resume", flush=True)
        send("/resume", wait=2.0)
        send("\r", wait=4.0)  # 打开选择器

        tail = buffer[-3000:]
        print("== 选择器出现后屏幕尾部 ===", flush=True)
        print(tail[-1200:], flush=True)

        print("== 回车选择最近会话", flush=True)
        send("\r", wait=3.0)  # 选中 → 可能弹 confirm_destructive 确认框

        if "切换会话" in buffer[-2000:] and "继续" in buffer[-2000:]:
            print("== 确认框出现，回车确认 Yes", flush=True)
            send("\r", wait=8.0)  # 确认 → switchSession + sync + 渲染
        else:
            drain(6.0)

        tail = buffer[-4000:]
        crashed = "trim" in tail or "TypeError" in tail
        print("== 切换后屏幕尾部 ===", flush=True)
        print(tail[-2000:], flush=True)
        print(f"\n== 结果: {'复现崩溃 ✗' if crashed else '未崩溃 ✓'}", flush=True)
        return 1 if crashed else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
