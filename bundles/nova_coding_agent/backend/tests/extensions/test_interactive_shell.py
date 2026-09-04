"""interactive_shell 扩展测试。

覆盖：命中集/``i `` 前缀判定、有能力时 request 参数与 result 翻译、
cancelled 取消回执、无能力/无 UI 的 TUI 缺失回执、未命中放行。
"""

import asyncio
import importlib.util
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set

from nova_harness.core.types.ui.primitives import UIResponse


def _load_extension():
    """按路径加载 interactive_shell.py（对齐 test_plan_mode.py 的加载方式）。"""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "interactive_shell.py"
    )
    spec = importlib.util.spec_from_file_location("_test_ext_interactive_shell", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


class _FakeNovaAPI:
    def __init__(self):
        self.handlers: Dict[str, Any] = {}

    def on(self, event_type, handler):
        self.handlers[event_type] = handler
        return lambda: None


class _ScriptedUI:
    """剧本式 UI（request 应答队列 + 调用记录）。"""

    def __init__(self, responses: List[UIResponse]):
        self._responses = list(responses)
        self.calls: List[tuple] = []

    @property
    def capabilities(self) -> Set[str]:
        return {"dialog:interactive-shell"}

    def has_capability(self, method: str) -> bool:
        return method in self.capabilities

    async def request(
        self, method: str, params: Dict[str, Any], signal: Any = None
    ) -> UIResponse:
        self.calls.append((method, params))
        return self._responses.pop(0)

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        pass


class _NoShellUI(_ScriptedUI):
    """无 dialog:interactive-shell 能力的 UI。"""

    @property
    def capabilities(self) -> Set[str]:
        return set()


def _fake_ctx(ui: Any = None, has_ui: bool = True):
    return SimpleNamespace(
        has_ui=has_ui,
        ui=ui or _ScriptedUI([]),
        cwd="/ctx-cwd",
    )


def _event(command: str, cwd: str = "/proj"):
    return SimpleNamespace(command=command, cwd=cwd)


def _setup(module):
    api = _FakeNovaAPI()
    module.extension(api)
    return api.handlers["user_bash"]


# ---------------------------------------------------------------------------
# 判定（_resolve_interactive）
# ---------------------------------------------------------------------------


def test_resolve_interactive_command_set():
    """交互程序集首 token 命中（strip 后）。"""
    module = _load_extension()
    for cmd in ["vim foo.py", "  htop  ", "ssh host", "less a.log", "man ls"]:
        assert module._resolve_interactive(cmd) == cmd.strip(), cmd


def test_resolve_force_prefix():
    """``i `` 前缀强制交互（剥离后作为真实命令）。"""
    module = _load_extension()
    assert module._resolve_interactive("i python") == "python"
    assert module._resolve_interactive("  i   top -n 1 ") == "top -n 1"


def test_resolve_non_interactive_passes():
    """普通命令/空前缀未命中。"""
    module = _load_extension()
    assert module._resolve_interactive("ls -la") is None
    assert module._resolve_interactive("git status") is None
    assert module._resolve_interactive("iconv a.txt") is None  # 非 "i " 前缀
    assert module._resolve_interactive("i ") is None  # 前缀后无命令
    assert module._resolve_interactive("   ") is None


# ---------------------------------------------------------------------------
# handler 流
# ---------------------------------------------------------------------------


def test_hit_with_capability_requests_and_translates():
    """命中 + 有能力：request 参数（command/cwd）+ {"exitCode"} → result 翻译。"""
    module = _load_extension()
    handler = _setup(module)
    ui = _ScriptedUI([UIResponse(value={"exitCode": 2})])
    ctx = _fake_ctx(ui=ui)

    result = _run(handler(_event("vim foo.py"), ctx))

    assert ui.calls == [
        ("dialog:interactive-shell", {"command": "vim foo.py", "cwd": "/proj"})
    ]
    assert result == {"result": {"output": "", "exitCode": 2}}


def test_hit_force_prefix_sends_stripped_command():
    """``i `` 前缀命中：剥离后的真实命令进入 request 载荷。"""
    module = _load_extension()
    handler = _setup(module)
    ui = _ScriptedUI([UIResponse(value={"exitCode": 0})])
    ctx = _fake_ctx(ui=ui)

    result = _run(handler(_event("i python"), ctx))

    assert ui.calls[0][1]["command"] == "python"
    assert result == {"result": {"output": "", "exitCode": 0}}


def test_hit_cancelled_returns_130_receipt():
    """前端取消：exitCode=130 + cancelled=True 回执。"""
    module = _load_extension()
    handler = _setup(module)
    ui = _ScriptedUI([UIResponse(cancelled=True)])
    ctx = _fake_ctx(ui=ui)

    result = _run(handler(_event("htop"), ctx))

    assert result == {
        "result": {
            "output": "(交互式命令已取消)",
            "exitCode": 130,
            "cancelled": True,
        }
    }


def test_hit_without_capability_returns_tui_missing_receipt():
    """命中但无面板能力： TUI 缺失回执（不弹 request）。"""
    module = _load_extension()
    handler = _setup(module)
    ui = _NoShellUI([])
    ctx = _fake_ctx(ui=ui)

    result = _run(handler(_event("vim foo.py"), ctx))

    assert ui.calls == []
    assert result == {
        "result": {"output": "(interactive commands require TUI)", "exitCode": 1}
    }


def test_hit_without_ui_returns_tui_missing_receipt():
    """命中但无 UI（headless）：同一 TUI 缺失回执。"""
    module = _load_extension()
    handler = _setup(module)
    ctx = _fake_ctx(has_ui=False)

    result = _run(handler(_event("htop"), ctx))

    assert result == {
        "result": {"output": "(interactive commands require TUI)", "exitCode": 1}
    }


def test_miss_returns_none():
    """未命中：不返回（正常执行路径）。"""
    module = _load_extension()
    handler = _setup(module)
    ui = _ScriptedUI([])
    ctx = _fake_ctx(ui=ui)

    assert _run(handler(_event("ls -la"), ctx)) is None
    assert ui.calls == []
