#!/usr/bin/env python3
"""OSC 8 可点链接的 PTY 验证（真实 LLM 轮次）。

TERM_PROGRAM=vscode 显式开 OSC 8 能力（pi-tui 探测）。断言：
1. assistant markdown 链接 [text](url) → OSC 8 目标含 url；
2. 反向守卫：read/grep 工具卡片不发射 file://（pi 式克制——可点性归 assistant 汇总层）。
"""

import os, re, pty, subprocess, select, time, sys

NOVA_REPO = os.environ.get("NOVA_REPO", "/Users/liujinming/agent/nova-backup-20260824")
PYTHON = os.environ.get("NOVA_PYTHON", f"{NOVA_REPO}/.pixi/envs/dev/bin/python")
MAIN = f"{NOVA_REPO}/packages/nova-tui/dist/modes/tui/main.js"

master, slave = pty.openpty()
env = dict(os.environ, NOVA_PYTHON=PYTHON, TERM_PROGRAM="vscode")
proc = subprocess.Popen(["node", MAIN], stdin=slave, stdout=slave, stderr=slave,
                        cwd="/tmp", env=env, close_fds=True)
os.close(slave)
raw = b""

def drain(t):
    global raw
    end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.2)
        if not r: continue
        try: chunk = os.read(master, 65536)
        except OSError: break
        if not chunk: break
        raw += chunk

def send(keys, wait=2.0):
    global raw
    os.write(master, keys.encode()); drain(wait)

fails = []
try:
    drain(8)
    send("\x1b", 1)  # 首次配置引导（无默认模型时弹出）会让路吞输入——先关掉
    send("输出一个 markdown 链接 [示例](https://example.com)，然后闭嘴\r", 45)
    send("\x1b", 2)
    text = raw.decode("utf-8", "replace")
    if not any("example.com" in t for t in re.findall(r"\x1b\]8;;([^\x1b\x07]+)", text)):
        fails.append("markdown 链接未发射 OSC 8")

    raw = b""
    send("用 read 读 /tmp/hello_world.py，然后用 grep 在 /tmp 搜 def\r", 60)
    send("\x1b", 2)
    text = raw.decode("utf-8", "replace")
    targets = re.findall(r"\x1b\]8;;file://([^\x1b\x07]+)", text)
    # 设计定案（与 pi 同哲学）：工具卡片是过程证据，刻意不链接——
    # 可点性归 assistant 汇总层（markdown 链接）。file:// 出现即回归
    if targets:
        fails.append(f"工具卡片不应发射 file:// 链接（视觉噪音）：{sorted(set(targets))[:3]}")
finally:
    try: os.write(master, b"\x03\x03"); drain(1.5)
    except OSError: pass
    proc.kill(); os.close(master)

if fails:
    print("FAIL:", *fails, sep="\n  - ")
    sys.exit(1)
print("PASS: markdown 链接可点 + 工具卡片保持纯文本")
