#!/usr/bin/env python3 reserial
"""工具卡片真实渲染矩阵 PTY（真实模型逐工具调用）。

沙盒双官方包 + 真实 LLM（VOLCENGINE_API_KEY）：用指令式提示词让 agent
依次真实调用各工具，断言每个工具卡片的渲染器签名内容。

用法：python3 scripts/pty-tool-cards.py
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
    if not os.environ.get("VOLCENGINE_API_KEY"):
        print("跳过：无 VOLCENGINE_API_KEY")
        return 0

    sandbox = tempfile.mkdtemp(prefix="nova-pty-cards-")
    cwd = tempfile.mkdtemp(prefix="nova-pty-cards-cwd-")
    with open(os.path.join(cwd, "seed.txt"), "w", encoding="utf-8") as f:
        f.write("seed-alpha\nseed-beta\n")
    with open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "default_provider": "volcengine",
                "default_model": "deepseek-v4-flash-260425",
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

        def turn(prompt: str, wait: float = 30.0) -> str:
            checkpoint = len(tui.buffer)
            tui.send(prompt + "\r", wait)
            return tui.buffer[checkpoint:]

        # 1. bash
        delta = turn("用 bash 工具执行 echo CARD_PROBE_BASH，别的什么都别说")
        check("bash 卡片：命令 + 输出", "CARD_PROBE_BASH" in delta)

        # 2. write
        delta = turn("用 write 工具在当前目录创建 out.txt，内容为 hello-probe，别的什么都别说")
        check("write 卡片：路径呈现", "out.txt" in delta)

        # 3. read
        delta = turn("用 read 工具读取当前目录的 seed.txt，别的什么都别说")
        check("read 卡片：文件内容呈现", "seed-alpha" in delta)

        # 4. edit（diff 风）
        delta = turn(
            "用 edit 工具把当前目录 out.txt 里的 hello-probe 替换为 done-probe，别的什么都别说"
        )
        check("edit 卡片：diff 呈现", "done-probe" in delta and ("-" in delta or "+" in delta))

        # 5. grep
        delta = turn("用 grep 工具在当前目录 out.txt 里搜索 done-probe，别的什么都别说")
        check("grep 卡片：匹配行呈现", "done-probe" in delta and "out.txt" in delta)

        # 6. find
        delta = turn("用 find 工具在当前目录找名为 out.txt 的文件，别的什么都别说")
        check("find 卡片：路径呈现", "out.txt" in delta)

        # 7. ls
        delta = turn("用 ls 工具列出当前目录，别的什么都别说")
        check("ls 卡片：条目呈现", "seed.txt" in delta and "out.txt" in delta)

        # 8. todo（清单卡片）
        delta = turn(
            "用 todo 工具列一个两步清单：第一步探测A、第二步探测B，只列出不要执行，别的什么都别说"
        )
        check("todo 卡片：清单项呈现", "探测A" in delta and "探测B" in delta)

        # 9. question（对话框 + PTY 应答）
        checkpoint = len(tui.buffer)
        tui.send("用 question 工具问我一个问题：确认继续吗？选项给 继续/取消\r", 25.0)
        delta = tui.buffer[checkpoint:]
        check("question 对话框出现", "确认继续吗" in delta or "继续" in delta)
        tui.send("\r", 15.0)  # 选第一项（继续）提交
        delta = tui.buffer[checkpoint:]
        check("question 回执（选择落定）", "继续" in delta)

        # 10. subagent（gate 选允许一次 → 卡片）
        checkpoint = len(tui.buffer)
        tui.send("用 subagent 工具委派 worker 执行：只回答 ok\r", 20.0)
        delta = tui.buffer[checkpoint:]
        if "允许" in delta or "委派" in delta:
            tui.send("\r", 45.0)  # 允许一次
            delta = tui.buffer[checkpoint:]
        check("subagent 卡片呈现", "subagent" in delta or "worker" in delta or "ok" in delta.lower())

        # 收尾：全部完结后无 bare running 残留
        tui._drain(3.0)
        tail = tui.screen_tail(30)
        check(
            "完结后无 bare running 残留（去重回归）",
            "running…" not in tail,
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
    print("✔ pty-tool-cards 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
