"""confirm_destructive 扩展测试（pi confirm-destructive.ts 对位）。

覆盖：confirm 放行/取消两态、entry_count=0 放行、headless 放行、
session_before_switch / session_before_fork 两事件注册与文案。
"""

import asyncio
import importlib.util
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Set

from nova_harness.types.ui.primitives import UIResponse


def _load_extension():
    """按路径加载 confirm_destructive.py（对齐 test_plan_mode.py 的加载方式）。"""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "confirm_destructive.py"
    )
    spec = importlib.util.spec_from_file_location("_test_ext_confirm_destructive", path)
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
    """剧本式 UI（confirm 应答队列 + 调用记录）。"""

    def __init__(self, responses: List[UIResponse]):
        self._responses = list(responses)
        self.calls: List[tuple] = []

    @property
    def capabilities(self) -> Set[str]:
        return {"confirm"}

    def has_capability(self, method: str) -> bool:
        return method in self.capabilities

    async def request(
        self, method: str, params: Dict[str, Any], signal: Any = None
    ) -> UIResponse:
        self.calls.append((method, params))
        return self._responses.pop(0)

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        pass


def _fake_ctx(ui: Any = None, has_ui: bool = True, entry_count: int = 5):
    entries = [SimpleNamespace()] * entry_count
    return SimpleNamespace(
        has_ui=has_ui,
        ui=ui or _ScriptedUI([]),
        session_manager=SimpleNamespace(get_entries=lambda: entries),
    )


def _setup(module):
    api = _FakeNovaAPI()
    module.extension(api)
    return api


# ---------------------------------------------------------------------------
# 事件注册
# ---------------------------------------------------------------------------


def test_registers_both_events():
    """session_before_switch 与 session_before_fork 两事件都注册。"""
    module = _load_extension()
    api = _setup(module)
    assert "session_before_switch" in api.handlers
    assert "session_before_fork" in api.handlers


# ---------------------------------------------------------------------------
# session_before_switch
# ---------------------------------------------------------------------------


def test_switch_confirmed_passes():
    """用户确认（confirm True）：放行（不返回）。"""
    module = _load_extension()
    api = _setup(module)
    ui = _ScriptedUI([UIResponse(value=True)])
    ctx = _fake_ctx(ui=ui)

    event = SimpleNamespace(reason="new", target_session_file=None)
    result = _run(api.handlers["session_before_switch"](event, ctx))

    assert result is None
    # 文案含动作与条目数
    method, params = ui.calls[0]
    assert method == "confirm"
    assert "新建会话" in params["title"]
    assert "5 条条目" in params["message"]


def test_switch_declined_cancels():
    """用户选否：返回 cancel=True（runtime 据此取消本次切换）。"""
    module = _load_extension()
    api = _setup(module)
    ui = _ScriptedUI([UIResponse(value=False)])
    ctx = _fake_ctx(ui=ui)

    event = SimpleNamespace(reason="resume", target_session_file="/s/a.jsonl")
    result = _run(api.handlers["session_before_switch"](event, ctx))

    assert result is not None
    assert result.cancel is True


def test_switch_cancelled_dialog_cancels():
    """用户取消对话框（未作答）：同样按取消处理（fail-closed）。"""
    module = _load_extension()
    api = _setup(module)
    ui = _ScriptedUI([UIResponse(cancelled=True)])
    ctx = _fake_ctx(ui=ui)

    event = SimpleNamespace(reason="new", target_session_file=None)
    result = _run(api.handlers["session_before_switch"](event, ctx))

    assert result is not None
    assert result.cancel is True


# ---------------------------------------------------------------------------
# session_before_fork
# ---------------------------------------------------------------------------


def test_fork_confirmed_passes():
    """分叉确认通过：放行。"""
    module = _load_extension()
    api = _setup(module)
    ui = _ScriptedUI([UIResponse(value=True)])
    ctx = _fake_ctx(ui=ui)

    event = SimpleNamespace(entry_id="e1", position="at")
    result = _run(api.handlers["session_before_fork"](event, ctx))

    assert result is None
    assert "分叉会话" in ui.calls[0][1]["title"]


def test_fork_declined_cancels():
    """分叉选否：cancel=True。"""
    module = _load_extension()
    api = _setup(module)
    ui = _ScriptedUI([UIResponse(value=False)])
    ctx = _fake_ctx(ui=ui)

    event = SimpleNamespace(entry_id="e1", position="at")
    result = _run(api.handlers["session_before_fork"](event, ctx))

    assert result is not None
    assert result.cancel is True


# ---------------------------------------------------------------------------
# 放行边界
# ---------------------------------------------------------------------------


def test_empty_session_passes_without_prompt():
    """entry_count=0（空会话）：不拦直接放行（不弹确认）。"""
    module = _load_extension()
    api = _setup(module)
    ui = _ScriptedUI([])
    ctx = _fake_ctx(ui=ui, entry_count=0)

    event = SimpleNamespace(reason="new", target_session_file=None)
    assert _run(api.handlers["session_before_switch"](event, ctx)) is None
    assert ui.calls == []

    fork_event = SimpleNamespace(entry_id="e1", position="at")
    assert _run(api.handlers["session_before_fork"](fork_event, ctx)) is None
    assert ui.calls == []


def test_headless_passes_without_prompt():
    """headless（无 UI）：放行（不弹确认）。"""
    module = _load_extension()
    api = _setup(module)
    ctx = _fake_ctx(has_ui=False)

    event = SimpleNamespace(reason="resume", target_session_file="/s/a.jsonl")
    assert _run(api.handlers["session_before_switch"](event, ctx)) is None

    fork_event = SimpleNamespace(entry_id="e1", position="at")
    assert _run(api.handlers["session_before_fork"](fork_event, ctx)) is None
