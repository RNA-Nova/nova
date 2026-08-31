"""tools_panel 扩展测试（/tools 交互开关面板——pi tools.ts 对位）。

覆盖：/tools dialog 应答应用 + append_entry 持久化 + command_result 反馈
条目、cancelled 无操作、无 UI 文本清单回退、session_start/session_tree
从分支条目恢复（有/无两态）。
"""

import asyncio
import importlib.util
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set

from nova_harness.types.ui.primitives import UIResponse


def _load_extension():
    """按路径加载 tools_panel.py（对齐 test_plan_mode.py 的加载方式）。"""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "tools_panel.py"
    )
    spec = importlib.util.spec_from_file_location("_test_ext_tools_panel", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


class _FakeNovaAPI:
    def __init__(self):
        self.handlers: Dict[str, Any] = {}
        self.commands: Dict[str, Any] = {}

    def on(self, event_type, handler):
        self.handlers[event_type] = handler
        return lambda: None

    def registerCommand(self, name, options=None):
        self.commands[name] = options or {}


class _ScriptedUI:
    """剧本式 UI（request 应答队列 + 调用记录）。"""

    def __init__(self, responses: List[UIResponse]):
        self._responses = list(responses)
        self.calls: List[tuple] = []

    @property
    def capabilities(self) -> Set[str]:
        return {"dialog:tools"}

    def has_capability(self, method: str) -> bool:
        return method in self.capabilities

    async def request(
        self, method: str, params: Dict[str, Any], signal: Any = None
    ) -> UIResponse:
        self.calls.append((method, params))
        return self._responses.pop(0)

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        pass


_ALL_TOOLS = [
    {"name": "read", "description": "读文件"},
    {"name": "write", "description": "写文件"},
    {"name": "bash", "description": "跑命令"},
]


def _fake_ctx(
    ui: Optional[_ScriptedUI] = None,
    has_ui: bool = True,
    branch=None,
    role="coding_agent",
):
    active_tools = ["read", "write", "bash"]
    appended: List[tuple] = []
    ctx = SimpleNamespace(
        has_ui=has_ui,
        ui=ui or _ScriptedUI([]),
        get_all_tools=lambda: list(_ALL_TOOLS),
        get_active_tools=lambda: list(active_tools),
        set_active_tools=lambda names: active_tools.__setitem__(
            slice(None), list(names)
        ),
        append_entry=lambda t, d: appended.append((t, d)),
        session_manager=SimpleNamespace(get_branch=lambda: branch or []),
        get_agents=lambda: [{"name": role, "current": True}],
    )
    ctx._active_tools = active_tools  # 测试侧读取
    ctx._appended = appended
    return ctx


def _panel_entry(active: List[str], role: Optional[str] = "coding_agent"):
    return SimpleNamespace(
        type="custom",
        custom_type="tool-panel",
        data={"active": active, "role": role},
    )


# ---------------------------------------------------------------------------
# /tools 命令
# ---------------------------------------------------------------------------


def test_tools_dialog_answer_applies_and_persists():
    """应答 {"active": [...]}：应用绝对集 + tool-panel 持久化 + command_result 反馈。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ui = _ScriptedUI([UIResponse(value={"active": ["read", "bash"]})])
    ctx = _fake_ctx(ui=ui)

    _run(api.commands["tools"]["handler"]("", ctx))

    assert ctx._active_tools == ["read", "bash"]  # write 被关掉
    assert ctx._appended[0] == (
        "tool-panel",
        {"active": ["read", "bash"], "role": "coding_agent"},
    )
    # 反馈走 command_result 条目（转录卡片，不进 LLM 上下文）
    assert ctx._appended[1][0] == "command_result"
    assert "2/3" in ctx._appended[1][1]["text"]
    assert ctx._appended[1][1]["level"] == "info"
    # request 载荷：全部工具带 name/label/description/active
    assert ui.calls[0][0] == "dialog:tools"
    sent = ui.calls[0][1]["tools"]
    assert [t["name"] for t in sent] == ["read", "write", "bash"]
    assert all(t["active"] for t in sent)
    assert sent[0]["label"] == "read"


def test_tools_dialog_cancelled_is_noop():
    """cancelled：激活集不变、无 append_entry、无确认消息。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ui = _ScriptedUI([UIResponse(cancelled=True)])
    ctx = _fake_ctx(ui=ui)

    _run(api.commands["tools"]["handler"]("", ctx))

    assert ctx._active_tools == ["read", "write", "bash"]
    assert ctx._appended == []


def test_tools_no_ui_text_fallback():
    """无 UI：文本列出全部工具与激活状态（不弹面板）。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ui = _ScriptedUI([])
    ctx = _fake_ctx(ui=ui, has_ui=False)

    _run(api.commands["tools"]["handler"]("", ctx))

    assert ui.calls == []
    assert len(ctx._appended) == 1
    entry_type, data = ctx._appended[0]
    assert entry_type == "command_result"
    text = data["text"]
    assert "3/3 active" in text
    assert "✓ read" in text
    assert "✓ bash" in text


# ---------------------------------------------------------------------------
# 状态恢复（session_start / session_tree）
# ---------------------------------------------------------------------------


def test_session_start_restores_from_branch_entry():
    """分支最新 tool-panel 条目存在：应用其激活集。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _fake_ctx(branch=[_panel_entry(["read"])])

    _run(api.handlers["session_start"](SimpleNamespace(), ctx))
    assert ctx._active_tools == ["read"]


def test_session_start_without_entry_keeps_default():
    """分支无 tool-panel 条目：不动（默认全激活）。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _fake_ctx(branch=[])

    _run(api.handlers["session_start"](SimpleNamespace(), ctx))
    assert ctx._active_tools == ["read", "write", "bash"]


def test_session_tree_restores_latest_entry():
    """树导航后：扫当前分支最新条目应用（旧条目被新的覆盖）。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    entries = [
        _panel_entry(["read"]),
        SimpleNamespace(type="message", custom_type="", data=None),
        _panel_entry(["bash", "write"]),
    ]
    ctx = _fake_ctx(branch=entries)

    _run(api.handlers["session_tree"](SimpleNamespace(), ctx))
    assert ctx._active_tools == ["bash", "write"]


def test_session_tree_without_entry_keeps_current():
    """树导航后分支无条目：不动。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _fake_ctx(branch=[])

    _run(api.handlers["session_tree"](SimpleNamespace(), ctx))
    assert ctx._active_tools == ["read", "write", "bash"]


def test_restore_skips_entry_from_other_role():
    """条目角色与当前角色不符：不应用（面板 delta 不越界到新角色）。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _fake_ctx(branch=[_panel_entry(["read"], role="reviewer")])

    _run(api.handlers["session_start"](SimpleNamespace(), ctx))
    assert ctx._active_tools == ["read", "write", "bash"]  # 保持默认，未被覆盖


def test_restore_applies_entry_matching_role():
    """条目角色与当前角色一致：正常应用。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _fake_ctx(branch=[_panel_entry(["read"], role="coding_agent")])

    _run(api.handlers["session_start"](SimpleNamespace(), ctx))
    assert ctx._active_tools == ["read"]
