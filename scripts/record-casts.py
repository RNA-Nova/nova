#!/usr/bin/env python3
"""批量素材录制器：nova 打包产物 → 每场景一条 .cast（agg 转 GIF / PIL 抽帧出 PNG）。

场景表驱动：每个场景一串步骤（wait/type/key/drain/quit）。PNG 场景以目标
状态收尾（最后一帧即截图）；GIF 场景带安静期收尾。close 一律 SIGKILL——
不触发"恢复会话"提示文本（不污染尾帧）。

用法：python3 scripts/record-casts.py <cast输出目录> [场景名...]
环境：需 VOLCENGINE_API_KEY；NOVA_ROOT 指打包解出目录（含 nova + runtime/——
      先解一份产物：mkdir -p /tmp/nova-bin && tar -xzf dist/release/nova-
      darwin-arm64.tar.gz -C /tmp/nova-bin，NOVA_ROOT=/tmp/nova-bin）。
      .cast 经 agg 转 GIF（agg --idle-time-limit 2 --font-size 16 in.cast out.gif），
      PNG 截图 = 目标状态收尾的场景 cast 最后一帧（PIL seek 导出）。
"""

from __future__ import annotations

import codecs
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

ROOT = os.environ.get("NOVA_ROOT", "/tmp/nova-bin")
BINARY = os.path.join(ROOT, "nova")
REPO = os.environ.get("RECORD_REPO", "/Users/liujinming/agent/nova-backup-20260824")
PIXI_PY = "/Users/liujinming/agent/nova-backup-20260824/.pixi/envs/dev/bin/python"
COLS, ROWS = 120, 40


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[float, str]] = []
        self.t0 = time.monotonic()
        self.buffer = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self.master = -1
        self.proc: subprocess.Popen | None = None

    def spawn(self, cwd: str, env_extra: dict[str, str]) -> None:
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        env = dict(os.environ, TERM="xterm-256color", **env_extra)
        env.pop("NOVA_BACKEND", None)
        env.pop("NOVA_PYTHON", None)
        self.proc = subprocess.Popen([BINARY], stdin=slave, stdout=slave,
                                     stderr=slave, cwd=cwd, env=env, close_fds=True)
        os.close(slave)

    def drain(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r, _, _ = select.select([self.master], [], [], 0.1)
            if not r:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                return
            if not chunk:
                return
            text = self._decoder.decode(chunk)
            self.events.append((time.monotonic() - self.t0, text))
            self.buffer += text

    def wait_for(self, pattern: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if re.search(pattern, self.buffer):
                return True
            self.drain(0.3)
        return False

    def mark(self) -> int:
        """水位标记：配合 wait_since 只匹配标记后的新内容（累积 buffer 里
        的旧文本——如上一会话的用量行——会造成误判）。"""
        return len(self.buffer)

    def wait_since(self, mark: int, pattern: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if re.search(pattern, self.buffer[mark:]):
                return True
            self.drain(0.3)
        return False

    def quiet_tail(self, quiet_s: float = 5.0, cap: float = 90.0) -> None:
        deadline = time.monotonic() + cap
        while time.monotonic() < deadline:
            before = len(self.events)
            self.drain(quiet_s)
            if len(self.events) == before:
                return

    def type_text(self, text: str, cps: float = 25.0) -> None:
        for ch in text:
            os.write(self.master, ch.encode())
            self.drain(1.0 / cps)

    def key(self, data: bytes) -> None:
        os.write(self.master, data)
        self.drain(0.4)

    def close(self) -> None:
        try:
            self.proc.kill()
        except (OSError, AttributeError):
            pass
        try:
            os.close(self.master)
        except OSError:
            pass

    def save(self, path: str) -> None:
        header = {"version": 2, "width": COLS, "height": ROWS,
                  "timestamp": int(time.time()), "env": {"TERM": "xterm-256color"}}
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(header) + "\n")
            for t, data in self.events:
                f.write(json.dumps([round(t, 3), "o", data], ensure_ascii=False) + "\n")


# —— 场景（步骤为 (动作, 参数) 序列；动作：wait/type/key/drain/quiet/new） ——

def _steps_hero(r: Recorder) -> None:
    r.type_text("看一下当前目录的结构，用 ls 列出来，然后一句话告诉我这是什么项目")
    r.key(b"\r")
    assert r.wait_for(r"↑[1-9]", 120), "hero: 无回答"
    r.quiet_tail()


def _steps_toolcards(r: Recorder) -> None:
    r.type_text("先用 todo 建一个三步的发布检查清单，然后把 README.md 的标题改成 Nova!，最后列出当前目录")
    r.key(b"\r")
    assert r.wait_for(r"↑[1-9]", 180), "toolcards: 无回答"
    r.quiet_tail()
    r.key(b"\x0f")  # ctrl+o 展开全部卡片
    r.drain(1.5)


def _steps_subagent(r: Recorder) -> None:
    r.type_text("并行调两个 worker 子代理：一个写 /tmp/nova-sg-a.txt 内容 hello-a，另一个写 /tmp/nova-sg-b.txt 内容 hello-b")
    r.key(b"\r")
    # subagent_gate 检查点：逐名裁决（两个 worker 两道门）——按 Enter 允许一次
    for _ in range(4):
        if r.wait_for(r"允许一次|Allow", 60):
            r.key(b"\r")
        if re.search(r"✓.*subagent|完成|已写|hello-b", r.buffer):
            break
    assert r.wait_for(r"hello-b|完成|✓", 180), "subagent: 未完成"
    r.quiet_tail()


def _steps_selectors(r: Recorder) -> None:
    r.key(b"/model")
    r.key(b"\r")
    assert r.wait_for(r"deepseek", 15), "selectors: 面板未开"
    r.type_text("v4", cps=6)  # 模糊搜索中态
    r.drain(1.2)


def _steps_tree(r: Recorder) -> None:
    r.type_text("记住数字 42")
    r.key(b"\r")
    # 先等回合开始（Working 出现）再等用量出账——空窗误判（首字节前的安静）
    # 会把后续键误并进编辑器
    assert r.wait_for(r"Working", 30), "tree: 回合未开始"
    assert r.wait_for(r"↑[1-9]", 90), "tree: 无回答"
    r.quiet_tail()
    # slash 命令单笔写入（命令+\r 同帧）：拆开写会让 Enter 被斜杠补全
    # 菜单截胡（选中补全项而非执行）
    r.key(b"/fork\r")
    assert r.wait_for(r"从消息分叉", 15), "tree: fork 选择器未开"
    mark = r.mark()
    r.key(b"\r")  # 选中默认条目分叉
    # 分叉后新分支从分叉点重跑回答（一个全新的 Working 回合）——等它落定；
    # 用量行用 wait_since（旧会话的 ↑16902 已在累积 buffer 里，直接匹配会误判）
    assert r.wait_since(mark, r"Working", 30), "tree: 分叉分支回合未开始"
    assert r.wait_since(mark, r"↑[1-9]", 90), "tree: 分叉分支回合未结"
    r.quiet_tail()
    r.key(b"/tree\r")
    assert r.wait_since(mark, r"会话树", 15), "tree: 树选择器未开"
    r.drain(1.5)


def _steps_planmode(r: Recorder) -> None:
    r.key(b"/plan")
    r.key(b"\r")
    r.drain(1.5)
    r.type_text("写一个文件 /tmp/x.txt 内容 hello")
    r.key(b"\r")
    assert r.wait_for(r"plan|只读|拦截|规划", 60), "planmode: 无响应"
    r.quiet_tail(quiet_s=3.0, cap=45.0)


def _steps_packages(r: Recorder) -> None:
    r.key(b"/packages")
    r.key(b"\r")
    assert r.wait_for(r"nova-base|nova-coding-agent|包", 20), "packages: 面板未开"
    r.drain(1.5)


# 场景工作目录：hero 读真实仓库（只读 ls/read——上镜用真项目）；
# 会写盘的场景一律进临时沙盒目录（toolcards 的 edit 会真改 README——
# 曾在真实仓库录过一次把 README 标题改掉，纪律因此而立）
def _scratch_cwd() -> str:
    d = tempfile.mkdtemp(prefix="nova-rec-cwd-")
    with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
        f.write("# Nova\n\n演示项目。\n\n## 结构\n\n- src/\n- docs/\n")
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    os.makedirs(os.path.join(d, "docs"), exist_ok=True)
    return d


SCENARIO_CWD = {
    "toolcards": "scratch",
    "planmode": "scratch",
    "subagent": "scratch",
}


SCENARIOS = {
    "hero": _steps_hero,
    "toolcards": _steps_toolcards,
    "subagent": _steps_subagent,
    "selectors": _steps_selectors,
    "tree": _steps_tree,
    "planmode": _steps_planmode,
    "packages": _steps_packages,
}


def prepare(home: str, agent_dir: str) -> None:
    """首启落地内建 base + pkg 装 coding（真实用户首启链路）。"""
    env_extra = {"HOME": home, "NOVA_AGENT_DIR": agent_dir}
    boot = Recorder()
    boot.spawn(REPO, env_extra)
    ok = boot.wait_for("deepseek-v4-flash-260425", 90.0)
    boot.key(b"/quit\r")
    boot.drain(3.0)
    boot.close()
    assert ok, "准备启动超时"
    ie = dict(os.environ, HOME=home, NOVA_AGENT_DIR=agent_dir, NOVA_PYTHON=PIXI_PY)
    r = subprocess.run(
        [os.path.join(ROOT, "runtime", "nova-server"), "pkg", "install", "--editable",
         f"path:{REPO}/bundles/nova_coding_agent"],
        env=ie, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"coding 安装失败: {r.stdout[-300:]}{r.stderr[-300:]}"


def main() -> int:
    if not os.environ.get("VOLCENGINE_API_KEY"):
        print("跳过：无 VOLCENGINE_API_KEY")
        return 1
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/nova-rec/casts"
    wanted = sys.argv[2:] or list(SCENARIOS)
    os.makedirs(out_dir, exist_ok=True)

    home = tempfile.mkdtemp(prefix="nova-rec-home-")
    agent_dir = os.path.join(home, "nova-agent")
    os.makedirs(agent_dir)
    with open(os.path.join(agent_dir, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"default_provider": "volcengine",
                   "default_model": "deepseek-v4-flash-260425",
                   "default_project_trust": "always"}, f)

    print("准备（首启落地 + coding 安装）…", flush=True)
    prepare(home, agent_dir)

    failures: list[str] = []
    for name in wanted:
        fn = SCENARIOS[name]
        cwd = _scratch_cwd() if SCENARIO_CWD.get(name) == "scratch" else REPO
        rec = Recorder()
        rec.spawn(cwd, {"HOME": home, "NOVA_AGENT_DIR": agent_dir})
        try:
            ok = rec.wait_for("deepseek-v4-flash-260425", 90.0)
            if not ok:
                raise AssertionError("启动超时")
            rec.drain(1.5)
            fn(rec)
            rec.save(os.path.join(out_dir, f"{name}.cast"))
            print(f"✔ {name}（{len(rec.events)} 事件）", flush=True)
        except AssertionError as exc:
            rec.save(os.path.join(out_dir, f"{name}.cast"))
            print(f"✘ {name}: {exc}", flush=True)
            failures.append(name)
        finally:
            rec.close()

    if failures:
        print("失败场景:", ", ".join(failures))
        print(f"（沙盒保留排查：HOME={home}）")
        return 1
    print(f"全部完成 → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
