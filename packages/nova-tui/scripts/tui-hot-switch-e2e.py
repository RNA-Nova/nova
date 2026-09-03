#!/usr/bin/env python3
"""真 PTY 热切换 e2e：驱动真实 nova TUI（node dist + pixi Python 后端）。

验证三件事（全部真实链路，无 mock）：
1. **热切换后端 + 远程 cwd**：/executor remote ssh://liujinming@180.184.33.245
   → 真实 SSH 供给（管理密钥已装，BatchMode 免密）→ !pwd && hostname
   输出远程会话工作区路径 + 远程主机名 dp；
2. **系统提示词注入**：问模型 environment 段——断言答案出现
   <environment_id>ssh://liujinming@180.184.33.245</environment_id>。
   该字符串只可能来自系统提示词环境段：slash 命令/notice 不进模型上下文，
   提问文本也不含完整标签（防屏幕回显碰撞）；
3. **热切回本地**：/executor local → !hostname 回本地主机名。

用法：python packages/nova-tui/scripts/tui-hot-switch-e2e.py
"""

import os
import platform
import pty
import re
import select
import signal
import subprocess
import sys
import time

CWD = "/Users/liujinming/agent/nova/tmp"
TUI_CMD = [
    "node",
    "/Users/liujinming/agent/nova/packages/nova-harness/frontend/dist/modes/tui/main.js",
]
LOG_PATH = "/tmp/nova-pty-e2e.log"

REMOTE_HOST = "liujinming@180.184.33.245"
REMOTE_HOSTNAME = "dp"
LOCAL_HOSTNAME = platform.node()

_ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b\[[0-9;?]*[A-Za-z]"  # CSI
    r"|\x1b[()][0-9A-B]"  # 字符集
    r"|\x1b[>=<]"  # 其他单字符
)


def _strip(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    text = _ANSI_RE.sub("", text).replace("\r", "")
    # TTY 行尾有 padding 空格——逐行 rstrip，行级断言（^hostname$）才可靠
    return "\n".join(line.rstrip() for line in text.split("\n"))


class TuiPty:
    def __init__(self) -> None:
        self.master, slave = pty.openpty()
        # 窗口宽度放宽到 200：长路径（会话工作区 ULID）渲染不受 80 列截断
        # （pi-tui 对超宽行抛异常的既有健壮性问题，宽终端是真实使用形态）
        import fcntl
        import struct
        import termios

        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
        env = dict(os.environ)
        env["NOVA_PYTHON"] = "/Users/liujinming/agent/nova/.pixi/envs/dev/bin/python"
        env.setdefault("TERM", "xterm-256color")
        self.proc = subprocess.Popen(
            TUI_CMD,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=CWD,
            env=env,
            preexec_fn=os.setsid,
            close_fds=True,
        )
        os.close(slave)
        os.set_blocking(self.master, False)
        self.buf = ""
        self.log = open(LOG_PATH, "w", encoding="utf-8")

    def pump(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            r, _, _ = select.select([self.master], [], [], 0.2)
            if not r:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            if not data:
                return
            text = _strip(data)
            self.buf += text
            self.log.write(text)
            self.log.flush()

    def wait_for(self, pattern: str, timeout: float, desc: str, after: int = 0) -> int:
        """等待屏幕上 after 偏移后出现 pattern（正则）；超时 dump 尾部并失败。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            match = re.search(pattern, self.buf[after:])
            if match:
                print(f"  ✓ [{desc}] 命中 {pattern!r}")
                return after + match.start()
            if self.proc.poll() is not None:
                self._fail(f"TUI 提前退出（code {self.proc.returncode}）", desc)
            self.pump(0.5)
        self._fail(f"超时（{timeout:.0f}s）", desc)
        return -1

    def _fail(self, why: str, desc: str) -> None:
        print(f"  ✗ [{desc}] {why}")
        print("----- 屏幕尾部（1000 字） -----")
        print(self.buf[-1000:])
        self.kill()
        sys.exit(1)

    def send_line(self, text: str) -> int:
        """输入一行并提交；返回发送前的屏幕偏移（供后续断言只看新输出）。

        直接 text + Enter：Esc 会清空无补全菜单时的输入行（! 命令曾被
        误清）；slash 命令的补全菜单在输入空格+参数后自行关闭。
        """
        offset = len(self.buf)
        os.write(self.master, text.encode())
        time.sleep(0.3)
        os.write(self.master, b"\r")
        return offset

    def kill(self) -> None:
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except Exception:
            pass
        time.sleep(1)
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        except Exception:
            pass


def main() -> int:
    tui = TuiPty()
    try:
        print("== 1. 启动 TUI，等待就绪（会话+模型就位才算就绪）")
        tui.wait_for(r"! bash", 90, "TUI 界面就绪")
        tui.wait_for(r"coding_agent · \S", 60, "会话与模型就绪")

        print("== 2. /executor remote（真实 SSH 供给 + 会话工作区）")
        tui.send_line(f"/executor remote ssh://{REMOTE_HOST}")
        switched = tui.wait_for("执行后端已切换", 120, "远程切换回执")

        print("== 3. !pwd / !hostname 单发（转录卡多行渲染不可靠，逐行断言）")
        tui.send_line("!pwd")
        marker = tui.wait_for(
            r"executor/workspaces/", 60, "远程会话工作区", after=switched
        )
        tui.send_line("!hostname")
        tui.wait_for(rf"(?m)^\s*{REMOTE_HOSTNAME}$", 30, "远程主机名 dp", after=marker)

        print("== 4. 问模型 environment 段（系统提示词注入实证）")
        ask_offset = tui.send_line(
            "不要调用任何工具。你的系统提示词里有一段 environment 信息。"
            "请把其中的 backend 行、environment_id 行和 filesystem 段里的 "
            "root 行放进代码块、逐字符原样引用（含尖括号标签，不要改写不要省略）。"
        )
        tui.wait_for(
            r"<environment_id>ssh://liujinming@180\.184\.33\.245</environment_id>",
            240,
            "模型引用 environment_id（注入实证）",
            after=ask_offset,
        )
        tui.wait_for(r"<backend>executor</backend>", 30, "backend 标签", after=ask_offset)
        tui.wait_for(
            r"<root>/home/liujinming/\.nova/agent/executor/workspaces/",
            30,
            "workspace_roots 跟随后端",
            after=ask_offset,
        )

        print("== 4b. 模型经 write/read 工具操作远程工作区（全链路实证）")
        # 会话工作区从远程侧取最新目录（屏幕路径会被 TTY 换行截断，不可靠）
        import subprocess as _sp
        import time as _time

        tui.send_line(
            "用 write 工具在当前工作目录创建 fs-check.txt，内容恰好为 "
            "nova-fs-ok（不要加任何其他字符）；然后用 read 工具读回它。"
        )
        # 工具卡路径形态不定（相对/绝对/省略号截断）——屏幕只作时序信号
        # （内容字符串必然出现于写卡），实证归服务器侧轮询（真正的证据）
        tui.wait_for("nova-fs-ok", 300, "write 工具远程执行")

        def _remote(cmd: str) -> str:
            return _sp.run(
                [
                    "ssh",
                    "-i",
                    os.path.expanduser("~/.nova/agent/executor/id_ed25519"),
                    "-o",
                    "BatchMode=yes",
                    REMOTE_HOST,
                    cmd,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()

        content = ""
        for _attempt in range(10):
            _time.sleep(6)
            # 全工作区匹配（避免"最新目录"竞态；内容各工作区相同，
            # 取前 10 字节（"nova-fs-ok" 恰 10B，多文件 cat 拼接无换行）
            content = _remote(
                "cat ~/.nova/agent/executor/workspaces/*/fs-check.txt 2>/dev/null | head -c 10"
            )
            if content == "nova-fs-ok":
                break
        assert content == "nova-fs-ok", f"远程文件内容不符：{content!r}"
        print("  ✓ [远程文件实证] 服务器上 fs-check.txt 内容精确")

        print("== 5. /executor local 热切回本地")
        tui.send_line("/executor local")
        local_offset = tui.wait_for("执行后端已切换", 30, "切回本地回执")
        tui.send_line("!hostname")
        tui.wait_for(
            rf"(?m)^\s*{re.escape(LOCAL_HOSTNAME)}$",
            30,
            "本地主机名",
            after=local_offset,
        )

        print("\nALL PTY HOT-SWITCH ASSERTIONS PASSED")
        return 0
    finally:
        tui.kill()


if __name__ == "__main__":
    sys.exit(main())
