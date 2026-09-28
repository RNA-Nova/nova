"""委派裁决点单元测试（subagent_gate 收编——自治权检查点）。"""

import asyncio

from nova_coding_agent.adjudication.delegation import DelegationGate, extract_agent_names
from nova_harness.events.results import ToolCallEventResult


def _run(coro):
    return asyncio.run(coro)


class _FakeUI:
    def __init__(self, script=()):
        self._script = list(script)

    def has_capability(self, method):
        return True

    class _Resp:
        def __init__(self, value):
            self.value = value
            self.cancelled = False

    async def request(self, method, params):
        return self._Resp(self._script.pop(0))


class _FakeCtx:
    def __init__(self, has_ui=True, script=(), branch=()):
        self.has_ui = has_ui
        self.ui = _FakeUI(script)
        self.session_manager = None
        self.entries = []
        self._branch = list(branch)

    def append_entry(self, custom_type, data):
        self.entries.append((custom_type, data))


class _FakeEvent:
    def __init__(self, tool_name, args):
        self.tool_name = tool_name
        self.args = args


def test_extract_agent_names():
    assert extract_agent_names({"agent": "worker"}) == ["worker"]
    assert extract_agent_names(
        {"tasks": [{"agent": "a"}, {"agent": "b"}, {"agent": "a"}]}
    ) == ["a", "b"]
    assert extract_agent_names(
        {"chain": [{"agent": "x"}, {"agent": "y"}]}
    ) == ["x", "y"]
    assert extract_agent_names({}) == []


def test_headless_passes():
    gate = DelegationGate()
    event = _FakeEvent("subagent", {"agent": "worker"})
    assert _run(gate.on_tool_call(event, _FakeCtx(has_ui=False))) is None


def test_allow_once():
    gate = DelegationGate()
    event = _FakeEvent("subagent", {"agent": "worker"})
    assert _run(gate.on_tool_call(event, _FakeCtx(script=["允许一次"]))) is None
    # 下次仍问
    event2 = _FakeEvent("subagent", {"agent": "worker"})
    assert _run(gate.on_tool_call(event2, _FakeCtx(script=["允许一次"]))) is None


def test_allow_session_caches_and_restores():
    gate = DelegationGate()
    ctx = _FakeCtx(script=["本会话始终允许"])
    event = _FakeEvent("subagent", {"agent": "worker"})
    assert _run(gate.on_tool_call(event, ctx)) is None
    # 会话条目落盘（subagent_allow，累计全集）
    assert ("subagent_allow", {"agents": ["worker"]}) in ctx.entries
    # 同一 gate 内后续不再问
    assert _run(gate.on_tool_call(_FakeEvent("subagent", {"agent": "worker"}), _FakeCtx(script=[]))) is None

    # 新 gate（模拟重启）经 restore 恢复
    gate2 = DelegationGate()
    class _FakeSM:
        def __init__(self, entries):
            self._entries = entries
        def get_branch(self):
            class E:
                type = "custom"
                custom_type = "subagent_allow"
                data = None
            out = []
            for data in self._entries:
                e = E(); e.data = data; out.append(e)
            return out
    ctx2 = _FakeCtx()
    ctx2.session_manager = _FakeSM([{"agents": ["worker"]}])
    gate2.restore(ctx2)
    assert _run(gate2.on_tool_call(_FakeEvent("subagent", {"agent": "worker"}), _FakeCtx())) is None


def test_cancel_blocks():
    gate = DelegationGate()
    event = _FakeEvent("subagent", {"agent": "worker"})
    result = _run(gate.on_tool_call(event, _FakeCtx(script=["取消"])))
    assert isinstance(result, ToolCallEventResult)
    assert result.block is True


def test_multi_agent_any_cancel_blocks_all():
    gate = DelegationGate()
    event = _FakeEvent(
        "subagent",
        {"parallel": None, "tasks": [{"agent": "a"}, {"agent": "b"}]},
    )
    ctx = _FakeCtx(script=["允许一次", "取消"])
    result = _run(gate.on_tool_call(event, ctx))
    assert isinstance(result, ToolCallEventResult)


def test_non_subagent_tool_skips():
    gate = DelegationGate()
    assert _run(gate.on_tool_call(_FakeEvent("bash", {}), _FakeCtx())) is None
