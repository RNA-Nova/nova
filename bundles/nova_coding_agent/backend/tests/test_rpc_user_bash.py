"""RPC 模式下用户 bash 工具调用的黑盒回归（Windows 挂起事故的 CI 复现钉）。

事故画像（Windows 实机实测 + CI windows 腿复现）：TUI 态 bash 卡片须
用户再次发送才出结果；invokeUserTool 直调看到 user_tool start 帧后
RPC 结果永不到达；headless print 模式同机 0.2s 完成。差异锁定在
"RPC 长驻事件循环 + 引擎"的交互上。

本测试位于 bundle 套件（两个包都可 import——harness 腿没有 bundle 包，
会在别处环境失败）。不标 integration：全程本机进程，模型不参与。

失败时打印帧时间线 + 后端 stderr 尾部（死因留档）。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


def _write_settings(agent_dir: Path, pkg_dir: Path) -> None:
    """settings：trust 放开 + 注册夹具包（path 源，绝对路径）。"""
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "settings.json").write_text(
        json.dumps(
            {
                "defaultProjectTrust": "always",
                "packages": [f"path:{pkg_dir}"],
            }
        ),
        encoding="utf-8",
    )


def _make_user_bash_pkg(pkg_dir: Path, repo_root: Path) -> None:
    """最小夹具包：只带真实 bash 用户工具（抄自官方 bundle——跟住真身），
    零 Python 依赖（不触发 pip——否则冷缓存下 pip 构建输出会污染
    协议通道，且 CI 冷装超时）。"""
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "fixture-user-bash"\nversion = "0.1.0"\n'
        '[tool.nova]\nuser_tools = ["./user_tools/bash.py"]\n',
        encoding="utf-8",
    )
    (pkg_dir / "user_tools").mkdir()
    real = (
        repo_root
        / "bundles"
        / "nova_coding_agent"
        / "backend"
        / "user_tools"
        / "bash.py"
    )
    (pkg_dir / "user_tools" / "bash.py").write_text(
        real.read_text(encoding="utf-8"), encoding="utf-8"
    )


class _Wire:
    """最小黑盒客户端：NDJSON 请求/响应 + 通知捕获 + ui/request 自动应答。

    读侧专职线程 + 队列（阻塞读——select+readline 的用户态预读缓冲
    陷阱实录在 conformance 套件）。与 conformance 套件的 Wire 同构，
    裁剪到本测试所需。
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._next_id = 0
        self.frames: list[tuple[float, str]] = []  # （相对秒，行首 120 字符）
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self._lines.put(line)

    def _write(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        """通知（无 id——system/capabilities 即此形态：服务器收讫即登记，
        无应答帧）。"""
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call(
        self, method: str, params: dict | None = None, timeout: float = 15.0
    ) -> dict:
        self._next_id += 1
        rid = self._next_id
        self._write(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        )
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{method} 未在 {timeout}s 内应答")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            msg = json.loads(line)
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                return msg
            if msg.get("method") == "ui/request":
                # 反向原语自动应答（取消语义——探针不应答会挂住后端）
                rid_ui = msg["params"]["id"]
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": f"ui-resp-{rid_ui}",
                        "method": "ui/response",
                        "params": {"id": rid_ui, "result": {"cancelled": True}},
                    }
                )
            self.frames.append((time.monotonic(), line[:120]))


def _spawn_backend(debug_log: Path, agent_dir: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    # 引擎阶段观测：RPC 模式 fd 2 被 dup2 到 rpc-stderr.log（stderr 尾恒空），
    # 故打点写专用文件——挂起时日志停在最后完成的阶段，挂点定段
    env["NOVA_ENGINE_DEBUG"] = str(debug_log)
    # 本地 pixi 环境的 nova_coding_agent 可能是 site-packages 陈旧副本
    # （非 editable）——PYTHONPATH 前置仓内 backend/，保证后端跑的是仓内代码
    repo_backend = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = repo_backend + os.pathsep + env.get("PYTHONPATH", "")
    # agent 目录收进 tmp_path：rpc-stderr.log（fd 2 dup2 落点）随测试
    # 目录留档，失败时可倒出——循环内部异常栈的唯一可见处
    env["NOVA_AGENT_DIR"] = str(agent_dir)
    proc = subprocess.Popen(
        [sys.executable, "-m", "nova_harness.modes.rpc.cli"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    # stderr 排干线程（异常栈不进协议通道；防止管道缓冲写满死锁）；
    # 内容留档——teardown 断言失败时带出后端死因
    stderr_lines: list[str] = []

    def _drain() -> None:
        for line in proc.stderr:
            stderr_lines.append(line)

    threading.Thread(target=_drain, daemon=True).start()
    proc.stderr_lines = stderr_lines  # type: ignore[attr-defined]
    return proc


def test_user_bash_invoke_completes_over_stdio(tmp_path):
    """invokeUserTool(bash) 在 RPC 长驻循环下按时完成（结果帧到达 +
    start 帧先行 + 输出落进结果消息）。"""
    root = Path(__file__).resolve().parents[4]
    pkg_dir = tmp_path / "fixture_pkg"
    _make_user_bash_pkg(pkg_dir, root)
    agent_dir = tmp_path / "agent"
    _write_settings(agent_dir, pkg_dir)
    proj = tmp_path / "proj"
    proj.mkdir()

    proc = _spawn_backend(tmp_path / "engine-stage.log", agent_dir)
    wire = _Wire(proc)
    t0 = time.monotonic()
    try:
        wire.call("initialize")
        wire.notify("system/capabilities", {"capabilities": ["notify"]})
        resp = wire.call(
            "createSession", {"cwd": str(proj), "agentDir": str(agent_dir)}
        )
        assert "error" not in resp, resp

        wire.frames.clear()
        started = time.monotonic()
        result = wire.call(
            "invokeUserTool",
            {
                "name": "bash",
                "params": {"command": "echo PROBE_A; sleep 1; echo PROBE_B"},
            },
            timeout=30.0,
        )
        elapsed = time.monotonic() - started
        assert "error" not in result, result
        message = result["result"]["message"]
        assert "PROBE_B" in str(message)
        assert elapsed >= 1.0
        assert elapsed < 25.0, f"完成耗时 {elapsed:.1f}s（Windows 挂起形态）"

        # user_tool 的 start 帧先于结果到达（事件流活着）
        starts = [f for f in wire.frames if '"user_tool"' in f[1]]
        assert starts, "user_tool 事件帧未见"
    except Exception:
        # 死因留档：帧时间线 + 引擎阶段日志 + rpc-stderr.log 尾
        # （RPC 模式 stderr 被 dup2 到该文件——循环内部异常栈的唯一可见处）
        timeline = "\n".join(
            f"  {t - t0:6.1f}s  {head}" for t, head in wire.frames[-25:]
        )
        stage_log = tmp_path / "engine-stage.log"
        stages = stage_log.read_text(encoding="utf-8") if stage_log.exists() else ""
        stderr_log = agent_dir / "logs" / "rpc-stderr.log"
        stderr_tail = ""
        if stderr_log.exists():
            stderr_tail = "".join(
                stderr_log.read_text(encoding="utf-8").splitlines(keepends=True)[-30:]
            )
        pytest.fail(
            f"invokeUserTool 挂起/失败。\n帧时间线：\n{timeline}\n"
            f"引擎阶段：\n{stages}\nrpc-stderr 尾：\n{stderr_tail}"
        )
    finally:
        try:
            wire.call("shutdown", timeout=5)
        except Exception:
            pass
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
