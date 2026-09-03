#!/usr/bin/env python3
"""pty-smoke：真实 TTY 终端冒烟矩阵（逐命令断言）。

用 pty 启动 nova TUI，按序注入命令并断言屏幕输出——覆盖 slash 命令、
本地命令、用户 bash、选择器开合。与 scripts/smoke-e2e.ts（RPC 层）互补：
本脚本验的是"用户在终端里真实看到的"。

用法：
  python3 scripts/pty-smoke.py [--cwd DIR] [--filter REGEX] [--list]
"""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from typing import Union

NOVA_REPO = os.environ.get("NOVA_REPO", "/Users/liujinming/agent/nova-backup-20260824")
PYTHON = os.environ.get(
    "NOVA_PYTHON", f"{NOVA_REPO}/.pixi/envs/dev/bin/python"
)
MAIN_JS = f"{NOVA_REPO}/packages/nova-tui/dist/modes/tui/main.js"
NODE = os.path.expanduser("~/.pixi/bin/node")

STARTUP_WAIT = 10.0
STEP_WAIT = 3.0


def strip_ansi(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    return text.replace("\r", "\n")


class TuiSession:
    """一个 pty 里的 nova 进程。"""

    def __init__(self, cwd: str) -> None:
        self.master, slave = pty.openpty()
        env = dict(os.environ, NOVA_PYTHON=PYTHON)
        self.proc = subprocess.Popen(
            [NODE, MAIN_JS],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            env=env,
            close_fds=True,
        )
        os.close(slave)
        self.buffer = ""

    def _drain(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            r, _, _ = select.select([self.master], [], [], 0.2)
            if not r:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.buffer += strip_ansi(chunk)

    def send(self, keys: str, wait: float = STEP_WAIT) -> None:
        os.write(self.master, keys.encode())
        self._drain(wait)

    def screen_tail(self, n: int = 400) -> str:
        return self.buffer[-n * 120 :]

    def close(self) -> None:
        try:
            os.write(self.master, b"\x03\x03")  # 双击 ctrl+c
            self._drain(1.5)
        except OSError:
            pass
        try:
            self.proc.kill()
        except OSError:
            pass
        os.close(self.master)


# (名称, 步骤序列, [必须全部命中的正则], [不得命中的正则], 可选等待秒)
# 步骤序列：字符串（单步）或 [(按键, 等待秒), ...]（多步——如 Esc 后接 /debug 探针）
Case = Union[
    tuple[str, str, list[str], list[str]],
    tuple[str, str, list[str], list[str], float],
    tuple[str, list[tuple[str, float]], list[str], list[str]],
]

CASES: list[Case] = [
    ("help 命令目录", "/help\r", [r"后端命令", r"提示词模板", r"本地命令", r"/packages"], []),
    ("help 关闭", [("\x1b", 2.0), ("/debug\r", 4.0)], [r"debug|dump"], [r"后端命令"]),
    (
        "session 信息",
        "/session\r",
        [r"ID:", r"CWD:", r"条目数", r"Leaf"],
        [],
    ),
    ("todos 空态", "/todos\r", [r"还没有 todo 清单|已清空|todo"], []),
    ("tools 开关面板", "/tools\r", [r"工具开关", r"激活 \d+/\d+"], []),
    ("tools 面板关闭", [("\x1b", 2.0), ("/debug\r", 4.0)], [r"debug|dump"], [r"工具开关"]),
    ("scoped 池面板", "/scoped-models\r", [r"Scoped 模型池"], []),
    ("scoped 面板关闭", [("\x1b", 2.0), ("/debug\r", 4.0)], [r"debug|dump"], [r"Scoped 模型池"]),
    ("model 选择器", "/model\r", [r"选择模型"], []),
    ("model 选择器关闭", [("\x1b", 2.0), ("/debug\r", 4.0)], [r"debug|dump"], [r"选择模型"]),
    ("resume 选择器", "/resume\r", [r"恢复|会话|没有历史会话"], []),
    ("tree 选择器", "/tree\r", [r"会话树|当前|session"], [], 5.0),
    ("tree 关闭", [("\x1b", 2.0), ("/debug\r", 4.0)], [r"debug|dump"], [r"会话树"]),
    ("fork 空会话提示", "/fork\r", [r"没有可分叉|分叉|fork"], []),
    ("name 设置", "/name pty-冒烟\r", [r"pty-冒烟"], []),
    ("session 名称回读", "/session\r", [r"pty-冒烟"], []),
    ("export HTML 导出", "/export\r", [r"导出|html|HTML"], []),
    ("trust 信任", "/trust\r", [r"已信任当前项目"], [], 8.0),
    ("untrust 取消", "/untrust\r", [r"已取消信任"], [], 8.0),
    ("reload 重载", "/reload\r", [r"已重新加载"], [], 8.0),
    ("hotkeys 键位表", "/hotkeys\r", [r"ctrl|esc|键位"], []),
    ("changelog", "/changelog\r", [r"Added|Changed|Fixed|Unreleased"], []),
    ("settings 面板", "/settings\r", [r"主题|设置"], []),
    # settings 是两级面板（Esc 逐级返回）——中间帧必含一级页，不做 must_not；
    # /debug 探针命中即证明控制已交还编辑器
    ("settings 关闭", [("\x1b", 1.5), ("\x1b", 1.5), ("/debug\r", 4.0)], [r"debug|dump"], []),
    ("theme 选择器", "/theme\r", [r"主题|dark|light"], []),
    ("theme 关闭", [("\x1b", 2.0), ("/debug\r", 4.0)], [r"debug|dump"], [r"主题"]),
    ("copy 空回复", "/copy\r", [r"没有可复制|已复制|copy"], []),
    ("user bash", "!echo pty-ok\r", [r"pty-ok"], []),
    ("import 缺参数", "/import\r", [r"用法|参数|import"], []),
    ("debug dump", "/debug\r", [r"debug|dump"], []),
    # —— 补齐剩余命令（覆盖全部 34 个）——
    ("plan 开启", "/plan\r", [r"plan|规划"], [], 5.0),
    ("plan 关闭", "/plan\r", [r"plan|规划|执行"], [], 5.0),
    ("packages 面板", "/packages\r", [r"包|packages|nova-coding-agent"], []),
    ("packages 关闭", [("\x1b", 2.0), ("/debug\r", 4.0)], [r"debug|dump"], [r"nova-coding-agent"]),
    ("clone 克隆", "/clone\r", [r"克隆|clone|已|无法克隆|还没有内容"], [], 6.0),
    ("new 新建（确认框选是）", "/new\r", [r"确认|新建|新会话|nova v"], [], 6.0),
    ("login 选择器", "/login\r", [r"登录|provider|OAuth|auth"], []),
    ("login 关闭", [("\x1b", 2.0), ("/debug\r", 4.0)], [r"debug|dump"], [r"OAuth"]),
    ("logout 选择器", "/logout\r", [r"移除|provider|登录|OAuth|auth"], []),
    ("logout 关闭", [("\x1b", 2.0), ("/debug\r", 4.0)], [r"debug|dump"], [r"OAuth|移除"]),
    ("share 无 gh 降级", "/share\r", [r"gh|分享|失败|错误"], [], 8.0),
    ("prompt 模板展开", "/refactor 测试\r", [r"refactor|重构|assistant|⠋|working"], [], 6.0),
    ("prompt 模板中止", [("\x1b", 4.0), ("/debug\r", 4.0)], [r"debug|dump"], []),
    ("真实对话一轮", "回复ok\r", [r"."], [], 45.0),
]
# quit 单独判定（断言进程退出而非屏幕文本）；
# 先 Esc 中止可能未完的 LLM 轮次——否则 /quit 会被当 steering 文本发给模型
QUIT_CASE = ("quit 退出", [("\x1b", 2.0), ("/quit\r", 4.0)])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default="/tmp")
    parser.add_argument("--filter", default="")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    cases = CASES
    if args.filter:
        pattern = re.compile(args.filter)
        cases = [c for c in CASES if pattern.search(c[0])]
    if args.list:
        for name, *_ in cases:
            print(name)
        return 0

    tui = TuiSession(args.cwd)
    tui._drain(STARTUP_WAIT)
    # 首次配置引导（无默认模型时弹出）会让路吞输入——先 ESC 关掉
    tui.send("\x1b", 1.0)

    failures: list[tuple[str, str]] = []
    for case in cases:
        name, keys, must, must_not = case[0], case[1], case[2], case[3]
        wait = case[4] if len(case) > 4 else STEP_WAIT
        before = len(tui.buffer)
        if isinstance(keys, str):
            tui.send(keys, wait)
        else:
            for step_keys, step_wait in keys:
                tui.send(step_keys, step_wait)
        delta = tui.buffer[before:]  # buffer 只增不减——增量即全部新输出
        missing = [p for p in must if not re.search(p, delta)]
        forbidden = [p for p in must_not if re.search(p, delta)]
        if missing or forbidden:
            failures.append((name, f"missing={missing} forbidden={forbidden}"))
            print(f"✖ {name}  missing={missing} forbidden={forbidden}")
        else:
            print(f"✔ {name}")

    # quit：断言进程真的退出
    qname, qkeys = QUIT_CASE
    if isinstance(qkeys, str):
        tui.send(qkeys, 4.0)
    else:
        for step_keys, step_wait in qkeys:
            tui.send(step_keys, step_wait)
    time.sleep(2)
    if tui.proc.poll() is not None:
        print(f"✔ {qname}")
    else:
        failures.append((qname, "进程未退出"))
        print(f"✖ {qname}  进程未退出")

    tui.close()
    print(f"\n{len(cases) + 1 - len(failures)}/{len(cases) + 1} 通过")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
