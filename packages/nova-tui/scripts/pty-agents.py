#!/usr/bin/env python3
"""pty-agents：/agent 切换链路的真实 TTY 验证。

/agent 系列在真实终端里逐条断言：选择器开合、直切反馈、footer 角色行、
/tools 面板随角色换集。模型段用"你是谁"探针验证系统提示词真实落到模型
（人格文本即提示词）。（/persona 运行期旋钮已拆——人格文本的唯一装配点
是 agent 组合声明。）

探针读法：编辑器内容不可靠回显时经 ctrl+g 草稿转存读回；渲染器后台加载
经 wait_for 轮询承接。

用法：python3 scripts/pty-agents.py [--cwd DIR] [--filter REGEX] [--list]
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import time
from typing import Optional

# 共享 pty-keymap 的 TuiSession 驱动（同目录 import——第三份副本不再抄）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "pty_keymap", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pty-keymap.py")
)
_pk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pk)
TuiSession = _pk.TuiSession

READY_RE = _pk.READY_RE

STEP_WAIT = 3.0


def _last_role_line(tui) -> str:
    """footer/welcome 的角色行（agent · model · thinking 形态）。"""
    matches = re.findall(r"(?:coding_agent|scout|planner|reviewer|worker|base_agent)[^\n]*", tui.buffer)
    return matches[-1] if matches else ""


def case_agent_selector(tui) -> Optional[str]:
    """/agent 无参：选择器开合，五角色在列。"""
    before = len(tui.buffer)
    tui.send("/agent\r", STEP_WAIT)
    delta = tui.buffer[before:]
    if not re.search(r"scout|worker", delta):
        return "/agent 选择器未见 scout/worker"
    tui.send("\x1b", 2.0)  # 关
    return None


def case_agent_switch_scout(tui) -> Optional[str]:
    """/agent scout 直切：反馈 + footer 角色行换 scout。"""
    before = len(tui.buffer)
    tui.send("/agent scout\r", STEP_WAIT + 2)
    delta = tui.buffer[before:]
    if "已切换角色" not in delta or "scout" not in delta:
        return "/agent scout 无切换反馈"
    role = _last_role_line(tui)
    if not role.startswith("scout"):
        return f"footer 角色行未换 scout（读到 {role[:60]!r}）"
    return None


def case_tools_panel_scout_set(tui) -> Optional[str]:
    """/tools：切到 scout 后面板反映只读工具集（write/edit 不在激活列）。"""
    before = len(tui.buffer)
    tui.send("/tools\r", STEP_WAIT + 1)
    delta = tui.buffer[before:]
    if "工具开关" not in delta and "激活" not in delta:
        return "/tools 面板未开"
    # scout 集 = read/grep/find/ls/bash——write/edit 应非激活
    # 面板行形态：[x]/[ ] 复选。write/edit 行不得为激活态。
    for tool in ("write", "edit"):
        m = re.search(rf"\[(x|✓| )\]\s*{tool}\b", delta)
        if m and m.group(1) in ("x", "✓"):
            return f"scout 激活集里 {tool} 仍激活"
    tui.send("\x1b", 2.0)  # 关面板
    return None
def case_model_identity_probe(tui) -> Optional[str]:
    """模型探针（真模型段）：当前 worker 角色下问"你是谁"——回答应反映
    worker 人格（worker/执行/implement 字样），证明系统提示词真实落到模型。"""
    before = len(tui.buffer)
    tui.send("你是谁？用一句话回答，必须说出你的角色名\r", 2.0)
    if not tui.wait_for(r"worker|执行|implement|工人", 60.0):
        return "模型回复未现 worker 人格线索（60s 超时）"
    delta = tui.buffer[before:]
    if not re.search(r"worker|执行者|执行代理|implement", delta, re.I):
        return "模型回复未见 worker 人格内容"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default="/tmp")
    parser.add_argument("--filter", default="")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    cases = [
        ("agent 选择器开合", case_agent_selector),
        ("agent 直切 scout + footer 换名", case_agent_switch_scout),
        ("tools 面板随角色换集", case_tools_panel_scout_set),
        ("模型探针：worker 人格落地", case_model_identity_probe),
        ("切回 coding_agent", None),  # 占位——主流程串行处理
    ]
    if args.filter:
        pattern = re.compile(args.filter)
        cases = [c for c in cases if pattern.search(c[0])]
    if args.list:
        for name, *_ in cases:
            print(name)
        return 0

    has_model = bool(os.environ.get("VOLCENGINE_API_KEY"))
    failures: list[tuple[str, str]] = []

    tui = TuiSession(args.cwd)
    try:
        if not tui.wait_ready():
            print("✖ 启动超时")
            print(tui.buffer[-800:])
            return 1
        tui.send("\x1b", 1.0)
        for name, fn in cases:
            if fn is None:
                continue
            if fn is case_model_identity_probe and not has_model:
                print(f"- {name}（跳过：无 VOLCENGINE_API_KEY）")
                continue
            problem = fn(tui)
            if problem is None:
                print(f"✔ {name}")
            else:
                failures.append((name, problem))
                print(f"✖ {name}  {problem}")
        # 收尾：切回 coding_agent（后续会话不被 worker 形态污染）
        tui.send("/agent coding_agent\r", STEP_WAIT + 2)
    finally:
        tui.close()

    total = len([c for c in cases if c[1] is not None and not (c[1] is case_model_identity_probe and not has_model)])
    print(f"\n{total - len(failures)}/{total} 通过")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
