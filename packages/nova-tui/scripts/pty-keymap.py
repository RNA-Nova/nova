#!/usr/bin/env python3
"""pty-keymap：键位全量 PTY 验证（真实 TTY 逐键断言）。

键位表的每个 app 级动作（keymap/tables.ts 的 APP_KEYBINDINGS）在真实 TTY
里按一遍，断言其用户可见后果。与 pty-smoke.py（命令矩阵）互补：那个验
"敲命令"，这个验"按键位"。

平台说明（PTY 只能验"处理器侧"——终端实际发什么字节归终端）：
- shift+ctrl+p：legacy 终端发 \\x10（与 ctrl+p 同字节——碰撞，backward 不可达，
  本脚本钉死这个现实）；kitty 协商成功的终端发 CSI-u（\\x1b[112;6u）才区分。
  dumb PTY 不应答 kitty 协商 → 走 legacy 路径。
- alt+enter：legacy conhost 会被全屏切换吃掉（Windows 侧风险，文档备录）。
- ctrl+v：部分 Windows 终端是粘贴键（冲突备录）。

分三段：A 模型无关单会话 / B 退出键各起新会话 / C 真模型段（无
VOLCENGINE_API_KEY 跳过）。

用法：python3 scripts/pty-keymap.py [--cwd DIR] [--filter REGEX] [--list]
"""

from __future__ import annotations

import argparse
import codecs
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Callable, Optional

if sys.platform != "win32":
    import pty
    import select
    import signal

NOVA_REPO = os.environ.get(
    "NOVA_REPO", os.environ.get("NOVA_REPO_DIR", "/Users/liujinming/agent/nova-backup-20260824")
)
PYTHON = os.environ.get(
    "NOVA_PYTHON",
    os.path.join(NOVA_REPO, ".pixi", "envs", "dev", "Scripts" if sys.platform == "win32" else "bin", "python"),
)
MAIN_JS = os.path.join(NOVA_REPO, "packages", "nova-tui", "dist", "modes", "tui", "main.js")
import shutil as _shutil

NODE = os.environ.get("NOVA_NODE") or _shutil.which("node") or os.path.expanduser("~/.pixi/bin/node")

READY_TIMEOUT = 90.0
STEP_WAIT = 3.0

READY_RE = re.compile(r"coding_agent · \S")


def strip_ansi(text: str) -> str:
    """剥 ANSI 控制序列（入参是已解码文本——解码归 TuiSession 的增量解码器）。"""
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[\?[0-9;]*[hl]", "", text)  # kitty/模式切换序列
    return text.replace("\r", "\n")


def make_sandbox() -> tuple[str, str]:
    """临时 HOME + NOVA_AGENT_DIR + settings（trust 放开 + 本仓 bundle + 模型）。

    预填托管二进制（fd/rg）——fresh 沙箱首启的 ensure_binary 会从 GitHub
    下载（本机实测吃掉分钟级）；从真实安装复制进沙箱（开发机形态）。
    """
    home = tempfile.mkdtemp(prefix="nova-keymap-home-")
    agent_dir = os.path.join(home, "nova-agent")
    os.makedirs(agent_dir, exist_ok=True)
    real_bin = os.path.expanduser("~/.nova/agent/bin")
    sandbox_bin = os.path.join(agent_dir, "bin")
    os.makedirs(sandbox_bin, exist_ok=True)
    for name in ("fd", "rg"):
        for candidate in (
            os.path.join(real_bin, name),
            os.path.expanduser(f"~/.kimi-code/bin/{name}"),
        ):
            if os.path.isfile(candidate):
                import shutil

                shutil.copy2(candidate, os.path.join(sandbox_bin, name))
                break
    settings = {
        "defaultProjectTrust": "always",
        "default_provider": "volcengine",
        "default_model": "deepseek-v4-flash-260425",
        "default_thinking_level": "high",
        "packages": [
            f"path:{NOVA_REPO}/bundles/nova_base",
            f"path:{NOVA_REPO}/bundles/nova_coding_agent",
        ],
    }
    import json

    with open(os.path.join(agent_dir, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(settings, f)
    return home, agent_dir


class TuiSession:
    """一个伪终端里的 nova 进程（沙箱 HOME + NOVA_AGENT_DIR）。

    双后端：POSIX 用 pty+select；Windows 用 pywinpty（ConPTY）+ 读取线程
    （Windows 的 select 不吃管道句柄——读线程 + 队列是唯一姿势）。
    """

    def __init__(self, cwd: str, extra_env: Optional[dict] = None) -> None:
        home, agent_dir = make_sandbox()
        env = dict(
            os.environ,
            HOME=home,
            NOVA_AGENT_DIR=agent_dir,
            NOVA_PYTHON=PYTHON,
            **(extra_env or {}),
        )
        self.buffer = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._win32 = sys.platform == "win32"
        if self._win32:
            from winpty import PtyProcess  # pywinpty（CI windows 腿 pip 装）

            self.proc = PtyProcess.spawn(
                [NODE, MAIN_JS], cwd=cwd, env=env, dimensions=(24, 100)
            )
            import threading

            def _pump() -> None:
                while True:
                    try:
                        chunk = self.proc.read()  # pywinpty 文本模式：返回 str
                    except Exception:
                        break
                    if not chunk:
                        break
                    self.buffer += strip_ansi(chunk)

            threading.Thread(target=_pump, daemon=True).start()
            self.master = None
        else:
            self.master, slave = pty.openpty()
            self.proc = subprocess.Popen(
                [NODE, MAIN_JS],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env=env,
                close_fds=True,
                start_new_session=True,  # 独立进程组——ctrl+z 挂起不影响驱动进程
            )
            os.close(slave)

    def _drain(self, timeout: float) -> None:
        if self._win32:
            # 读线程已在攒 buffer——drain 即等待
            time.sleep(timeout)
            return
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
            self.buffer += strip_ansi(self._decoder.decode(chunk))

    def send(self, keys: str, wait: float = STEP_WAIT) -> None:
        if self._win32:
            self.proc.write(keys)
        else:
            os.write(self.master, keys.encode())
        self._drain(wait)

    def wait_for(self, pattern: str, timeout: float = 10.0) -> bool:
        """轮询排干直到 buffer 命中正则（渲染器后台加载等异步就绪的等待面）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if re.search(pattern, self.buffer):
                return True
            self._drain(0.4)
        return False

    def wait_ready(self) -> bool:
        """等到首屏就绪标记（模型行）。"""
        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline:
            if READY_RE.search(self.buffer):
                return True
            self._drain(0.5)
        return False

    def close(self) -> None:
        try:
            if self._win32:
                self.proc.write("\x03\x03")
            else:
                os.write(self.master, b"\x03\x03")
            self._drain(1.0)
        except OSError:
            pass
        try:
            self.buffer += strip_ansi(self._decoder.decode(b"", final=True))
        except Exception:
            pass
        try:
            if self._win32:
                self.proc.terminate()  # pywinpty：kill 要带 sig，terminate 才是无参强停
            else:
                self.proc.kill()
        except (OSError, AttributeError):
            pass
        try:
            if self.master is not None:
                os.close(self.master)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 用例：name, 执行体（session → 失败描述或 None）
# ---------------------------------------------------------------------------


def _clipboard_write(text: str) -> None:
    """跨平台剪贴板写入（mac pbcopy / win clip.exe——探针标记全 ASCII）。"""
    if sys.platform == "win32":
        subprocess.run(["clip.exe"], input=text.encode("ascii"), check=True)
    else:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)


def _clipboard_read() -> str:
    """跨平台剪贴板读回（mac pbpaste / win PowerShell Get-Clipboard）。"""
    if sys.platform == "win32":
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8", errors="replace").strip()
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def _last_model_line(tui: TuiSession) -> str:
    """当前屏上最后一行 模型·thinking 行（footer/welcome 行）。"""
    matches = re.findall(r"coding_agent · [^\n]+", tui.buffer)
    return matches[-1] if matches else ""


def case_ctrl_o_expand(tui: TuiSession) -> Optional[str]:
    """ctrl+o：工具卡片展开/折叠切换（长输出卡片的早前内容随展开出现）。"""
    tui.send("!seq 1 25\r", 2.0)
    if not tui.wait_for(r"\b25\b", 15):
        return "bash 卡片未出（seq 1 25 缺 25）"
    collapsed_has_head = re.search(r"^\s*3\s*$", tui.buffer, re.M)
    before = len(tui.buffer)
    tui.send("\x0f", 2.5)  # ctrl+o
    delta = tui.buffer[before:]
    if collapsed_has_head:
        return None  # 折叠态已见全文（终端够高）——无从断言，按通过
    if re.search(r"^\s*3\s*$", delta, re.M) or "earlier lines" not in delta:
        return None
    return "ctrl+o 后早前内容未出现"


def case_shift_tab_thinking(tui: TuiSession) -> Optional[str]:
    """shift+tab：thinking 级别循环（footer 模型行的级别词变化）。"""
    before_line = _last_model_line(tui)
    before = len(tui.buffer)
    tui.send("\x1b[Z", 2.5)  # backtab
    after_line = _last_model_line(tui)
    if not before_line or not after_line:
        return "模型行缺失"
    if before_line == after_line:
        return f"shift+tab 后模型行未变：{after_line!r}"
    return None


def case_ctrl_p_cycle(tui: TuiSession) -> Optional[str]:
    """ctrl+p：scoped/可用模型轮询（模型行的模型名变化）。"""
    before_line = _last_model_line(tui)
    tui.send("\x10", 3.0)  # ctrl+p
    after_line = _last_model_line(tui)
    if before_line == after_line:
        return f"ctrl+p 后模型行未变：{after_line!r}"
    return None


def case_shift_ctrl_p_collision(tui: TuiSession) -> Optional[str]:
    """shift+ctrl+p 的键位契约钉死：
    - legacy 字节 \\x10 与 ctrl+p 同字节（碰撞——表现同向前轮询）；
    - kitty CSI-u 形态（\\x1b[112;6u）解析为 shift+ctrl+p → 向后轮询
      （模型行回到向前之前的值）。dumb PTY 不应答协商，但 pi-tui 对
      CSI-u 的解析不依赖协商状态——legacy 终端永不发 CSI-u，解析它无害。
    """
    before_line = _last_model_line(tui)
    tui.send("\x10", 3.0)  # legacy：shift+ctrl+p 实际到达的就是 ctrl+p
    mid_line = _last_model_line(tui)
    if mid_line == before_line:
        return "legacy shift+ctrl+p（=ctrl+p 字节）未触发向前轮询"
    tui.send("\x1b[112;6u", 3.0)  # kitty CSI-u：shift+ctrl+p → 向后
    after_line = _last_model_line(tui)
    if after_line != before_line:
        return f"kitty shift+ctrl+p 未回环（期望回到 {before_line!r}，实际 {after_line!r}）"
    return None


def case_ctrl_l_selector(tui: TuiSession) -> Optional[str]:
    """ctrl+l：模型选择器打开（Esc 关闭）。"""
    before = len(tui.buffer)
    tui.send("\x0c", 3.0)  # ctrl+l
    if not re.search(r"选择模型|Select [Mm]odel|模型", tui.buffer[before:]):
        return "ctrl+l 后选择器未开"
    tui.send("\x1b", 2.0)
    return None


def case_ctrl_v_paste(tui: TuiSession) -> Optional[str]:
    """ctrl+v + alt+v：剪贴板文本进编辑器（双键位——ctrl+v 在 VS Code/WT
    被宿主吃掉，alt+v 为伴生键）。

    编辑器的回显不一定落字节（重绘区域不可见时增量为空）——真实内容经
    ctrl+g 的草稿转存探针读回（助手脚本转存后写入 ext-edit 标记；本条末尾
    ctrl+c 清掉该标记，不污染后续用例）。
    """
    probe = getattr(tui, "draft_probe", None)

    def _readback() -> str:
        if probe and os.path.exists(probe):
            os.unlink(probe)
        tui.send("\x07", 6.0)  # ctrl+g：助手转存草稿 + 写入 ext-edit 标记
        content = open(probe, encoding="utf-8").read() if probe and os.path.exists(probe) else ""
        tui.send("\x03", 1.0)  # 清掉 writeback 的 ext-edit 标记
        return content

    for keys, label in (("\x16", "ctrl+v"), ("\x1bv", "alt+v")):
        mark = f"pty-paste-mark-{label}"  # 探针标记全 ASCII（clip.exe 按 ANSI 走）
        _clipboard_write(mark)
        tui.send(keys, 3.0)
        time.sleep(4.0)  # osascript 图片探测 + pbpaste 串行最坏 ~6s
        tui._drain(1.0)
        content = _readback()
        if mark not in content:
            return f"{label} 后草稿无剪贴板文本（读到 {content[:40]!r}）"
    return None


def case_ctrl_g_external_editor(tui: TuiSession) -> Optional[str]:
    """ctrl+g：外部编辑器写回草稿（EDITOR 助手脚本写入标记）。"""
    # 会话 env 已注入 EDITOR 助手（见 main 的 extra_env）
    mark = "pty-ext-edit-mark"
    before = len(tui.buffer)
    tui.send("\x07", 6.0)  # ctrl+g
    if mark not in tui.buffer[before:]:
        return "ctrl+g 后草稿未写回"
    return None


def case_ctrl_z_suspend(tui: TuiSession) -> Optional[str]:
    """ctrl+z：挂起——PTY 沙箱的进程组是孤儿组（父在异会话），POSIX 会
    丢弃 SIGTSTP（防不可唤醒）——所以这里验应用侧契约而非内核停止：
    ① tui.stop() 的终端让位证据（kitty 弹栈序列 \\x1b[<u 落字节）；
    ② SIGCONT 后 tui.start() 重绘恢复。真实 shell 下 nova 是 shell 子进程
    （非孤儿组），内核停止自然成立。
    """
    if sys.platform == "win32":
        return None  # win32 默认键位已禁用
    before = len(tui.buffer)
    tui.send("\x1a", 2.0)  # ctrl+z
    delta = tui.buffer[before:]
    if "\x1b[<u" not in delta:  # kitty 弹栈 = tui.stop() 的终端让位证据
        return "ctrl+z 后未见终端让位（tui.stop 未执行）"
    # SIGCONT → 应触发恢复重绘（SIGCONT handler 已注册，与是否真的停过无关）
    os.killpg(os.getpgid(tui.proc.pid), signal.SIGCONT)
    tui._drain(3.0)
    after = tui.buffer[before + len(delta) :]
    if not re.search(r"coding_agent ·|escape 中断|命令", after):
        return "SIGCONT 后未见恢复重绘"
    if tui.proc.poll() is not None:
        return "ctrl+z 后进程退出（应为挂起恢复）"
    return None


def case_double_esc_tree(tui: TuiSession) -> Optional[str]:
    """Esc 双击（idle+空编辑器）：会话树导航打开。"""
    before = len(tui.buffer)
    # 双击须 500ms 窗内——一次 write 送两字节（逐 send 的 drain 在慢机上必超窗，
    # ctrl+c 双击同款修法）
    os.write(tui.master, b"\x1b\x1b") if not tui._win32 else tui.proc.write("\x1b\x1b")
    tui._drain(3.5)
    if not re.search(r"会话树|session", tui.buffer[before:], re.I):
        return "Esc 双击后会话树未开"
    tui.send("\x1b", 2.0)  # 关闭
    return None


def case_alt_up_empty(tui: TuiSession) -> Optional[str]:
    """alt+↑：空队列还原——不崩、给提示或不动作。"""
    before = len(tui.buffer)
    tui.send("\x1b[A", 2.5)  # alt+up
    delta = tui.buffer[before:]
    if "Traceback" in delta or "TypeError" in delta:
        return "alt+↑ 空队列触发异常"
    return None


def case_ctrl_c_clears_editor(tui: TuiSession) -> Optional[str]:
    """ctrl+c 单击：清空输入框（非退出）。

    探针：先键入 `!echo CLEARPROBE`（不回车）→ ctrl+c → 键入 `!echo AFTER`
    回车。若清空失效，提交的是混合命令 `echo CLEARPROBE!echo AFTER`，卡片
    输出会含 CLEARPROBE。卡片渲染有后台加载竞态——轮询等内容。
    """
    tui.send("!echo CLEARPROBE", 1.0)  # 只键入不提交
    tui.send("\x03", 1.2)  # ctrl+c 单击
    before = len(tui.buffer)
    tui.send("!echo AFTER\r", 1.0)
    if not tui.wait_for(r"\bAFTER\b", 20):
        return "清空后提交未见 AFTER（编辑器状态异常）"
    delta = tui.buffer[before:]
    if "CLEARPROBE" in delta:
        return "ctrl+c 未清空输入框（混合命令被执行）"
    if tui.proc.poll() is not None:
        return "ctrl+c 单击误退"
    return None


def case_ctrl_d_nonempty_stays(tui: TuiSession) -> Optional[str]:
    """ctrl+d：编辑器非空时不退出（空才退的反向断言）。"""
    tui.send("草稿文本", 1.0)
    tui.send("\x04", 1.5)  # ctrl+d
    time.sleep(1.0)
    if tui.proc.poll() is not None:
        return "ctrl+d 在非空编辑器误退"
    # 复原：清空草稿，别污染后续用例
    tui.send("\x03", 1.0)
    return None


def case_esc_dialog_yield(tui: TuiSession) -> Optional[str]:
    """Esc 域级让路：对话框开着时 Esc 归组件（关框），不触发域级中止/导航。"""
    tui.send("/tools\r", 3.0)  # 开工具开关面板
    before = len(tui.buffer)
    tui.send("\x1b", 2.0)  # Esc：应关框而非中止
    delta = tui.buffer[before:]
    if "已中止" in delta or "aborted" in delta.lower():
        return "对话框开着时 Esc 误触发中止"
    # 控制交还编辑器的证据：/debug 探针可达
    tui.send("/debug\r", 4.0)
    if not re.search(r"debug|dump", tui.buffer[before:]):
        return "Esc 关框后控制未回编辑器（/debug 探针未达）"
    return None


# —— B 段：退出键（各起新会话，断言进程级行为）——


def case_ctrl_d_exits(cwd: str) -> Optional[str]:
    """ctrl+d：空编辑器退出。"""
    tui = TuiSession(cwd)
    try:
        if not tui.wait_ready():
            return "启动超时"
        tui.send("\x04", 2.0)  # ctrl+d
        time.sleep(1.5)
        if tui.proc.poll() is None:
            return "ctrl+d 后进程未退出"
        return None
    finally:
        tui.close()


def case_ctrl_c_double_exits(cwd: str) -> Optional[str]:
    """ctrl+c 双击：退出（单击只清编辑器——kitty release 不算第二次）。"""
    tui = TuiSession(cwd)
    try:
        if not tui.wait_ready():
            return "启动超时"
        tui.send("\x03", 1.0)  # 单击：不退出
        if tui.proc.poll() is not None:
            return "ctrl+c 单击误退"
        # 双击须 500ms 窗内——两次按键一次 write 送入（逐 send 的 drain
        # 会把间隔拉到秒级，必超窗）
        os.write(tui.master, b"\x03\x03")
        tui._drain(2.0)
        time.sleep(1.0)
        if tui.proc.poll() is None:
            return "ctrl+c 双击未退出"
        return None
    finally:
        tui.close()


# —— C 段：真模型（VOLCENGINE_API_KEY 缺失跳过）——


def case_turn_copy_abort_followup(tui: TuiSession) -> Optional[str]:
    """一轮真对话 → ctrl+x 复制最后回复（剪贴板读回，独特词防误判）；
    第二轮 working 中 alt+enter 排队 → alt+↑ 还原进编辑器 → Esc 中止。"""
    tui.send("只回复两个字：蓝莓\r", 1.0)
    if not tui.wait_for("蓝莓", 60.0):
        return "首轮对话未见蓝莓标记"
    # ctrl+x：复制最后 assistant 消息（剪贴板是真实 mac 全局——与本机用户
    # 并发使用存在竞争；断言"读回内容出自屏幕回复区"，允许一次重试）
    reply_match = None
    for _ in range(2):
        _clipboard_write("__pty_clear__")
        tui.send("\x18", 2.5)
        time.sleep(0.5)
        pasted = _clipboard_read()
        if "蓝莓" in pasted:
            reply_match = pasted
            break
        time.sleep(1.0)
    if reply_match is None:
        return "ctrl+x 剪贴板未见回复（剪贴板与本机用户并发使用时可能误败）"
    # 第二轮：working 中 alt+enter 排队 → alt+↑ 还原
    tui.send("写一段80字废话\r", 2.0)
    tui.send("插队消息", 0.5)
    before = len(tui.buffer)
    tui.send("\x1b\r", 2.0)  # alt+enter：排队
    tui.send("\x1b[A", 2.5)  # alt+↑：还原进编辑器
    delta = tui.buffer[before:]
    if "插队消息" not in delta:
        return "alt+↑ 后排队内容未还原进编辑器"
    tui.send("\x1b", 2.5)  # Esc 中止
    if "Traceback" in tui.buffer[-2000:]:
        return "Esc 中止触发 Traceback"
    return None


def main() -> int:
    if sys.platform == "win32":
        # ✔/✖ 在 GBK 控制台（charmap）不可编码——stdout 换 UTF-8 容错
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cwd", default=tempfile.gettempdir(), help="会话工作目录（缺省系统临时目录）"
    )
    parser.add_argument("--filter", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--local",
        action="store_true",
        help="无可用模型的环境（CI 无 API key）——跳过模型依赖用例（ctrl+p 族/C 段）",
    )
    args = parser.parse_args()

    # A 段（模型无关，单会话）——顺序即依赖（ctrl+o 的卡片等）
    section_a: list[tuple[str, Callable[[TuiSession], Optional[str]]]] = [
        ("ctrl+o 工具卡片展开", case_ctrl_o_expand),
        ("shift+tab thinking 循环", case_shift_tab_thinking),
        ("ctrl+p 模型向前轮询", case_ctrl_p_cycle),
        ("shift+ctrl+p 碰撞现实", case_shift_ctrl_p_collision),
        ("ctrl+l 模型选择器", case_ctrl_l_selector),
        ("ctrl+v 剪贴板粘贴", case_ctrl_v_paste),
        ("ctrl+g 外部编辑器写回", case_ctrl_g_external_editor),
        ("ctrl+d 非空编辑器不退", case_ctrl_d_nonempty_stays),
        ("ctrl+c 单击清空输入框", case_ctrl_c_clears_editor),
        ("Esc 对话框让路", case_esc_dialog_yield),
        ("Esc 双击会话树", case_double_esc_tree),
        ("ctrl+z 挂起恢复", case_ctrl_z_suspend),
        ("alt+↑ 空队列还原", case_alt_up_empty),
    ]
    section_b: list[tuple[str, Callable[[str], Optional[str]]]] = [
        ("ctrl+d 空编辑器退出", case_ctrl_d_exits),
        ("ctrl+c 双击退出", case_ctrl_c_double_exits),
    ]

    # 模型依赖用例（无可用模型时不可达）：ctrl+p 轮询需 scoped/可用模型 ≥2
    MODEL_CASES = {"ctrl+p 模型向前轮询", "shift+ctrl+p 碰撞现实", "shift+tab thinking 循环"}
    cases: list[tuple[str, str, object]] = [
        *[(name, "a", fn) for name, fn in section_a],
        *[(name, "b", fn) for name, fn in section_b],
        ("真模型：复制/排队/还原/中止", "c", case_turn_copy_abort_followup),
    ]
    if args.local:
        cases = [c for c in cases if c[1] != "c" and c[0] not in MODEL_CASES]
    if args.filter:
        pattern = re.compile(args.filter)
        cases = [c for c in cases if pattern.search(c[0])]
    if args.list:
        for name, *_ in cases:
            print(name)
        return 0

    failures: list[tuple[str, str]] = []

    def report(name: str, problem: Optional[str]) -> None:
        if problem is None:
            print(f"✔ {name}")
        else:
            failures.append((name, problem))
            print(f"✖ {name}  {problem}")

    # —— A 段 ——
    if any(sec == "a" for _, sec, _ in cases):
        # 外部编辑器助手脚本（ctrl+g 用）——双模：先把草稿转存探针文件
        # （ctrl+v 的读回面），再写入标记（ctrl+g 的写回断言）
        probe = os.path.join(tempfile.mkdtemp(prefix="nova-keymap-probe-"), "draft.txt")
        helper_dir = tempfile.mkdtemp(prefix="nova-keymap-editor-")
        helper = os.path.join(helper_dir, "editor_helper.py")
        with open(helper, "w", encoding="utf-8") as f:
            f.write(
                "import shutil, sys, pathlib\n"
                f'shutil.copyfile(sys.argv[1], r"{probe}")\n'
                'pathlib.Path(sys.argv[1]).write_text("pty-ext-edit-mark", encoding="utf-8")\n'
            )
        editor_cmd = f"{PYTHON} {helper}"  # app 按空格分拆 cmd+args（不带引号）——路径均无空格
        tui = TuiSession(args.cwd, extra_env={"EDITOR": editor_cmd, "VISUAL": editor_cmd})
        tui.draft_probe = probe  # type: ignore[attr-defined]
        try:
            if not tui.wait_ready():
                print("✖ A 段启动超时")
                print(tui.buffer[-800:])  # 死因留档
                failures.append(("A 段启动", "ready 标记未现"))
            else:
                tui.send("\x1b", 1.0)  # 关掉可能的首启引导/对话框让路
                for name, sec, fn in cases:
                    if sec != "a":
                        continue
                    report(name, fn(tui))  # type: ignore[arg-type]
        finally:
            tui.close()

    # —— B 段 ——
    for name, sec, fn in cases:
        if sec != "b":
            continue
        report(name, fn(args.cwd))  # type: ignore[arg-type]

    # —— C 段（真模型）——
    for name, sec, fn in cases:
        if sec != "c":
            continue
        if not os.environ.get("VOLCENGINE_API_KEY"):
            print(f"- {name}（跳过：无 VOLCENGINE_API_KEY）")
            continue
        tui = TuiSession(args.cwd)
        try:
            if not tui.wait_ready():
                report(name, "启动超时")
            else:
                tui.send("\x1b", 1.0)
                report(name, fn(tui))  # type: ignore[arg-type]
        finally:
            tui.close()

    total = len([c for c in cases if c[1] != "c" or os.environ.get("VOLCENGINE_API_KEY")])
    print(f"\n{total - len(failures)}/{total} 通过")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
