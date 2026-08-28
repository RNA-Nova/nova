"""plan_mode 扩展测试。

纯函数（pi 移植规则）：bash 双判（危险否决+白名单）、计划提取、步骤清洗、
DONE 标记。handler 流：切换工具集、bash 拦截、上下文注入/过滤、turn_end
完成标记、agent_end 执行路径、session_start 条目重建。
"""

import asyncio
import importlib.util
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set
from unittest.mock import AsyncMock, Mock

from nova_harness.core.types.ui.primitives import UIResponse


def _load_extension():
    """按路径加载 plan_mode.py（对齐 test_session_commands.py 的加载方式）。"""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "plan_mode.py"
    )
    spec = importlib.util.spec_from_file_location("_test_ext_plan_mode", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


class _FakeNovaAPI:
    def __init__(self, flag_values=None):
        self.handlers: Dict[str, Any] = {}
        self.commands: Dict[str, Any] = {}
        self.shortcuts: Dict[str, Any] = {}
        self.flags: Dict[str, Any] = {}
        self._flag_values = flag_values or {}

    def on(self, event_type, handler):
        self.handlers[event_type] = handler
        return lambda: None

    def registerCommand(self, name, options=None):
        self.commands[name] = options or {}

    def registerShortcut(self, shortcut, options=None):
        self.shortcuts[shortcut] = options or {}

    def registerFlag(self, name, options=None):
        self.flags[name] = options or {}
        self._flag_values.setdefault(name, (options or {}).get("default"))

    def getFlag(self, name):
        return self._flag_values.get(name)


class _ScriptedUI:
    """剧本式 UI（select/input 应答队列 + notify 记录）。"""

    def __init__(self, responses: List[UIResponse]):
        self._responses = list(responses)
        self.calls: List[tuple] = []
        self.notifies: List[tuple] = []

    @property
    def capabilities(self) -> Set[str]:
        return {"select", "input", "notify", "set_status"}

    def has_capability(self, method: str) -> bool:
        return method in self.capabilities

    async def request(
        self, method: str, params: Dict[str, Any], signal: Any = None
    ) -> UIResponse:
        self.calls.append((method, params))
        return self._responses.pop(0)

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        self.notifies.append((method, params))


def _fake_ctx(ui: Optional[_ScriptedUI] = None, has_ui: bool = True, entries=None):
    active_tools = ["read", "write", "edit", "bash", "grep", "question"]
    ctx = SimpleNamespace(
        has_ui=has_ui,
        ui=ui or _ScriptedUI([]),
        get_active_tools=lambda: list(active_tools),
        set_active_tools=lambda names: active_tools.__setitem__(
            slice(None), list(names)
        ),
        append_entry=Mock(),
        send_message=AsyncMock(),
        send_user_message=AsyncMock(),
        session_manager=(
            SimpleNamespace(get_entries=lambda: entries)
            if entries is not None
            else None
        ),
    )
    ctx._active_tools = active_tools  # 测试侧读取
    return ctx


# ---------------------------------------------------------------------------
# 纯函数：bash 双判
# ---------------------------------------------------------------------------


def test_safe_commands_allowed():
    module = _load_extension()
    for cmd in [
        "ls -la",
        "git status",
        "git diff HEAD~1",
        "rg foo src/",
        "cat a.py",
        "sed -n 1,10p f",
    ]:
        assert module.is_safe_command(cmd), cmd


def test_destructive_commands_blocked():
    module = _load_extension()
    for cmd in [
        "rm -rf /",
        "git commit -m x",
        "pip install foo",
        "echo hi > out.txt",
        "cat a >> b",
        "sudo ls",
        "npm install",
    ]:
        assert not module.is_safe_command(cmd), cmd


def test_unknown_command_blocked():
    """不在白名单也未命中危险模式：否决（白名单制——不在即不安全）。"""
    module = _load_extension()
    assert not module.is_safe_command("python script.py")
    assert not module.is_safe_command("./run.sh")


# ---------------------------------------------------------------------------
# 纯函数：计划解析
# ---------------------------------------------------------------------------


def test_extract_todo_items_from_plan_section():
    module = _load_extension()
    message = "分析完毕。\n\nPlan:\n1. Read the config files\n2. Add retry logic to runner.py\n3. Verify with tests\n"
    items = module.extract_todo_items(message)
    assert [i["step"] for i in items] == [1, 2, 3]
    # 动词前缀被清洗（"Read the..." → "Config..."，"Add retry..." → "Retry..."）
    assert items[0]["text"] == "Config files"
    assert items[1]["text"].startswith("Retry logic")
    assert all(not i["completed"] for i in items)


def test_extract_todo_items_strips_inline_markdown():
    """行内 markdown（**Add** 粗体）：pi 同款边界——[^*] 捕获遇 * 即止，
    过短的残片被丢弃（pi 原文行为，非 bug）。"""
    module = _load_extension()
    message = "Plan:\n1. **Add** retry logic\n2. Read config files\n"
    items = module.extract_todo_items(message)
    # "**Add**" 残片被丢弃，只有正常步骤保留（与 pi 逐字符一致的语义）
    assert [i["text"] for i in items] == ["Config files"]


def test_extract_todo_items_requires_plan_header():
    module = _load_extension()
    assert module.extract_todo_items("1. Read\n2. Write") == []


def test_clean_step_text_rules():
    module = _load_extension()
    assert module.clean_step_text("Use the `find` tool") == "Find tool"
    assert len(module.clean_step_text("x" * 100)) == 50


def test_done_marks_completed():
    module = _load_extension()
    items = [
        {"step": 1, "text": "a", "completed": False},
        {"step": 2, "text": "b", "completed": False},
    ]
    assert module.mark_completed_steps("done with step [DONE:1]", items) == 1
    assert items[0]["completed"] is True
    assert items[1]["completed"] is False


# ---------------------------------------------------------------------------
# handler 流
# ---------------------------------------------------------------------------


def test_toggle_swaps_tools_and_persists():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _fake_ctx()

    _run(api.commands["plan"]["handler"]("", ctx))
    assert ctx._active_tools == [
        "read",
        "bash",
        "grep",
        "question",
    ]  # edit/write 被移除

    _run(api.commands["plan"]["handler"]("", ctx))
    assert ctx._active_tools == ["read", "write", "edit", "bash", "grep", "question"]


def test_tool_call_blocks_unsafe_bash_in_plan_mode():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _fake_ctx()
    _run(api.commands["plan"]["handler"]("", ctx))

    unsafe = SimpleNamespace(tool_name="bash", args={"command": "rm -rf /"})
    result = _run(api.handlers["tool_call"](unsafe, ctx))
    assert result.block is True
    assert "Plan mode" in result.reason

    safe = SimpleNamespace(tool_name="bash", args={"command": "git status"})
    assert _run(api.handlers["tool_call"](safe, ctx)) is None

    # 非 bash 工具不拦（edit/write 已从激活集移除，模型调不到）
    other = SimpleNamespace(tool_name="read", args={"path": "/x"})
    assert _run(api.handlers["tool_call"](other, ctx)) is None


def test_before_agent_start_injects_plan_context():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _fake_ctx()
    _run(api.commands["plan"]["handler"]("", ctx))

    result = _run(api.handlers["before_agent_start"](SimpleNamespace(), ctx))
    assert result is not None
    assert result.message.custom_type == "plan-mode-context"
    assert "PLAN MODE ACTIVE" in result.message.content
    assert result.message.display is False


def test_context_filters_stale_plan_messages_when_disabled():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _fake_ctx()

    from nova_ai import UserMessage

    from nova_harness.core.types.messages import CustomMessage

    stale = CustomMessage(
        custom_type="plan-mode-context",
        content="[PLAN MODE ACTIVE] ...",
        display=False,
        timestamp=0,
    )
    normal = UserMessage(content="hello")
    event = SimpleNamespace(messages=[stale, normal])
    result = _run(api.handlers["context"](event, ctx))
    assert result.messages == [normal]


def test_turn_end_marks_done_and_agent_end_completes():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _fake_ctx()
    handler_state = api.handlers

    # 手工置入执行态（经 Execute 路径之外的状态注入——借 session_start 重建通道）
    entry = SimpleNamespace(
        type="custom",
        custom_type="plan-mode",
        data={
            "enabled": False,
            "executing": True,
            "todos": [{"step": 1, "text": "写测试", "completed": False}],
            "tools_before": None,
        },
    )
    ctx.session_manager = SimpleNamespace(get_entries=lambda: [entry])
    _run(handler_state["session_start"](SimpleNamespace(), ctx))

    message = SimpleNamespace(
        role="assistant", content=[{"type": "text", "text": "完成 [DONE:1]"}]
    )
    _run(handler_state["turn_end"](SimpleNamespace(message=message), ctx))

    _run(handler_state["agent_end"](SimpleNamespace(messages=[]), ctx))
    # 全部完成 → 发 plan-complete 条目（转录卡片，不进 LLM 上下文）并清执行态
    # （其余 append_entry 调用是 _persist 的状态条目，按 entry_type 过滤）
    complete_calls = [
        c for c in ctx.append_entry.call_args_list if c[0][0] == "plan-complete"
    ]
    assert len(complete_calls) == 1
    assert "写测试" in complete_calls[0][0][1]["text"]


def test_agent_end_execute_path_restores_tools_and_triggers():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ui = _ScriptedUI([UIResponse(value="Execute the plan (track progress)")])
    ctx = _fake_ctx(ui=ui)

    # 进入规划态
    _run(api.commands["plan"]["handler"]("", ctx))
    assert "edit" not in ctx._active_tools

    # 模型产出 Plan 后 agent_end
    assistant = SimpleNamespace(
        role="assistant",
        content=[
            {
                "type": "text",
                "text": "Plan:\n1. Read config files\n2. Add retry logic\n",
            }
        ],
    )
    _run(api.handlers["agent_end"](SimpleNamespace(messages=[assistant]), ctx))

    # Execute：工具恢复 + 执行态 + 两条消息（计划清单 + 触发执行的 exec 消息）
    assert "edit" in ctx._active_tools
    assert ctx.send_message.await_count == 2
    exec_call = ctx.send_message.await_args_list[1]
    assert exec_call[0][0]["custom_type"] == "plan-mode-execute"
    assert exec_call[0][1]["triggerTurn"] is True


def test_session_start_restores_from_entries():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    entry = SimpleNamespace(
        type="custom",
        custom_type="plan-mode",
        data={
            "enabled": True,
            "executing": False,
            "todos": [],
            "tools_before": ["read", "write", "edit", "bash"],
        },
    )
    ctx = _fake_ctx(entries=[entry])
    _run(api.handlers["session_start"](SimpleNamespace(), ctx))
    # 规划态重建：edit/write 再次被禁用
    assert ctx._active_tools == ["read", "bash"]


def test_plan_flag_enables_on_start():
    module = _load_extension()
    api = _FakeNovaAPI(flag_values={"plan": True})
    module.extension(api)
    ctx = _fake_ctx(entries=[])
    _run(api.handlers["session_start"](SimpleNamespace(), ctx))
    assert "edit" not in ctx._active_tools


# ---------------------------------------------------------------------------
# footer 状态条（set_status 命名通知——pi setStatus 对位）
# ---------------------------------------------------------------------------


def _status_notifies(ui):
    return [(m, p) for m, p in ui.notifies if m == "set_status"]


def test_status_set_and_cleared_on_toggle():
    """开启 → ⏸ plan；关闭 → 空文本清除。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ui = _ScriptedUI([])
    ctx = _fake_ctx(ui=ui)

    _run(api.commands["plan"]["handler"]("", ctx))
    assert _status_notifies(ui) == [
        ("set_status", {"key": "plan-mode", "text": "⏸ plan"})
    ]

    _run(api.commands["plan"]["handler"]("", ctx))
    assert _status_notifies(ui)[-1] == ("set_status", {"key": "plan-mode", "text": ""})


def test_status_progress_during_execution():
    """执行态：📋 completed/total 随 [DONE:n] 更新；全部完成清除。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ui = _ScriptedUI([])
    ctx = _fake_ctx(ui=ui)

    entry = SimpleNamespace(
        type="custom",
        custom_type="plan-mode",
        data={
            "enabled": False,
            "executing": True,
            "todos": [
                {"step": 1, "text": "写测试", "completed": False},
                {"step": 2, "text": "修 bug", "completed": False},
            ],
            "tools_before": None,
        },
    )
    ctx.session_manager = SimpleNamespace(get_entries=lambda: [entry])
    _run(api.handlers["session_start"](SimpleNamespace(), ctx))
    assert _status_notifies(ui)[-1] == (
        "set_status",
        {"key": "plan-mode", "text": "📋 0/2"},
    )

    message = SimpleNamespace(
        role="assistant", content=[{"type": "text", "text": "完成 [DONE:1]"}]
    )
    _run(api.handlers["turn_end"](SimpleNamespace(message=message), ctx))
    assert _status_notifies(ui)[-1] == (
        "set_status",
        {"key": "plan-mode", "text": "📋 1/2"},
    )

    message2 = SimpleNamespace(
        role="assistant", content=[{"type": "text", "text": "完成 [DONE:2]"}]
    )
    _run(api.handlers["turn_end"](SimpleNamespace(message=message2), ctx))
    _run(api.handlers["agent_end"](SimpleNamespace(messages=[]), ctx))
    assert _status_notifies(ui)[-1] == ("set_status", {"key": "plan-mode", "text": ""})
