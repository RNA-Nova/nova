"""第三轮 RPC 补面与修复的回归测试：

- session 域：switchSession / cloneSession / exportSession / importSession；
- model 域：setThinkingLevel 严校验 / cycleThinkingLevel；
- system 域：getShortcuts / invokeShortcut；
- settings/auth 域：无会话（runtime=None）时 getSettings/getAuthStatus 可用；
- package 域：缺参显式 INVALID_PARAMS。
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from nova_ai import ModelThinkingLevel

from nova_harness.core.rpc.protocol import JSONRPCError, MethodRegistry
from nova_harness.core.rpc.protocol.methods import auth as auth_methods
from nova_harness.core.rpc.protocol.methods import model as model_methods
from nova_harness.core.rpc.protocol.methods import package as package_methods
from nova_harness.core.rpc.protocol.methods import session as session_methods
from nova_harness.core.rpc.protocol.methods import settings as settings_methods
from nova_harness.core.rpc.protocol.methods import system as system_methods
from nova_harness.core.rpc.protocol.methods.state import ServerState


def _make_state(runtime: Any = None) -> ServerState:
    state = ServerState(ui_context=SimpleNamespace())
    if runtime is not None:
        state.set_runtime(runtime)
    return state


def _dispatch(registry: MethodRegistry):
    async def _call(method: str, params: Optional[Dict[str, Any]] = None):
        msg = SimpleNamespace(method=method, params=params or {}, id=1)
        return await registry.dispatch(msg)

    return _call


# ---------------------------------------------------------------------------
# session 域：switchSession / cloneSession / exportSession / importSession
# ---------------------------------------------------------------------------


class _MgmtSession:
    def __init__(self):
        self.session_id = "s-1"
        self.session_name = "demo"
        self.session_file = "/tmp/s-1.jsonl"
        self.calls: List[tuple] = []

    async def clone_session(self):
        self.calls.append(("clone",))
        return {"cancelled": False}

    async def export_session(self, path):
        self.calls.append(("export", path))
        return {"exported_to": path}

    async def import_session(self, path, cwd_override=None):
        self.calls.append(("import", path, cwd_override))
        return {"cancelled": False}


class _MgmtRuntime:
    def __init__(self, session, switch_result=None):
        self.session = session
        self._switch_result = switch_result or {"cancelled": False}
        self.switched_to: List[str] = []

    async def switch_session(self, path):
        self.switched_to.append(path)
        return self._switch_result


def _session_registry(runtime) -> Any:
    state = _make_state(runtime)
    reg = MethodRegistry()
    session_methods.register(reg, state)
    return reg


@pytest.mark.asyncio
async def test_switch_session_requires_params():
    reg = _session_registry(_MgmtRuntime(_MgmtSession()))
    resp = await _dispatch(reg)("switchSession", {})
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.INVALID_PARAMS


@pytest.mark.asyncio
async def test_switch_session_by_path():
    runtime = _MgmtRuntime(_MgmtSession())
    reg = _session_registry(runtime)
    resp = await _dispatch(reg)("switchSession", {"path": "/tmp/other.jsonl"})
    assert resp.error is None
    assert runtime.switched_to == ["/tmp/other.jsonl"]
    assert resp.result["ok"] is True
    assert resp.result["sessionId"] == "s-1"


@pytest.mark.asyncio
async def test_switch_session_unknown_id():
    runtime = _MgmtRuntime(_MgmtSession())
    reg = _session_registry(runtime)
    resp = await _dispatch(reg)(
        "switchSession", {"sessionId": "nope", "cwd": "/nonexistent-dir-xyz"}
    )
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.SESSION_NOT_FOUND


@pytest.mark.asyncio
async def test_switch_session_cancelled():
    runtime = _MgmtRuntime(_MgmtSession(), switch_result={"cancelled": True})
    reg = _session_registry(runtime)
    resp = await _dispatch(reg)("switchSession", {"path": "/tmp/x.jsonl"})
    assert resp.error is None
    assert resp.result["ok"] is False
    assert resp.result["cancelled"] is True


@pytest.mark.asyncio
async def test_clone_session():
    session = _MgmtSession()
    reg = _session_registry(_MgmtRuntime(session))
    resp = await _dispatch(reg)("cloneSession")
    assert resp.error is None
    assert session.calls == [("clone",)]
    assert resp.result["ok"] is True


@pytest.mark.asyncio
async def test_export_session_requires_path():
    reg = _session_registry(_MgmtRuntime(_MgmtSession()))
    resp = await _dispatch(reg)("exportSession", {})
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.INVALID_PARAMS


@pytest.mark.asyncio
async def test_export_and_import_session():
    session = _MgmtSession()
    reg = _session_registry(_MgmtRuntime(session))
    resp = await _dispatch(reg)("exportSession", {"path": "/tmp/out.jsonl"})
    assert resp.error is None
    assert resp.result["exportedTo"] == "/tmp/out.jsonl"

    resp = await _dispatch(reg)("importSession", {"path": "/tmp/in.jsonl"})
    assert resp.error is None
    assert resp.result["ok"] is True
    assert ("import", "/tmp/in.jsonl", None) in session.calls


# ---------------------------------------------------------------------------
# model 域：setThinkingLevel 严校验 / cycleThinkingLevel
# ---------------------------------------------------------------------------


class _ModelSession:
    def __init__(self, next_level=ModelThinkingLevel.HIGH):
        self.model_runtime = SimpleNamespace()
        self.set_levels: List[Any] = []
        self._next_level = next_level

    async def set_thinking_level(self, level):
        self.set_levels.append(level)

    async def cycle_thinking_level(self):
        return self._next_level


def _model_registry(session) -> Any:
    state = _make_state(SimpleNamespace(session=session))
    reg = MethodRegistry()
    model_methods.register(reg, state)
    return reg


@pytest.mark.asyncio
async def test_set_thinking_level_rejects_invalid():
    reg = _model_registry(_ModelSession())
    resp = await _dispatch(reg)("setThinkingLevel", {"level": "banana"})
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.INVALID_PARAMS


@pytest.mark.asyncio
async def test_set_thinking_level_valid():
    session = _ModelSession()
    reg = _model_registry(session)
    resp = await _dispatch(reg)("setThinkingLevel", {"level": "high"})
    assert resp.error is None
    assert session.set_levels == [ModelThinkingLevel.HIGH]
    assert resp.result["thinkingLevel"] == "high"


@pytest.mark.asyncio
async def test_cycle_thinking_level():
    reg = _model_registry(_ModelSession())
    resp = await _dispatch(reg)("cycleThinkingLevel")
    assert resp.error is None
    assert resp.result == {"ok": True, "thinkingLevel": "high", "reason": None}


@pytest.mark.asyncio
async def test_cycle_thinking_level_unsupported():
    reg = _model_registry(_ModelSession(next_level=None))
    resp = await _dispatch(reg)("cycleThinkingLevel")
    assert resp.error is None
    assert resp.result["ok"] is False


# ---------------------------------------------------------------------------
# system 域：getShortcuts / invokeShortcut
# ---------------------------------------------------------------------------


def _system_registry(runner) -> Any:
    session = SimpleNamespace(extension_runner=runner)
    state = _make_state(SimpleNamespace(session=session))
    reg = MethodRegistry()
    system_methods.register(reg, state)
    return reg


@pytest.mark.asyncio
async def test_get_shortcuts_empty_without_runtime():
    state = _make_state()
    reg = MethodRegistry()
    system_methods.register(reg, state)
    resp = await _dispatch(reg)("getShortcuts")
    assert resp.error is None
    assert resp.result == {"shortcuts": []}


@pytest.mark.asyncio
async def test_get_shortcuts_lists_registry():
    shortcut = SimpleNamespace(
        shortcut="ctrl+x", description="do X", extension_path="/ext/x.py"
    )
    runner = SimpleNamespace(get_shortcuts=lambda: {"ctrl+x": shortcut})
    reg = _system_registry(runner)
    resp = await _dispatch(reg)("getShortcuts")
    assert resp.error is None
    assert resp.result["shortcuts"] == [
        {"shortcut": "ctrl+x", "description": "do X", "extensionPath": "/ext/x.py"}
    ]


@pytest.mark.asyncio
async def test_invoke_shortcut_dispatch():
    invoked: List[str] = []

    async def _invoke(key):
        invoked.append(key)
        return True

    runner = SimpleNamespace(invoke_shortcut=_invoke)
    reg = _system_registry(runner)
    resp = await _dispatch(reg)("invokeShortcut", {"shortcut": "Ctrl+X"})
    assert resp.error is None
    assert invoked == ["Ctrl+X"]
    assert resp.result["ok"] is True


@pytest.mark.asyncio
async def test_invoke_shortcut_requires_param():
    reg = _system_registry(SimpleNamespace())
    resp = await _dispatch(reg)("invokeShortcut", {})
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.INVALID_PARAMS


# ---------------------------------------------------------------------------
# settings/auth 域：无会话可用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_settings_without_runtime(monkeypatch):
    from nova_harness.core.config.settings.manager import SettingsManager
    from tests._helpers.settings_storage import InMemorySettingsStorage

    fallback = SettingsManager.from_storage(InMemorySettingsStorage())
    fallback.set_default_provider("volcengine")
    fallback.flush_sync()

    created: List[Dict[str, Any]] = []

    def _create(**kwargs):
        created.append(kwargs)
        return fallback

    monkeypatch.setattr(SettingsManager, "create", staticmethod(_create))

    state = _make_state()
    reg = MethodRegistry()
    settings_methods.register(reg, state)

    resp = await _dispatch(reg)("getSettings", {"cwd": "/tmp"})
    assert resp.error is None
    assert resp.result["settings"]["defaultProvider"] == "volcengine"
    # fallback 管理器被缓存复用（避免每次调用新建后台写线程）
    assert state.fallback_settings_manager is fallback
    await _dispatch(reg)("getSettings")
    assert len(created) == 1


@pytest.mark.asyncio
async def test_get_auth_status_without_runtime(monkeypatch):
    from nova_harness.core.config import AuthStorage

    class _FakeStorage:
        async def list(self):
            return [SimpleNamespace(provider_id="volcengine", type="api_key")]

    monkeypatch.setattr(AuthStorage, "create", staticmethod(lambda: _FakeStorage()))

    state = _make_state()
    reg = MethodRegistry()
    auth_methods.register(reg, state)
    resp = await _dispatch(reg)("getAuthStatus")
    assert resp.error is None
    assert resp.result["credentials"] == [{"provider": "volcengine", "type": "api_key"}]


# ---------------------------------------------------------------------------
# package 域：缺参显式 INVALID_PARAMS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pkg_methods_require_params():
    state = _make_state()
    reg = MethodRegistry()
    package_methods.register(reg, state)

    for method, key in (
        ("pkgInstall", "source"),
        ("pkgUninstall", "name_or_source"),
        ("pkgInfo", "name_or_source"),
        ("pkgUpdate", "name_or_source"),
    ):
        resp = await _dispatch(reg)(method, {})
        assert resp.error is not None, method
        assert resp.error["code"] == JSONRPCError.INVALID_PARAMS, method
