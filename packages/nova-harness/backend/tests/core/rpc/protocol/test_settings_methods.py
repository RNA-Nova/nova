"""settings 域 RPC 方法测试：getSettings / updateSettings。"""

from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest

from nova_harness.core.rpc.protocol import MethodRegistry
from nova_harness.core.rpc.protocol.methods import settings as settings_methods
from nova_harness.core.rpc.protocol.methods.state import ServerState


class FakeSettingsManager:
    """记录调用的 SettingsManager 替身（内存合并语义）。"""

    def __init__(self):
        self._data: Dict[str, Any] = {"steeringMode": "all"}
        self.updated: list = []

    def get_settings(self):
        return SimpleNamespace(
            model_dump=lambda: dict(self._data), dump_wire=lambda: dict(self._data)
        )

    def update_global_settings(self, partial):
        unknown = set(partial) - {"steeringMode", "showCacheMissNotices"}
        if unknown:
            raise ValueError(f"Unknown settings keys: {sorted(unknown)}")
        self.updated.append(partial)
        self._data.update(partial)


class FakeRuntime:
    def __init__(self, manager):
        self.services = SimpleNamespace(settings_manager=manager)


@pytest.fixture
def registry():
    manager = FakeSettingsManager()
    state = ServerState(ui_context=SimpleNamespace())
    state.set_runtime(FakeRuntime(manager))
    reg = MethodRegistry()
    settings_methods.register(reg, state)
    return reg, manager


async def _call(registry, method: str, params: Optional[Dict[str, Any]] = None):
    msg = SimpleNamespace(method=method, params=params or {}, id=1)
    resp = await registry.dispatch(msg)
    assert resp is not None
    return resp


def _result(resp) -> Dict[str, Any]:
    assert resp.error is None, f"unexpected error: {resp.error}"
    return resp.result


@pytest.mark.asyncio
async def test_get_settings(registry):
    reg, _ = registry
    result = _result(await _call(reg, "getSettings"))
    assert result["settings"] == {"steeringMode": "all"}


@pytest.mark.asyncio
async def test_update_settings(registry):
    reg, manager = registry
    result = _result(
        await _call(reg, "updateSettings", {"settings": {"showCacheMissNotices": True}})
    )
    assert result["ok"] is True
    assert manager.updated == [{"showCacheMissNotices": True}]
    assert result["settings"]["showCacheMissNotices"] is True


@pytest.mark.asyncio
async def test_update_settings_rejects_unknown_key(registry):
    reg, manager = registry
    resp = await _call(reg, "updateSettings", {"settings": {"nope": 1}})
    assert resp.error is not None
    assert manager.updated == []


@pytest.mark.asyncio
async def test_update_settings_requires_object(registry):
    reg, _ = registry
    resp = await _call(reg, "updateSettings", {"settings": "bad"})
    assert resp.error is not None


class FakePatternSettingsManager(FakeSettingsManager):
    """带资源 pattern 访问器的替身（tools / user_tools 三态数组）。"""

    def __init__(self):
        super().__init__()
        self._patterns: Dict[str, list] = {"tools": [], "user_tools": []}

    def get_tool_patterns(self):
        return list(self._patterns["tools"])

    def set_tool_patterns(self, patterns):
        self._patterns["tools"] = list(patterns)

    def get_user_tool_patterns(self):
        return list(self._patterns["user_tools"])

    def set_user_tool_patterns(self, patterns):
        self._patterns["user_tools"] = list(patterns)


@pytest.fixture
def pattern_registry():
    manager = FakePatternSettingsManager()
    state = ServerState(ui_context=SimpleNamespace())
    # runtime 无 session 属性——重解析步骤应安全跳过
    state.set_runtime(FakeRuntime(manager))
    reg = MethodRegistry()
    settings_methods.register(reg, state)
    return reg, manager


@pytest.mark.asyncio
async def test_exclude_resource_appends_bang_entry(pattern_registry):
    reg, manager = pattern_registry
    result = _result(
        await _call(reg, "excludeResource", {"resourceType": "tools", "name": "bash"})
    )
    assert result["ok"] is True
    assert result["patterns"] == ["!bash"]
    assert manager.get_tool_patterns() == ["!bash"]


@pytest.mark.asyncio
async def test_exclude_resource_idempotent(pattern_registry):
    reg, _ = pattern_registry
    await _call(reg, "excludeResource", {"resourceType": "tools", "name": "bash"})
    result = _result(
        await _call(reg, "excludeResource", {"resourceType": "tools", "name": "bash"})
    )
    assert result["patterns"] == ["!bash"]


@pytest.mark.asyncio
async def test_include_resource_removes_bang_entry(pattern_registry):
    reg, manager = pattern_registry
    await _call(reg, "excludeResource", {"resourceType": "user_tools", "name": "bash"})
    result = _result(
        await _call(
            reg, "includeResource", {"resourceType": "user_tools", "name": "bash"}
        )
    )
    assert result["patterns"] == []
    assert manager.get_user_tool_patterns() == []


@pytest.mark.asyncio
async def test_exclude_resource_rejects_unknown_type(pattern_registry):
    reg, _ = pattern_registry
    resp = await _call(reg, "excludeResource", {"resourceType": "skills", "name": "x"})
    assert resp.error is not None
