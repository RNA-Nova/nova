#!/usr/bin/env python3
"""打包产物 PTY 冒烟（真实会话 + 内建 nova-base 首启落地验证）。

与 pty-smoke.py（开发态）的差异：spawn 的是**发布归档解出的 nova 二进制**，
后端发现链走同目录 runtime/nova-server（冻结 PyInstaller 产物），且沙盒
**不预装任何包**——nova-base 必须由二进制内建通道（sys._MEIPASS/bundles/
nova_base → <agentDir>/builtin/nova_base）首启落地。

沙盒：
- HOME=<临时目录>（前端域 ~/.nova/agent/frontend/tui 随 homedir 走，一并隔离）
- NOVA_AGENT_DIR=<临时目录>（后端状态根：settings/packages/sessions/builtin）
- settings.json 只写 trust 放开 + 默认模型——零 packages 条目
- 显式清掉 NOVA_BACKEND / NOVA_PYTHON（保证命中随行 runtime/，而非开发态回退）

断言：启动到模型 footer（含版本号）→ 内建 nova-base 落盘 + 登记 → 真实 API
一轮对话 → /help slash 命令清单（session_commands 来自落地包）→ /tools
复选面板（dialog:tools——落地包前端经 jiti 加载）→ /changelog 读随行
CHANGELOG.md → /quit 干净退出。

用法：python3 pty-binary-smoke.py <归档解出的 nova 路径>
（或 NOVA_BINARY 环境变量；需 VOLCENGINE_API_KEY）
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time

BINARY = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("NOVA_BINARY", "")
if not BINARY or not os.path.isfile(BINARY):
    print("用法：python3 pty-binary-smoke.py <归档解出的 nova 路径>（或设 NOVA_BINARY）")
    sys.exit(2)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✔' if ok else '✘'} {name}" + (f" —— {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def strip_ansi(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    return text.replace("\r", "\n")


class BinaryTuiSession:
    """一个 pty 里的打包产物 nova 进程（后端 = 同目录 runtime/nova-server）。"""

    def __init__(self, cwd: str, env_extra: dict[str, str]) -> None:
        self.master, slave = pty.openpty()
        # 显式窗口尺寸——无终端继承时 openpty 是 0x0
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        env = dict(
            os.environ,
            TERM="xterm-256color",
            **env_extra,
        )
        # 打包形态验收：必须走二进制旁 runtime/ 的发现支路——把开发态旋钮全摘掉
        env.pop("NOVA_BACKEND", None)
        env.pop("NOVA_PYTHON", None)
        self.proc = subprocess.Popen(
            [BINARY],
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

    def send(self, keys: str, wait: float = 3.0) -> None:
        os.write(self.master, keys.encode())
        self._drain(wait)

    def wait_for(self, pattern: str, timeout: float) -> str:
        """轮询等待 buffer 出现 pattern（全量 buffer——一次性命令的输出留在
        转录里，全量查比增量查抗时序抖动）；超时返回当下 buffer。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if re.search(pattern, self.buffer):
                break
            self._drain(0.5)
        return self.buffer

    def close(self) -> None:
        try:
            os.write(self.master, b"\x03\x03")
            self._drain(1.0)
        except OSError:
            pass
        try:
            self.proc.kill()
        except OSError:
            pass
        os.close(self.master)


def main() -> int:
    if not os.environ.get("VOLCENGINE_API_KEY"):
        print("跳过：无 VOLCENGINE_API_KEY")
        return 1

    home = tempfile.mkdtemp(prefix="nova-pkg-home-")
    agent_dir = os.path.join(home, "nova-agent")
    os.makedirs(agent_dir)
    cwd = tempfile.mkdtemp(prefix="nova-pkg-cwd-")
    with open(os.path.join(agent_dir, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "default_provider": "volcengine",
                "default_model": "deepseek-v4-flash-260425",
                "default_project_trust": "always",
                # 零 packages 条目——nova-base 必须由内建通道自行落地登记
            },
            f,
        )

    env_extra = {"HOME": home, "NOVA_AGENT_DIR": agent_dir}
    tui = BinaryTuiSession(cwd, env_extra)
    started = time.time()
    try:
        # —— 启动（轮询等 footer——冻结后端首启含内建落地，比开发态慢）——
        boot = tui.wait_for("deepseek-v4-flash-260425", 90.0)
        startup_s = time.time() - started
        check(
            f"启动到模型 footer（{startup_s:.1f}s）",
            "deepseek-v4-flash-260425" in boot,
            boot[-300:] if "deepseek" not in boot else "",
        )
        check(
            "欢迎区带版本号（__NOVA_VERSION__ 注入生效）",
            re.search(r"nova\s+v\d+\.\d+\.\d+", boot) is not None,
            boot[:400] if not re.search(r"nova\s+v\d+\.\d+\.\d+", boot) else "",
        )
        check(
            "启动期无后端崩溃/渲染器加载失败",
            not re.search(r"Traceback|渲染器加载失败|Cannot find module|Error code:", boot),
            "命中: "
            + ",".join(
                re.findall(r"渲染器加载失败[^\n]*|Cannot find module[^\n]*|Traceback", boot)
            )[:400],
        )

        # —— 内建 nova-base 首启落地（文件层 + settings 登记）——
        builtin_dir = os.path.join(agent_dir, "builtin", "nova_base")
        check(
            "内建 nova-base 落盘 <agentDir>/builtin/nova_base",
            os.path.isfile(os.path.join(builtin_dir, "pyproject.toml"))
            and os.path.isdir(os.path.join(builtin_dir, "backend", "extensions"))
            and os.path.isdir(os.path.join(builtin_dir, "frontend", "tui")),
        )
        with open(os.path.join(agent_dir, "settings.json"), encoding="utf-8") as f:
            settings_text = f.read()
        check(
            "内建 nova-base 登记进 settings 包清单",
            "builtin" in settings_text and "nova_base" in settings_text,
            settings_text[:300] if "nova_base" not in settings_text else "",
        )

        # —— L1：真实 API 一轮对话 ——
        tui.send("只回答两个字：收到\r", 1.0)
        screen = tui.wait_for("收到", 120.0)
        check("L1 真实 API 回复渲染", "收到" in screen, screen[-300:] if "收到" not in screen else "")
        check(
            "L1 无后端崩溃",
            not re.search(r"Traceback|Error code: |RuntimeError", screen),
        )

        # —— slash 命令可用（session_commands 来自内建落地包）——
        tui.send("/help\r", 1.0)
        screen = tui.wait_for(r"/changelog", 20.0)
        check(
            "/help 命令清单渲染（内建包扩展上线）",
            re.search(r"/(model|tools|changelog|session)", screen) is not None,
            screen[-300:] if not re.search(r"/(model|tools|changelog|session)", screen) else "",
        )
        tui.send("\x1b", 2.0)  # /help 是浮层（esc 关闭）——不关会吃掉后续按键

        # —— /tools 包侧复选面板（落地包前端 dialogs/tools.ts 经 jiti 加载注册；
        #     首载 jiti 编译比开发态慢，轮询等面板）——
        tui.send("/tools\r", 1.0)
        screen = tui.wait_for(r"\[[x ]\] ", 30.0)
        check(
            "/tools dialog:tools 复选面板（内建包前端加载）",
            re.search(r"\[[x ]\] ", screen) is not None and "工具开关" in screen,
            repr(screen[-300:]) if not re.search(r"\[[x ]\] ", screen) else "",
        )
        tui.send("\x1b", 2.0)  # 关面板

        # —— /changelog 读随行 CHANGELOG.md（资产随行验证）——
        tui.send("/changelog\r", 1.0)
        screen = tui.wait_for(r"\[0\.1\.0\]|Unreleased", 20.0)
        check(
            "/changelog 渲染随行 CHANGELOG.md",
            re.search(r"\[0\.1\.0\]|Unreleased", screen) is not None,
            repr(screen[-200:]) if not re.search(r"\[0\.1\.0\]|Unreleased", screen) else "",
        )

        # —— 干净退出 ——
        tui.send("/quit\r", 3.0)
        try:
            rc = tui.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            rc = None
        check("/quit 干净退出", rc == 0, f"rc={rc}" if rc != 0 else "")
    finally:
        tui.close()

    print()
    if FAILURES:
        print(f"✘ 失败 {len(FAILURES)} 项: {', '.join(FAILURES)}")
        print(f"（沙盒保留排查：HOME={home} NOVA_AGENT_DIR={agent_dir} cwd={cwd}）")
        return 1
    print("✔ 打包产物 PTY 冒烟全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
