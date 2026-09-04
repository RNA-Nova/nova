#!/usr/bin/env python3
"""auto-compaction 的 PTY 端到端加固（真实用户路径）。

不灌 1M：沙盒 NOVA_AGENT_DIR 里用 models.json 克隆一个小窗口模型
（context_window=45056，同 id 覆盖内置目录条目，api/base_url 继承内置默认，
真实 API 照跑）；首轮让 agent 用 read 工具读完三个大文件（工具结果完整落盘、
压缩可摘要），回复后上下文越过阈值触发 auto-compaction。

断言：压缩指示出现、无 continue_ RuntimeError 回归（length 尾剥离）、
压缩后会话可继续。

用法：python3 scripts/pty-auto-compaction.py（需 VOLCENGINE_API_KEY）
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

    sandbox = tempfile.mkdtemp(prefix="nova-pty-compact-")
    cwd = tempfile.mkdtemp(prefix="nova-pty-compact-cwd-")
    # 小窗口克隆：阈值 = 45056 - 16384 = 28672 token
    with open(os.path.join(sandbox, "models.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "providers": {
                    "volcengine": {
                        "models": [
                            {
                                "id": "deepseek-v4-flash-260425",
                                "context_window": 45056,
                            }
                        ]
                    }
                }
            },
            f,
        )
    with open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "default_provider": "volcengine",
                "default_model": "deepseek-v4-flash-260425",
                # 全新沙盒 cwd 会弹 Project Trust 对话框吞掉输入，直接放开
                "default_project_trust": "always",
                # CJK 文本真实 token 数是 chars/4 估算的约 4 倍——保持窗口
                # 覆盖整个分支会让 prepare_compaction 判"无内容可压"而静默
                # 跳过；收紧 keep_recent 让超窗内容分布在可摘要的早期消息里
                "compaction": {
                    "enabled": True,
                    "reserve_tokens": 16384,
                    "keep_recent_tokens": 8000,
                },
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
    # 已安装包注册表（editable 指针），避免会话启动时再走一遍安装
    shutil.copytree(
        os.path.expanduser("~/.nova/agent/packages"),
        os.path.join(sandbox, "packages"),
        symlinks=True,
    )

    # 三份各 1500 行（≈60KB）的资料文件——read 工具默认 2000 行截断线内一次读完
    for n in range(1, 4):
        with open(os.path.join(cwd, f"big{n}.txt"), "w", encoding="utf-8") as f:
            f.write(
                "\n".join(
                    f"[big{n} 行 {i}] 模块{i % 7}的部署要点与注意事项说明。"
                    for i in range(1500)
                )
            )

    os.environ["NOVA_AGENT_DIR"] = sandbox
    tui = smoke.TuiSession(cwd)
    try:
        tui._drain(12.0)
        check(
            "默认模型为小窗口克隆",
            "volcengine/deepseek-v4-flash-260425" in tui.buffer,
        )

        # 首轮：read 三个大文件（工具结果完整进上下文并持久化）→ 回复时
        # 上下文约 4.5 万 token > 28672 阈值 → 轮后应触发 auto-compaction
        checkpoint = len(tui.buffer)
        tui.send(
            "用 read 工具依次读取 big1.txt、big2.txt、big3.txt 的全部内容，"
            "读完后只回答两个字：收到\r",
            150.0,
        )
        delta = tui.buffer[checkpoint:]
        check("首轮助手回复", "收到" in delta)
        check(
            "auto-compaction 指示出现",
            "Auto-compacting" in delta or "Compacting context" in delta,
        )
        check(
            "无 continue_ RuntimeError 回归",
            "Cannot continue" not in delta and "RuntimeError" not in delta,
        )

        # 压缩后再对话一轮——续跑链路（重建上下文 + continue_）必须正常
        checkpoint = len(tui.buffer)
        tui.send("只回答一个字：好\r", 40.0)
        delta = tui.buffer[checkpoint:]
        check(
            "压缩后会话可继续",
            "好" in delta
            and "Cannot continue" not in delta
            and "RuntimeError" not in delta,
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
    print("✔ pty-auto-compaction 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
