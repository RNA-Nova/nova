#!/usr/bin/env python3
"""pty-user-bash-card：user_tools/bash 条目卡片的真实 TTY 复验。

断言两件事：
1. 启动期**没有**渲染器加载诊断（``渲染器加载失败`` / ``默认导出不是渲染函数``）——
   bash-execution 卡片归 lib/ 组织域（index.ts 编程式注册 entry:bashExecution），
   不得再被发现域当工具渲染器加载；
2. ``!echo`` 之后卡片渲染：``$ echo ...`` 命令头 + 输出文本都在屏上。

用法：python3 scripts/pty-user-bash-card.py [--cwd DIR]
"""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import subprocess
import sys
import time

NOVA_REPO = os.environ.get("NOVA_REPO", "/Users/liujinming/agent/nova-backup-20260824")
PYTHON = os.environ.get("NOVA_PYTHON", f"{NOVA_REPO}/.pixi/envs/dev/bin/python")
MAIN_JS = f"{NOVA_REPO}/packages/nova-tui/dist/modes/tui/main.js"
NODE = os.path.expanduser("~/.pixi/bin/node")

READY_TIMEOUT = 25.0
STEP_WAIT = 4.0

READY_RE = re.compile(r"coding_agent · \S")
DIAG_RE = re.compile(r"渲染器加载失败|默认导出不是渲染函数")


def strip_ansi(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    return text.replace("\r", "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default="/tmp")
    args = parser.parse_args()

    master, slave = pty.openpty()
    env = dict(os.environ, NOVA_PYTHON=PYTHON)
    proc = subprocess.Popen(
        [NODE, MAIN_JS],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=args.cwd,
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

    failures: list[str] = []
    try:
        # 就绪门：等到状态条出现（coding_agent · <model>），期间诊断若存在会落进 buffer
        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline and not READY_RE.search(buffer):
            drain(0.5)
        if not READY_RE.search(buffer):
            failures.append("启动超时：未见状态条")
        if DIAG_RE.search(buffer):
            failures.append("启动期出现渲染器加载诊断")
        else:
            print("✔ 启动期无渲染器加载诊断")

        # user bash 卡片
        before = len(buffer)
        os.write(master, "!echo pty-card-ok\r".encode())
        drain(STEP_WAIT)
        delta = buffer[before:]
        if re.search(r"\$\s*echo pty-card-ok", delta) and "pty-card-ok" in delta:
            print("✔ bashExecution 卡片渲染（$ 命令头 + 输出）")
        else:
            failures.append("卡片渲染缺失：未见 $ echo pty-card-ok 头或输出")
        if DIAG_RE.search(delta):
            failures.append("执行期出现渲染器加载诊断")

        # 慢命令时序：start 事件让 `$ command` 头先于输出渲染
        # （修复前：command 缺席至定稿，输出块先出现——nvidia-smi 类慢命令可感知）
        before = len(buffer)
        os.write(master, "!sleep 1 && echo slow-ok\r".encode())
        header_re = re.compile(r"\$\s*sleep 1 && echo slow-ok")
        output_re = re.compile(r"(?m)^\s*slow-ok\s*$")  # 整行匹配——避开命令头里同名片段
        header_at: float | None = None
        output_at: float | None = None
        deadline = time.time() + 6.0
        while time.time() < deadline and (header_at is None or output_at is None):
            drain(0.1)
            delta = buffer[before:]
            if header_at is None and header_re.search(delta):
                header_at = time.time()
            if output_at is None and output_re.search(delta):
                output_at = time.time()
        if header_at is None or output_at is None:
            failures.append(f"慢命令渲染缺失：header_at={header_at} output_at={output_at}")
        elif header_at < output_at:
            print(f"✔ 慢命令时序：$ 头先于输出 {output_at - header_at:.1f}s")
        else:
            failures.append("慢命令时序错误：输出先于 $ 命令头出现")
    finally:
        try:
            os.write(master, b"\x03\x03")
            drain(1.5)
        except OSError:
            pass
        try:
            proc.kill()
        except OSError:
            pass
        os.close(master)

    for f in failures:
        print(f"✖ {f}")
    print("\n通过" if not failures else "\n失败")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
