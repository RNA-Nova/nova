"""RPC 模式下用户 bash 工具调用的黑盒回归（Windows 挂起事故的 CI 复现钉）。

事故画像（Windows 实机实测）：TUI 态 bash 卡片一直运行态，须用户再次
发送才出结果；探针（invokeUserTool 直调）看到 user_tool start 事件后
RPC 结果永不到达；headless print 模式同机 0.2s 完成。差异锁定在
"RPC 长驻事件循环 + 引擎"的交互——本测试把该形态钉进 CI（三平台）。

不标 integration：全程本机进程（模型不参与），windows-latest 腿直接跑。
"""

import json
import sys
import time

import pytest

from tests.conformance.test_wire_conformance import Wire, _spawn_backend


def _write_settings(agent_dir, repo_root):
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "settings.json").write_text(
        json.dumps(
            {
                "defaultProjectTrust": "always",
                "packages": [
                    f"path:{repo_root / 'bundles' / 'nova_base'}",
                    f"path:{repo_root / 'bundles' / 'nova_coding_agent'}",
                ],
            }
        ),
        encoding="utf-8",
    )


def test_user_bash_invoke_completes_over_stdio(tmp_path):
    """RPC 模式 invokeUserTool（bash）：引擎完成 + RPC 结果按时到达。

    断言两件事：RPC 结果在期限内到达（引擎在 RPC 长驻循环下不卡）；
    user_tool 进度帧在到达结果之前出现（start/output 事件流）。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[5]
    agent_dir = tmp_path / "agent"
    _write_settings(agent_dir, root)
    proj = tmp_path / "proj"
    proj.mkdir()

    proc = _spawn_backend()
    wire = Wire(proc)
    try:
        wire.call("initialize")
        wire.notify("system/capabilities", {"capabilities": ["notify"]})
        wire.drain_events(0.3)
        resp = wire.call(
            "createSession", {"cwd": str(proj), "agentDir": str(agent_dir)}
        )
        assert "error" not in resp, resp

        wire.events.clear()
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
        assert "PROBE_B" in message.get("output", "")
        # 引擎有 sleep 1——完成不该快过它，也不该远超它
        assert elapsed >= 1.0
        assert elapsed < 25.0, f"完成耗时 {elapsed:.1f}s（Windows 挂起形态）"

        # start 事件先于结果到达（进度帧流起来了）
        user_tool_events = [e for e in wire.events if e.get("type") == "user_tool"]
        assert any(
            e.get("data", {}).get("event") == "start" for e in user_tool_events
        ), "user_tool start 事件未见"
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
