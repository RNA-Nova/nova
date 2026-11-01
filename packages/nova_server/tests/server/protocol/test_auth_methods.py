"""auth 域 RPC 方法测试：getAuthStatus / setApiKey / login / logout。"""

from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
from nova_protocol import ApiKeyCredential, CredentialInfo
from nova_server.protocol import MethodRegistry
from nova_server.protocol.methods import auth as auth_methods
from nova_server.protocol.methods.state import ServerState


class FakeAuthStorage:
    """内存版 AuthStorage 替身（sync 协议）。"""

    def __init__(self):
        self.data: Dict[str, Any] = {"volcengine": ApiKeyCredential(key="k-1")}

    def list(self):
        return [
            CredentialInfo(provider_id=pid, type=cred.type)
            for pid, cred in self.data.items()
        ]

    def write(self, provider_id, credential):
        self.data[provider_id] = credential

    def delete(self, provider_id):
        self.data.pop(provider_id, None)


class FakeModelRuntime:
    """模型运行时替身：记录 login/logout/refresh 联动。"""

    def __init__(self):
        self.calls: list = []

    async def refresh(self, *args, **kwargs):
        self.calls.append(("refresh",))

    async def set_credential(self, provider_id, credential):
        self.calls.append(("set_credential", provider_id, credential))

    async def login(self, provider_id, auth_type, interaction):
        self.calls.append(("login", provider_id, auth_type, interaction))
        return ApiKeyCredential(key="new-key")

    async def logout(self, provider_id):
        self.calls.append(("logout", provider_id))


class FakeRuntime:
    def __init__(self, session, storage):
        self.session = session
        self.services = SimpleNamespace(auth_storage=storage)


@pytest.fixture
def registry():
    model_runtime = FakeModelRuntime()
    storage = FakeAuthStorage()
    session = SimpleNamespace(model_runtime=model_runtime)
    state = ServerState(ui_context=SimpleNamespace(has_capability=lambda method: True))
    state.set_runtime(FakeRuntime(session, storage))
    reg = MethodRegistry()
    auth_methods.register(reg, state)
    return reg, storage, model_runtime


def _registry_with_capabilities(capabilities):
    """构造 ui_context 只支持指定能力集的 registry。"""
    model_runtime = FakeModelRuntime()
    storage = FakeAuthStorage()
    session = SimpleNamespace(model_runtime=model_runtime)
    state = ServerState(
        ui_context=SimpleNamespace(has_capability=lambda method: method in capabilities)
    )
    state.set_runtime(FakeRuntime(session, storage))
    reg = MethodRegistry()
    auth_methods.register(reg, state)
    return reg, model_runtime


async def _call(registry, method: str, params: Optional[Dict[str, Any]] = None):
    msg = SimpleNamespace(method=method, params=params or {}, id=1)
    resp = await registry.dispatch(msg)
    assert resp is not None
    return resp


def _result(resp) -> Dict[str, Any]:
    assert resp.error is None, f"unexpected error: {resp.error}"
    return resp.result


@pytest.mark.asyncio
async def test_get_auth_status(registry):
    reg, _, _ = registry
    result = _result(await _call(reg, "getAuthStatus"))
    assert result["credentials"] == [{"provider": "volcengine", "type": "api_key"}]


@pytest.mark.asyncio
async def test_set_api_key_writes_and_refreshes(registry):
    """setApiKey：经 model_runtime.set_credential 统一写漏斗（trim 后落值）
    + model_runtime.refresh() 联动（可用性快照重算）。"""
    reg, _storage, model_runtime = registry
    result = _result(
        await _call(reg, "setApiKey", {"provider": "kimi", "apiKey": "  sk-x  "})
    )
    assert result["success"] is True
    set_calls = [c for c in model_runtime.calls if c[0] == "set_credential"]
    assert len(set_calls) == 1
    assert set_calls[0][1] == "kimi"
    assert set_calls[0][2].key == "sk-x"
    assert ("refresh",) in model_runtime.calls


@pytest.mark.asyncio
async def test_set_api_key_missing_param(registry):
    reg, _, _ = registry
    resp = await _call(reg, "setApiKey", {"provider": "kimi"})
    assert resp.error is not None


@pytest.mark.asyncio
async def test_login_goes_through_model_runtime(registry):
    """login：交互式登录统一走 model_runtime.login（交互经反向原语）。"""
    reg, _, model_runtime = registry
    result = _result(
        await _call(reg, "login", {"provider": "kimi", "authType": "api_key"})
    )
    assert result["success"] is True
    assert result["type"] == "api_key"
    kind, provider, auth_type, interaction = model_runtime.calls[0]
    assert kind == "login"
    assert provider == "kimi"
    assert auth_type == "api_key"
    assert interaction is not None


@pytest.mark.asyncio
async def test_login_rejects_bad_auth_type(registry):
    reg, _, _ = registry
    resp = await _call(reg, "login", {"provider": "kimi", "authType": "weird"})
    assert resp.error is not None


@pytest.mark.asyncio
async def test_login_oauth_requires_notify_capability():
    """oauth 登录需要前端 notify 能力（展示设备码），缺失即拒绝且不启动流程。"""
    reg, model_runtime = _registry_with_capabilities(set())
    resp = await _call(reg, "login", {"provider": "kimi", "authType": "oauth"})
    assert resp.error is not None
    assert "notify" in resp.error["message"]
    assert model_runtime.calls == []


@pytest.mark.asyncio
async def test_login_api_key_requires_input_capability():
    """api_key 登录需要前端 input 能力（输入密钥），缺失即拒绝。"""
    reg, model_runtime = _registry_with_capabilities({"notify"})
    resp = await _call(reg, "login", {"provider": "kimi", "authType": "api_key"})
    assert resp.error is not None
    assert "input" in resp.error["message"]
    assert model_runtime.calls == []


@pytest.mark.asyncio
async def test_login_oauth_with_notify_proceeds():
    """oauth 只需 notify 即放行（device code 展示后走轮询）。"""
    reg, model_runtime = _registry_with_capabilities({"notify"})
    result = _result(await _call(reg, "login", {"provider": "kimi"}))
    assert result["success"] is True
    assert model_runtime.calls and model_runtime.calls[0][0] == "login"


@pytest.mark.asyncio
async def test_logout_goes_through_model_runtime(registry):
    """logout：走 model_runtime.logout（模型刷新/可用性快照联动）。"""
    reg, _, model_runtime = registry
    result = _result(await _call(reg, "logout", {"provider": "volcengine"}))
    assert result["success"] is True
    assert model_runtime.calls == [("logout", "volcengine")]
