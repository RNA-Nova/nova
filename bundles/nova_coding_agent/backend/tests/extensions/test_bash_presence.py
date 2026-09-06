"""bash_presence 扩展测试。

覆盖：平台门（仅 win32）、事件 reason 门（仅 start）、无 UI 静默、bash
可解析不打扰、bash 缺失发 warning 引导；``_configured_shell_path`` 的
全局/项目合并与坏文件容错。
"""

import asyncio
import importlib.util
import json
import os
import sys
from types import SimpleNamespace

import pytest


def _load_extension():
    """按路径加载 bash_presence.py（对齐 test_interactive_shell.py）。"""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "bash_presence.py"
    )
    spec = importlib.util.spec_from_file_location("_test_ext_bash_presence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


class _FakeNovaAPI:
    def __init__(self):
        self.handlers = {}

    def on(self, event_type, handler):
        self.handlers[event_type] = handler
        return lambda: None


class _FakeUI:
    def __init__(self):
        self.notifications = []

    def notify(self, method, params):
        self.notifications.append((method, params))


def _fake_ctx(ui, has_ui=True, cwd="/ctx-cwd"):
    return SimpleNamespace(has_ui=has_ui, ui=ui, cwd=cwd)


def _event(reason="start"):
    return SimpleNamespace(reason=reason)


def _register(module):
    api = _FakeNovaAPI()
    module.extension(api)
    return api.handlers["session_start"]


# -----------------------------------------------------------------------------
# _configured_shell_path：合并 settings 的 shell_path（项目覆盖全局）
# -----------------------------------------------------------------------------


def test_shell_path_none_when_no_settings(tmp_path, monkeypatch):
    module = _load_extension()
    monkeypatch.setenv("NOVA_AGENT_DIR", str(tmp_path / "agent"))
    assert module._configured_shell_path(str(tmp_path)) is None


def test_shell_path_project_overrides_global(tmp_path, monkeypatch):
    module = _load_extension()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "settings.json").write_text(
        json.dumps({"shell_path": "C:/global/bash.exe"}), encoding="utf-8"
    )
    project = tmp_path / "proj"
    (project / ".nova").mkdir(parents=True)
    (project / ".nova" / "settings.json").write_text(
        json.dumps({"shell_path": "D:/project/bash.exe"}), encoding="utf-8"
    )
    monkeypatch.setenv("NOVA_AGENT_DIR", str(agent_dir))
    assert module._configured_shell_path(str(project)) == "D:/project/bash.exe"


def test_shell_path_tolerates_broken_files(tmp_path, monkeypatch):
    """坏 JSON / 非字符串值按未配置处理（不错过引导，也不抛）。"""
    module = _load_extension()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "settings.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("NOVA_AGENT_DIR", str(agent_dir))
    assert module._configured_shell_path(str(tmp_path)) is None


# -----------------------------------------------------------------------------
# session_start 钩子：平台 / reason / UI 门 + bash 缺失引导
# -----------------------------------------------------------------------------


def test_skips_on_non_windows(monkeypatch):
    module = _load_extension()
    handler = _register(module)
    monkeypatch.setattr(sys, "platform", "darwin")
    ui = _FakeUI()
    _run(handler(_event(), _fake_ctx(ui)))
    assert ui.notifications == []


def test_skips_on_non_start_reason(monkeypatch):
    """reload/agent_change 不重复打扰。"""
    module = _load_extension()
    handler = _register(module)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(module, "_configured_shell_path", lambda cwd: None)
    monkeypatch.setattr(
        module,
        "get_shell_config",
        lambda path: (_ for _ in ()).throw(FileNotFoundError()),
    )
    for reason in ("reload", "agent_change", "branch"):
        ui = _FakeUI()
        _run(handler(_event(reason), _fake_ctx(ui)))
        assert ui.notifications == []


def test_skips_without_ui(monkeypatch):
    module = _load_extension()
    handler = _register(module)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(module, "_configured_shell_path", lambda cwd: None)
    monkeypatch.setattr(
        module,
        "get_shell_config",
        lambda path: (_ for _ in ()).throw(FileNotFoundError()),
    )
    ui = _FakeUI()
    _run(handler(_event(), _fake_ctx(ui, has_ui=False)))
    assert ui.notifications == []


def test_no_notice_when_bash_resolves(monkeypatch):
    module = _load_extension()
    handler = _register(module)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(module, "_configured_shell_path", lambda cwd: None)
    monkeypatch.setattr(
        module, "get_shell_config", lambda path: SimpleNamespace(shell="bash")
    )
    ui = _FakeUI()
    _run(handler(_event(), _fake_ctx(ui)))
    assert ui.notifications == []


def test_warns_with_guidance_when_bash_missing(monkeypatch):
    module = _load_extension()
    handler = _register(module)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(module, "_configured_shell_path", lambda cwd: None)
    monkeypatch.setattr(
        module,
        "get_shell_config",
        lambda path: (_ for _ in ()).throw(FileNotFoundError()),
    )
    ui = _FakeUI()
    _run(handler(_event(), _fake_ctx(ui)))
    assert len(ui.notifications) == 1
    method, params = ui.notifications[0]
    assert method == "notify"
    assert params["type"] == "warning"
    assert "bash" in params["message"]
    assert "install.ps1" in params["message"]


def test_other_exceptions_propagate(monkeypatch):
    """非 FileNotFoundError 不被吞（引导提示不掩盖真实故障）。"""
    module = _load_extension()
    handler = _register(module)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(module, "_configured_shell_path", lambda cwd: None)
    monkeypatch.setattr(
        module,
        "get_shell_config",
        lambda path: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    ui = _FakeUI()
    with pytest.raises(RuntimeError, match="boom"):
        _run(handler(_event(), _fake_ctx(ui)))
