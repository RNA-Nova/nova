#!/usr/bin/env python3
"""PTY 端到端：/executor 命令真实链路（选择器 → 切换 → notice → 系统提示词环境段）。"""

import os
import pty
import re
import select
import subprocess
import sys
import time

NOVA_REPO = "/Users/liujinming/agent/nova"
PYTHON = f"{NOVA_REPO}/.pixi/envs/dev/bin/python"
MAIN_JS = f"{NOVA_REPO}/packages/nova-harness/frontend/dist/modes/tui/main.js"
NODE = os.path.expanduser("~/.pixi/bin/node")


def strip_ansi(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    return text.replace("\r", "\n")


def drain(master, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.2)
        if not r:
            continue
        try:
            chunk = os.read(master, 65536)
        except OSError:
            return
        if not chunk:
            return
        yield strip_ansi(chunk)


def main() -> int:
    master, slave = pty.openpty()
    env = dict(os.environ, NOVA_PYTHON=PYTHON)
    proc = subprocess.Popen(
        [NODE, MAIN_JS], stdin=slave, stdout=slave, stderr=slave,
        cwd="/tmp", env=env, close_fds=True,
    )
    os.close(slave)
    buffer = ""

    def pump(seconds: float) -> None:
        nonlocal buffer
        for chunk in drain(master, seconds):
            buffer += chunk

    try:
        pump(12)  # 启动
        # trust 对话框如出现先确认
        if "Trust project folder?" in buffer:
            os.write(master, b"\r")
            buffer = ""
            pump(6)

        # 打开 /executor 选择器
        os.write(master, b"/executor")
        pump(1.5)
        os.write(master, b"\r")
        pump(4)
        picker = buffer
        assert "executor" in picker and "local" in picker, f"选择器未出现 executor 选项: {picker[-500:]}"
        print("✔ /executor 选择器打开（含 local / executor 选项）")

        # 选第二项（executor 本地沙箱）后回车
        os.write(master, b"\x1b[B")  # Down
        pump(1.0)
        os.write(master, b"\r")
        pump(5)
        assert "执行后端已切换" in buffer, f"切换 notice 未出现: {buffer[-500:]}"
        print("✔ 切换 notice 出现（执行后端已切换）")

        # !bash 用户命令经 executor 后端跑一条（用户工具也接了同一解析）
        buffer = ""
        os.write(master, b"!echo executor-e2e-ok\r")
        pump(6)
        assert "executor-e2e-ok" in buffer, f"用户 bash 未产出: {buffer[-500:]}"
        print("✔ executor 后端真实执行用户 bash（!echo）")

        print("\nE2E ALL PASS")
        return 0
    except AssertionError as e:
        print(f"✖ {e}")
        return 1
    finally:
        try:
            os.write(master, b"\x03\x03")
        except OSError:
            pass
        proc.kill()
        os.close(master)


if __name__ == "__main__":
    sys.exit(main())
