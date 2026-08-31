"""user_tools RPC 方法测试：listUserTools / invokeUserTool /
abortUserTool（泛型三件套；框架不内置具体工具，无别名方法）。"""

from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional

import pytest
from nova_agent import CustomAgentMessage

from nova_harness.core.types.resources.user_tools import UserToolInfo
from nova_harness.server.protocol import JSONRPCError, MethodRegistry
from nova_harness.server.protocol.methods import user_tools as st_methods
from nova_harness.server.protocol.methods.state import ServerState


class FakeToolMessage(CustomAgentMessage):
    """测试用用户工具消息。"""

    text: str = ""
    timestamp: int = 0
    role: Literal["fakeTool"] = "fakeTool"


class FakeSession:
    """记录调用的轻量 AgentSession 替身。"""

    def __init__(self):
        self.calls: List[tuple] = []
        self._catalog = [
            UserToolInfo(
                name="fake",
                description="测试用户工具",
                parameters={"type": "object", "properties": {}},
            )
        ]

    def list_user_tools(self):
        self.calls.append(("list_user_tools",))
        return self._catalog

    async def invoke_user_tool(self, name, params=None, on_event=None):
        self.calls.append(("invoke_user_tool", name, params))
        if name != "fake":
            raise KeyError(f"未知的用户工具: '{name}'")
        return FakeToolMessage(text="done\n", timestamp=1)

    def abort_user_tool(self, name=None):
        self.calls.append(("abort_user_tool", name))


class FakeRuntime:
    def __init__(self, session):
        self.session = session


@pytest.fixture
def registry():
    session = FakeSession()
    state = ServerState(ui_context=SimpleNamespace())
    state.set_runtime(FakeRuntime(session))
    reg = MethodRegistry()
    st_methods.register(reg, state)
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
async def test_list_user_tools(registry):
    reg, _ = registry
    result = _result(await _call(reg, "listUserTools"))
    assert len(result) == 1
    assert result[0]["name"] == "fake"


@pytest.mark.asyncio
async def test_invoke_user_tool(registry):
    reg, session = registry
    result = _result(
        await _call(reg, "invokeUserTool", {"name": "fake", "params": {"q": "nova"}})
    )
    assert session.calls == [("invoke_user_tool", "fake", {"q": "nova"})]
    assert result["message"]["role"] == "fakeTool"
    assert result["message"]["text"] == "done\n"


@pytest.mark.asyncio
async def test_invoke_user_tool_unknown(registry):
    reg, _ = registry
    resp = await _call(reg, "invokeUserTool", {"name": "nope"})
    assert resp.error is not None


@pytest.mark.asyncio
async def test_invoke_user_tool_missing_name(registry):
    reg, _ = registry
    resp = await _call(reg, "invokeUserTool", {})
    assert resp.error is not None


@pytest.mark.asyncio
async def test_abort_user_tool(registry):
    reg, session = registry
    result = _result(await _call(reg, "abortUserTool", {"name": "fake"}))
    assert result["success"] is True
    assert session.calls == [("abort_user_tool", "fake")]


@pytest.mark.asyncio
async def test_abort_user_tool_all(registry):
    reg, session = registry
    result = _result(await _call(reg, "abortUserTool", {}))
    assert result["success"] is True
    assert session.calls == [("abort_user_tool", None)]


@pytest.mark.asyncio
async def test_no_bash_alias_methods(registry):
    """框架不内置用户工具：RPC 面没有 bash / abort_bash 这类工具专属别名。"""
    reg, _ = registry
    resp = await _call(reg, "bash", {"command": "ls"})
    assert resp.error is not None
    resp = await _call(reg, "abort_bash", {})
    assert resp.error is not None
