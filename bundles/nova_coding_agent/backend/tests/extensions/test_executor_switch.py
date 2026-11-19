"""executor_switch 扩展测试（/executor 命令 + 条目持久化 + 分支恢复）。

覆盖：直切 local/ws(s) 远程（名字查端点清单）、选择器切换、notice 回执、
条目持久化、分支恢复，以及旧 ssh:// 自有供给入口的退出行为。
"""

import asyncio
import importlib.util
import os

import pytest
from nova_harness.ui.primitives import UIResponse

from nova_coding_agent.executor import get_backend_selection, reset_backend_selection


@pytest.fixture(autouse=True)
def _hermetic_default_config(monkeypatch):
    """默认路径隔离：不读真实 ``~/.nova/exec-server/config.toml``。"""
    from nova_exec_policy import ExecutorConfig

    import nova_coding_agent.executor.runtime as runtime_mod

    monkeypatch.setattr(
        runtime_mod, "load_executor_config", lambda **_: ExecutorConfig()
    )


def _load_extension():
    ext_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "executor_switch.py"
    )
    spec = importlib.util.spec_from_file_location("_test_executor_switch_ext", ext_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeNovaAPI:
    def __init__(self):
        self.handlers = {}
        self.commands = {}

    def on(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)

    def register_command(self, name, options=None, **_kwargs):
        opts = options or {}
        self.commands[name] = opts["handler"]


class _FakeUI:
    def __init__(self, select_script=()):
        self._select_script = list(select_script)
        self.notices = []

    def has_capability(self, method):
        return True

    async def request(self, method, params):
        value = self._select_script.pop(0) if self._select_script else None
        return UIResponse(value=value, cancelled=value is None)

    def notify(self, method, params):
        self.notices.append((method, params))


class _FakeSessionManager:
    def __init__(self, entries=()):
        self._entries = list(entries)

    def get_branch(self):
        return list(self._entries)


class _Entry:
    def __init__(self, data):
        self.type = "custom"
        self.custom_type = "executor_backend"
        self.data = data


@pytest.fixture
def fake_endpoints(monkeypatch):
    state = {"entries": [], "unregistered": []}

    def _load():
        return list(state["entries"])

    def _unregister(name):
        state["unregistered"].append(name)
        before = len(state["entries"])
        state["entries"] = [e for e in state["entries"] if e["name"] != name]
        return len(state["entries"]) != before

    monkeypatch.setattr(
        "nova_coding_agent.executor.endpoints_store.load_endpoints", _load
    )
    monkeypatch.setattr(
        "nova_coding_agent.executor.endpoints_store.unregister_endpoint", _unregister
    )
    return state


class _FakeCtx:
    def __init__(self, ui, session_manager=None):
        self.ui = ui
        self.has_ui = True
        self.cwd = "/tmp"
        self.session_manager = session_manager
        self.entries = []
        self.refresh_calls = 0

    def append_entry(self, entry_type, data):
        self.entries.append((entry_type, data))

    def refresh_system_prompt(self):
        self.refresh_calls += 1


def _notice_texts(ui):
    return [
        params.get("message", "") for method, params in ui.notices if method == "notify"
    ]


def _setup():
    reset_backend_selection()
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    return api


def _teardown():
    reset_backend_selection()


def test_switch_local_direct():
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("local", ctx))
        assert get_backend_selection().backend == "local"
        assert ctx.entries == [("executor_backend", {"backend": "local", "url": None})]
        assert ctx.refresh_calls == 1
        assert ctx.ui.notices, "切换应有 notice 回执"
    finally:
        _teardown()


def test_switch_remote_by_url():
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote wss://gpu-01:8080", ctx))
        sel = get_backend_selection()
        assert sel.backend == "executor" and sel.url == "wss://gpu-01:8080"
    finally:
        _teardown()


def test_switch_remote_by_endpoint_name(fake_endpoints):
    fake_endpoints["entries"] = [
        {"name": "gpu-01", "url": "wss://gpu-01:8080", "cwd": None}
    ]
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote gpu-01", ctx))
        assert get_backend_selection().url == "wss://gpu-01:8080"
    finally:
        _teardown()


def test_unknown_remote_name_keeps_current(fake_endpoints):
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote alice@gpu-01", ctx))
        assert get_backend_selection().backend == "local"
        assert ctx.entries == []
        assert any("未知端点" in text for text in _notice_texts(ctx.ui))
    finally:
        _teardown()


def test_ssh_product_scheme_is_rejected():
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote ssh://alice@gpu-01", ctx))
        assert get_backend_selection().backend == "local"
        assert ctx.entries == []
        assert any("仅支持 ws" in text for text in _notice_texts(ctx.ui))
    finally:
        _teardown()


def test_selector_cancel_keeps_current():
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI(select_script=[None]))
        before = get_backend_selection().backend
        asyncio.run(api.commands["executor"]("", ctx))
        assert get_backend_selection().backend == before
        assert ctx.entries == []
    finally:
        _teardown()


def test_selector_pick_executor_local():
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI(select_script=["executor-local"]))
        asyncio.run(api.commands["executor"]("", ctx))
        sel = get_backend_selection()
        assert sel.backend == "executor" and sel.url is None
    finally:
        _teardown()


def test_selector_pick_ws_endpoint(fake_endpoints):
    fake_endpoints["entries"] = [
        {"name": "gpu", "url": "wss://gpu-01:8080", "cwd": None}
    ]
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI(select_script=["endpoint:gpu"]))
        asyncio.run(api.commands["executor"]("", ctx))
        assert get_backend_selection().url == "wss://gpu-01:8080"
    finally:
        _teardown()


def test_forget_endpoint(fake_endpoints):
    fake_endpoints["entries"] = [
        {"name": "gpu-01", "url": "wss://gpu-01:8080", "cwd": None}
    ]
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("forget gpu-01", ctx))
        assert fake_endpoints["unregistered"] == ["gpu-01"]
        assert any("已移除端点：gpu-01" in text for text in _notice_texts(ctx.ui))
    finally:
        _teardown()


def test_restore_from_branch_entry():
    api = _setup()
    try:
        sm = _FakeSessionManager(
            [_Entry({"backend": "executor", "url": "wss://gpu-02:9000"})]
        )
        ctx = _FakeCtx(_FakeUI(), session_manager=sm)
        api.handlers["session_start"][0](None, ctx)
        sel = get_backend_selection()
        assert sel.backend == "executor" and sel.url == "wss://gpu-02:9000"
        assert ctx.refresh_calls == 1
    finally:
        _teardown()


def test_restore_legacy_ssh_entry_falls_back_to_local():
    api = _setup()
    try:
        sm = _FakeSessionManager(
            [
                _Entry(
                    {
                        "backend": "executor",
                        "url": "ssh://alice@gpu-01",
                        "remote_cwd": "/home/alice/workspaces/s1",
                        "remote_shell": "bash",
                    }
                )
            ]
        )
        ctx = _FakeCtx(_FakeUI(), session_manager=sm)
        api.handlers["session_start"][0](None, ctx)
        assert get_backend_selection().backend == "local"
        assert ctx.refresh_calls == 1
    finally:
        _teardown()
