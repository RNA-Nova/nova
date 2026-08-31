"""permission_gate 扩展的单元测试。

覆盖：bash 危险命令的 UI/无 UI 分支、Always 会话记忆、write/edit 保护路径、
无关工具与安全命令的零拦截、防御性分支（缺参数/非 dict args）。
"""

import asyncio
import importlib.util
import os
from types import SimpleNamespace
from unittest.mock import Mock


def _load_extension():
    """动态加载 permission_gate extension 模块。"""
    ext_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "permission_gate.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_test_permission_gate_extension", ext_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


class _FakeNovaAPI:
    """模拟 NovaExtensionAPI，捕获 on() 注册的 handler。"""

    def __init__(self):
        self.handlers = {}

    def on(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)


class _FakeUI:
    """模拟泛型 UIContext：request 按脚本返回，notify 记录。"""

    def __init__(self, select_script=()):
        self._select_script = list(select_script)
        self.select_calls = []
        self.notifications = []

    def has_capability(self, method):
        return True

    async def request(self, method, params):
        assert method == "select"
        self.select_calls.append(params)
        value = self._select_script.pop(0) if self._select_script else None
        return SimpleNamespace(value=value, cancelled=value is None, confirmed=None)

    def notify(self, method, params):
        assert method == "notify"
        self.notifications.append((params.get("message"), params.get("type")))


def _make_handler(ui=None, has_ui=False):
    """加载扩展并返回注册到 tool_call 的 handler。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    handler = api.handlers["tool_call"][0]
    ctx = SimpleNamespace(
        has_ui=has_ui,
        ui=ui or _FakeUI(),
        append_entry=Mock(),
    )
    return handler, ctx


def _event(tool_name, args):
    return SimpleNamespace(tool_name=tool_name, args=args)


# -----------------------------------------------------------------------------
# bash 危险命令
# -----------------------------------------------------------------------------


def test_dangerous_bash_no_ui_blocks():
    """无 UI（headless）时危险命令直接 block（fail-closed）。"""
    handler, ctx = _make_handler(has_ui=False)
    result = _run(handler(_event("bash", {"command": "rm -rf /tmp/x"}), ctx))
    assert result is not None
    assert result.block is True
    assert "no UI for confirmation" in result.reason


def test_dangerous_bash_ui_yes_allows():
    ui = _FakeUI(select_script=["Yes"])
    handler, ctx = _make_handler(ui=ui, has_ui=True)
    result = _run(handler(_event("bash", {"command": "sudo ls"}), ctx))
    assert result is None
    assert len(ui.select_calls) == 1


def test_dangerous_bash_ui_no_blocks():
    ui = _FakeUI(select_script=["No"])
    handler, ctx = _make_handler(ui=ui, has_ui=True)
    result = _run(handler(_event("bash", {"command": "sudo ls"}), ctx))
    assert result is not None
    assert result.block is True
    assert result.reason == "Blocked by user"


def test_dangerous_bash_ui_cancel_blocks():
    """select 返回 None（取消/不支持）按拒绝处理（fail-closed）。"""
    ui = _FakeUI(select_script=[None])
    handler, ctx = _make_handler(ui=ui, has_ui=True)
    result = _run(handler(_event("bash", {"command": "chmod 777 x"}), ctx))
    assert result is not None
    assert result.block is True


def test_always_remembered_per_exact_command():
    """Always 后同一精确命令串不再询问；不同命令仍要问。"""
    ui = _FakeUI(select_script=["Always", "Yes"])
    handler, ctx = _make_handler(ui=ui, has_ui=True)

    assert _run(handler(_event("bash", {"command": "sudo make install"}), ctx)) is None
    # 同一精确命令：直接放行，select 不再调用
    assert _run(handler(_event("bash", {"command": "sudo make install"}), ctx)) is None
    assert len(ui.select_calls) == 1
    # 不同命令（即使同模式）：仍要询问
    assert _run(handler(_event("bash", {"command": "sudo apt update"}), ctx)) is None
    assert len(ui.select_calls) == 2


def test_dangerous_patterns_all_match():
    """pi 同款三条正则的全部形态。"""
    handler, ctx = _make_handler(has_ui=False)
    dangerous = [
        "rm -rf /",
        "rm -r /tmp/x",
        "rm --recursive /tmp/x",
        "sudo apt install",
        "SUDO ls",
        "chmod 777 file",
        "chown -R 777 dir",
    ]
    for command in dangerous:
        result = _run(handler(_event("bash", {"command": command}), ctx))
        assert result is not None and result.block is True, command


def test_safe_bash_untouched():
    """安全命令零拦截，select 不调用。"""
    ui = _FakeUI()
    handler, ctx = _make_handler(ui=ui, has_ui=True)
    for command in ["ls -la", "rm file.txt", "git status", "chmod 644 x"]:
        assert _run(handler(_event("bash", {"command": command}), ctx)) is None
    assert ui.select_calls == []


# -----------------------------------------------------------------------------
# write/edit 保护路径
# -----------------------------------------------------------------------------


def test_protected_paths_block_without_asking():
    """保护路径命中直接 block（不询问），并 notify。"""
    ui = _FakeUI()
    handler, ctx = _make_handler(ui=ui, has_ui=True)
    for tool in ("write", "edit"):
        for path in ["src/.env", ".env.local", ".git/config", "app/node_modules/x.js"]:
            result = _run(handler(_event(tool, {"path": path}), ctx))
            assert result is not None and result.block is True, (tool, path)
            assert "protected" in result.reason
    assert ui.select_calls == []
    assert len(ui.notifications) == 8
    assert all(t == "warning" for _, t in ui.notifications)


def test_protected_paths_no_ui_still_blocks():
    handler, ctx = _make_handler(has_ui=False)
    result = _run(handler(_event("write", {"path": ".env"}), ctx))
    assert result is not None and result.block is True


def test_normal_paths_allowed():
    handler, ctx = _make_handler(has_ui=True)
    for tool in ("write", "edit"):
        for path in ["src/main.py", "README.md", "tests/test_x.py"]:
            assert _run(handler(_event(tool, {"path": path}), ctx)) is None


# -----------------------------------------------------------------------------
# 无关工具与防御分支
# -----------------------------------------------------------------------------


def test_unrelated_tools_untouched():
    handler, ctx = _make_handler(has_ui=True)
    for tool in ("read", "grep", "find", "ls"):
        assert _run(handler(_event(tool, {"path": ".env"}), ctx)) is None


def test_missing_or_invalid_args_pass_through():
    handler, ctx = _make_handler(has_ui=False)
    assert _run(handler(_event("bash", {}), ctx)) is None
    assert _run(handler(_event("bash", {"command": 123}), ctx)) is None
    assert _run(handler(_event("write", {}), ctx)) is None
    assert _run(handler(_event("bash", None), ctx)) is None


# -----------------------------------------------------------------------------
# 审批留痕（问记分离：dialog 问、custom 条目记）
# -----------------------------------------------------------------------------


def test_decision_recorded_as_permission_decision_entry():
    """allow/deny/blocked 均落 permission_decision 条目（tool/target/decision）。"""
    ui = _FakeUI(select_script=["Yes"])
    handler, ctx = _make_handler(ui=ui, has_ui=True)
    _run(handler(_event("bash", {"command": "sudo ls"}), ctx))
    ctx.append_entry.assert_called_once()
    entry_type, data = ctx.append_entry.call_args[0]
    assert entry_type == "permission_decision"
    assert data["tool"] == "bash"
    assert data["target"] == "sudo ls"
    assert data["decision"] == "allow"


def test_deny_records_reason():
    ui = _FakeUI(select_script=["No"])
    handler, ctx = _make_handler(ui=ui, has_ui=True)
    _run(handler(_event("bash", {"command": "sudo ls"}), ctx))
    data = ctx.append_entry.call_args[0][1]
    assert data["decision"] == "deny"
    assert data["reason"] == "Blocked by user"


def test_protected_path_block_recorded_without_ui():
    handler, ctx = _make_handler(has_ui=False)
    _run(handler(_event("write", {"path": ".env"}), ctx))
    data = ctx.append_entry.call_args[0][1]
    assert data["tool"] == "write/edit"
    assert data["decision"] == "blocked"


def test_safe_command_no_record():
    """未触门的调用不留痕（无噪音）。"""
    handler, ctx = _make_handler(has_ui=True)
    _run(handler(_event("bash", {"command": "ls"}), ctx))
    ctx.append_entry.assert_not_called()
