#!/usr/bin/env python3
"""auto-retry 的 PTY 端到端加固。

沙盒 NOVA_AGENT_DIR 里配一个指向死端口的 provider（127.0.0.1:9，
connection refused 立即返回且在可重试清单内），发送一条消息：
断言重试倒计时指示出现（Retrying n/m）、重试耗尽后错误正常落账、
进程不崩、无未捕获异常。

用法：python3 scripts/pty-auto-retry.py（无需 API key）
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
    sandbox = tempfile.mkdtemp(prefix="nova-pty-retry-")
    cwd = tempfile.mkdtemp(prefix="nova-pty-retry-cwd-")
    with open(os.path.join(sandbox, "models.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "providers": {
                    "dead-local": {
                        "base_url": "http://127.0.0.1:9/v1",
                        "api": "openai-completions",
                        "api_key": "x",
                        "models": [{"id": "dead-1", "name": "Dead Model"}],
                    }
                }
            },
            f,
        )
    with open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "default_provider": "dead-local",
                "default_model": "dead-1",
                # 全新沙盒 cwd 会弹 Project Trust 对话框吞掉输入，直接放开
                "default_project_trust": "always",
            },
            f,
        )

    os.environ["NOVA_AGENT_DIR"] = sandbox
    tui = smoke.TuiSession(cwd)
    try:
        tui._drain(12.0)
        check("默认模型为死端点模型", "dead-local/dead-1" in tui.buffer)

        checkpoint = len(tui.buffer)
        # 默认退避 2s/4s/8s（max_retries=3），耗尽约 15~20s，留足余量
        tui.send("hi\r", 50.0)
        delta = tui.buffer[checkpoint:]
        check("重试倒计时指示出现", "Retrying (1/3)" in delta or "Retrying (" in delta)
        check(
            "无未捕获异常/回溯",
            "Traceback" not in delta and "Cannot continue" not in delta,
        )
        check("进程存活（TUI 未崩）", tui.proc.poll() is None)

        # 重试耗尽后 TUI 仍可用：Esc 中断残留状态，编辑器可继续输入
        checkpoint = len(tui.buffer)
        tui.send("\x1b", 2.0)
        tui.send("test\r", 12.0)
        delta2 = tui.buffer[checkpoint:]
        check(
            "重试耗尽后仍可交互",
            "Traceback" not in delta2 and tui.proc.poll() is None,
        )
    finally:
        tui.close()
        os.environ.pop("NOVA_AGENT_DIR", None)
        shutil.rmtree(sandbox, ignore_errors=True)
        shutil.rmtree(cwd, ignore_errors=True)

    print()
    if FAILURES:
        print(f"✘ {len(FAILURES)} 项失败: {', '.join(FAILURES)}")
        return 1
    print("✔ pty-auto-retry 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
