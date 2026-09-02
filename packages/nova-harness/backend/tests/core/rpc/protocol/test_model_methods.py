"""model 域 RPC 方法测试：listModels / setModel / setThinkingLevel /
cycleModel / listScopedModels / setScopedModels。"""

from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
from nova_ai import Model

from nova_harness.core.rpc.protocol import MethodRegistry
from nova_harness.core.rpc.protocol.methods import model as model_methods
from nova_harness.core.rpc.protocol.methods.state import ServerState


def _model(model_id: str, provider: str = "volcengine") -> Model:
    return Model(
        id=model_id,
        name=model_id.upper(),
        api="openai-completions",
        provider=provider,
        base_url="https://example.test",
        reasoning=False,
        input_types=["text"],
        cost={},
        context_window=128000,
        max_tokens=8192,
    )


class FakeModelRuntime:
    """模型运行时替身：内置全量 + 可用快照 + find。"""

    def __init__(self):
        self._all = [_model("m1"), _model("m2"), _model("other", provider="kimi")]
        self._available = [self._all[0], self._all[2]]

    def get_all(self):
        return list(self._all)

    def get_available_snapshot(self):
        return list(self._available)

    def find(self, provider: str, model_id: str):
        for m in self._all:
            if m.provider == provider and m.id == model_id:
                return m
        return None


class FakeSession:
    def __init__(self):
        self.model_runtime = FakeModelRuntime()
        self.calls: list = []
        self.scoped_models: list = []
        self._cycle_result = None

    async def set_model(self, model):
        self.calls.append(("set_model", model.id))
        return True

    async def set_thinking_level(self, level):
        self.calls.append(("set_thinking_level", level))

    async def cycle_model(self, direction="forward"):
        self.calls.append(("cycle_model", direction))
        return self._cycle_result

    def set_scoped_models(self, scoped):
        self.calls.append(("set_scoped_models", len(scoped)))
        self.scoped_models = list(scoped)


class FakeRuntime:
    def __init__(self, session):
        self.session = session


@pytest.fixture
def registry():
    session = FakeSession()
    state = ServerState(ui_context=SimpleNamespace())
    state.set_runtime(FakeRuntime(session))
    reg = MethodRegistry()
    model_methods.register(reg, state)
    return reg, session


async def _call(registry, method: str, params: Optional[Dict[str, Any]] = None):
    msg = SimpleNamespace(method=method, params=params or {}, id=1)
    resp = await registry.dispatch(msg)
    assert resp is not None
    return resp


def _result(resp) -> Dict[str, Any]:
    assert resp.error is None, f"unexpected error: {resp.error}"
    return resp.result


@pytest.mark.asyncio
async def test_list_models_with_availability(registry):
    reg, _ = registry
    result = _result(await _call(reg, "listModels"))

    models = {m["provider"] + "/" + m["id"]: m for m in result["models"]}
    assert set(models) == {"volcengine/m1", "volcengine/m2", "kimi/other"}
    assert models["volcengine/m1"]["available"] is True
    assert models["volcengine/m2"]["available"] is False
    assert models["kimi/other"]["available"] is True


@pytest.mark.asyncio
async def test_set_model_dict_form(registry):
    """dict 形式直接 model_validate，不触文件系统。"""
    reg, session = registry
    raw = _model("m2").model_dump()
    result = _result(await _call(reg, "setModel", {"model": raw}))
    assert result["ok"] is True
    assert session.calls == [("set_model", "m2")]


@pytest.mark.asyncio
async def test_set_model_invalid_format(registry):
    reg, _ = registry
    resp = await _call(reg, "setModel", {"model": "no-slash"})
    assert resp.error is not None


@pytest.mark.asyncio
async def test_set_thinking_level(registry):
    reg, session = registry
    result = _result(await _call(reg, "setThinkingLevel", {"level": "high"}))
    assert result["ok"] is True
    level = session.calls[0][1]
    assert getattr(level, "value", level) == "high"


@pytest.mark.asyncio
async def test_cycle_model(registry):
    reg, session = registry
    session._cycle_result = SimpleNamespace(
        model=_model("m2"),
        thinking_level=SimpleNamespace(value="medium"),
        is_scoped=True,
    )
    result = _result(await _call(reg, "cycleModel", {"direction": "backward"}))
    assert result["ok"] is True
    assert result["model"] == {"provider": "volcengine", "id": "m2"}
    assert result["isScoped"] is True
    assert session.calls == [("cycle_model", "backward")]


@pytest.mark.asyncio
async def test_scoped_models_roundtrip(registry):
    reg, session = registry
    result = _result(
        await _call(
            reg,
            "setScopedModels",
            {
                "models": [
                    {"provider": "volcengine", "modelId": "m1"},
                    {"provider": "kimi", "modelId": "other", "thinkingLevel": "high"},
                ]
            },
        )
    )
    assert result["ok"] is True
    assert result["count"] == 2
    assert session.calls == [("set_scoped_models", 2)]

    listed = _result(await _call(reg, "listScopedModels"))
    assert [m["id"] for m in listed["models"]] == ["m1", "other"]
    assert listed["models"][1]["thinkingLevel"] == "high"


@pytest.mark.asyncio
async def test_set_scoped_models_unknown_model(registry):
    reg, _ = registry
    resp = await _call(
        reg,
        "setScopedModels",
        {"models": [{"provider": "volcengine", "modelId": "nope"}]},
    )
    assert resp.error is not None
