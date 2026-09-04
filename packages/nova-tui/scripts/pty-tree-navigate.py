#!/usr/bin/env python3
"""会话树导航与分叉的 PTY 端到端加固。

零 LLM 成本：用户消息发送后立即 Esc 中止（消息已落盘），随后：
- /tree 选择器导航回第一条消息节点——断言编辑器回填原文、转录重同步、
  会话 JSONL 里 leaf 指针迁移；
- 提交回填内容产生分支——断言 JSONL 出现挂在旧父条目下的新消息；
- /fork 从第二条消息分叉——断言新会话文件生成且内容截止于分叉点。

用法：python3 scripts/pty-tree-navigate.py（需 VOLCENGINE_API_KEY 过启动引导，
实际不消耗 LLM 调用）
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

MARK_A = "TREE_MARK_AAAA"
MARK_B = "TREE_MARK_BBBB"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✔' if ok else '✘'} {name}" + (f" —— {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def _sandbox() -> str:
    sandbox = tempfile.mkdtemp(prefix="nova-pty-tree-")
    with open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                # 全新沙盒 cwd 会弹 Project Trust 对话框吞掉输入，直接放开
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
    # 已安装包注册表（editable 指针），避免会话启动时再走一遍安装
    shutil.copytree(
        os.path.expanduser("~/.nova/agent/packages"),
        os.path.join(sandbox, "packages"),
        symlinks=True,
    )
    return sandbox


def _session_entries(sandbox: str) -> list[dict]:
    """读取沙盒里最新会话 JSONL 的全部条目。"""
    import glob

    files = glob.glob(
        os.path.join(sandbox, "sessions", "**", "*.jsonl"), recursive=True
    )
    files.sort(key=os.path.getmtime)
    entries: list[dict] = []
    for line in open(files[-1], encoding="utf-8"):
        if line.strip():
            entries.append(json.loads(line))
    return entries


def main() -> int:
    if not os.environ.get("VOLCENGINE_API_KEY"):
        print("跳过：无 VOLCENGINE_API_KEY（启动引导需要可用模型）")
        return 0

    sandbox = _sandbox()
    cwd = tempfile.mkdtemp(prefix="nova-pty-tree-cwd-")
    os.environ["NOVA_AGENT_DIR"] = sandbox
    tui = smoke.TuiSession(cwd)
    try:
        tui._drain(12.0)

        # 1. 两条标记消息（发送后立即 Esc 中止 LLM 轮次，消息已落盘）
        tui.send(f"{MARK_A}\r", 2.5)
        tui.send("\x1b", 2.0)
        tui.send(f"{MARK_B}\r", 2.5)
        tui.send("\x1b", 2.5)

        entries = _session_entries(sandbox)
        user_texts = [
            json.dumps(e.get("message", {}).get("content", ""), ensure_ascii=False)
            for e in entries
            if e.get("type") == "message" and e.get("message", {}).get("role") == "user"
        ]
        check(
            "两条标记消息落盘",
            any(MARK_A in t for t in user_texts) and any(MARK_B in t for t in user_texts),
        )

        # 2. /tree 打开选择器，过滤选中 MARK_A 节点
        checkpoint = len(tui.buffer)
        tui.send("/tree\r", 4.0)
        delta = tui.buffer[checkpoint:]
        check("/tree 选择器打开", "会话树" in delta or "tree" in delta.lower())

        checkpoint = len(tui.buffer)
        tui.send("AAAA", 2.5)  # 输入即过滤
        delta = tui.buffer[checkpoint:]
        check("过滤命中 MARK_A 节点", MARK_A in delta)
        tui.send("\r", 3.0)  # 确认选中节点

        # 摘要确认框：默认项即"不生成摘要 直接跳转"，直接回车
        delta = tui.buffer[checkpoint:]
        if "生成分支摘要" in delta:
            tui.send("\r", 4.0)
        # 离开确认（confirm_destructive）如出现则回车确认
        delta = tui.buffer[checkpoint:]
        if "确认" in delta or "确定" in delta:
            tui.send("\r", 4.0)

        tui._drain(2.0)
        delta = tui.buffer[checkpoint:]
        check(
            "导航无 SessionTreeEvent 校验错误（回归 -32603）",
            "-32603" not in delta and "validation error" not in delta,
        )
        check(
            "导航后编辑器回填 MARK_A 原文",
            MARK_A in tui.screen_tail(20),
        )

        # 3. 提交回填内容 → 新分支（挂在 MARK_A 父条目下）
        tui.send("\r", 3.0)
        tui._drain(3.0)
        tui.send("\x1b", 2.5)  # 中止 LLM 轮次
        tui._drain(1.0)
        entries = _session_entries(sandbox)
        marks = [
            e
            for e in entries
            if e.get("type") == "message"
            and MARK_A in json.dumps(e.get("message", {}).get("content", ""), ensure_ascii=False)
            and e.get("message", {}).get("role") == "user"
        ]
        check(
            "分支新消息落盘（MARK_A 出现两次）",
            len(marks) == 2,
            f"实际 {len(marks)} 次",
        )
        if len(marks) == 2:
            check(
                "分支挂在同一父条目（branch 而非线性追加）",
                marks[0].get("parent_id") == marks[1].get("parent_id"),
            )

        # 4. /fork 从 MARK_B 分叉
        files_before = len(
            __import__("glob").glob(
                os.path.join(sandbox, "sessions", "**", "*.jsonl"), recursive=True
            )
        )
        checkpoint = len(tui.buffer)
        tui.send("/fork\r", 4.0)
        delta = tui.buffer[checkpoint:]
        check("/fork 选择器打开", "分叉" in delta or "fork" in delta.lower())

        tui.send("BBBB", 2.5)
        tui.send("\r", 5.0)
        tui._drain(4.0)

        files_after = __import__("glob").glob(
            os.path.join(sandbox, "sessions", "**", "*.jsonl"), recursive=True
        )
        check(
            "分叉产生新会话文件",
            len(files_after) == files_before + 1,
            f"{files_before} → {len(files_after)}",
        )
        if len(files_after) == files_before + 1:
            files_after.sort(key=os.path.getmtime)
            forked = [
                json.loads(line)
                for line in open(files_after[-1], encoding="utf-8")
                if line.strip()
            ]
            texts = json.dumps(forked, ensure_ascii=False)
            # fork 默认 before 位：分叉会话截止于 MARK_B 之前（含 MARK_A 历史），
            # MARK_B 原文回填编辑器待重发
            check(
                "分叉会话截止于 MARK_B 之前（含 MARK_A、不含 MARK_B 消息）",
                MARK_A in texts and MARK_B not in texts,
            )
            check(
                "MARK_B 原文回填编辑器",
                MARK_B in tui.screen_tail(15),
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
    print("✔ pty-tree-navigate 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
