"""网络裁决门（gate.py）与审计糖 API（client.on_policy_decision）测试"""

import asyncio

import pytest

from nova_executor_client import ApprovalPolicy

pytestmark = pytest.mark.asyncio
from nova_executor_client.gate import (
    AskOutcome,
    NetworkPolicyGate,
)
from nova_executor_client.protocol import (
    ExecServerNetworkPolicyRequest,
    ExecServerNetworkProtocol,
    NetworkPolicyRequestParams,
)


def _params(host: str, process_id: str = "proc-1") -> NetworkPolicyRequestParams:
    return NetworkPolicyRequestParams(
        processId=process_id,
        request=ExecServerNetworkPolicyRequest(
            protocol=ExecServerNetworkProtocol.HTTPS_CONNECT, host=host, port=443
        ),
    )


class TestGateMemory:
    async def test_remembered_allow_short_circuits(self):
        gate = NetworkPolicyGate()  # 无 on_ask——若打到 ask 会 deny
        gate.remember("api.example.com", allow=True)
        decision = await gate.decide(_params("api.example.com"))
        assert decision.type == "allow"

    async def test_remembered_deny_short_circuits(self):
        gate = NetworkPolicyGate()
        gate.remember("evil.com", allow=False)
        decision = await gate.decide(_params("evil.com"))
        assert decision.type == "deny"
        assert decision.reason

    async def test_forget(self):
        gate = NetworkPolicyGate()
        gate.remember("x.com", allow=True)
        assert gate.forget("x.com") is True
        assert gate.forget("x.com") is False
        assert gate.snapshot() == {}

    async def test_snapshot_restore_roundtrip(self):
        gate = NetworkPolicyGate()
        gate.remember("a.com", allow=True)
        gate.remember("b.com", allow=False)
        snap = gate.snapshot()
        gate2 = NetworkPolicyGate()
        gate2.restore(snap)
        assert (await gate2.decide(_params("a.com"))).type == "allow"
        assert (await gate2.decide(_params("b.com"))).type == "deny"


class TestGateFailClosed:
    async def test_no_on_ask_denies(self):
        gate = NetworkPolicyGate()
        decision = await gate.decide(_params("unknown.com"))
        assert decision.type == "deny"
        assert "无可询问渠道" in (decision.reason or "")

    async def test_approval_never_denies_even_with_on_ask(self):
        called = False

        async def on_ask(_params):
            nonlocal called
            called = True
            return AskOutcome.ALLOW

        gate = NetworkPolicyGate(on_ask=on_ask, approval_policy=ApprovalPolicy.NEVER)
        decision = await gate.decide(_params("unknown.com"))
        assert decision.type == "deny"
        assert called is False  # never 档根本不问

    async def test_memory_table_is_exact_host_match(self):
        """模式匹配归服务端静态名单；记忆表只按精确主机名"""
        gate = NetworkPolicyGate()
        gate.remember("api.example.com", allow=True)
        decision = await gate.decide(_params("other.example.com"))
        assert decision.type == "deny"  # 不命中记忆，且无 on_ask → 兜底拒


class TestGateAskFlow:
    async def test_allow_once_does_not_remember(self):
        async def on_ask(_params):
            return AskOutcome.ALLOW

        gate = NetworkPolicyGate(on_ask=on_ask)
        assert (await gate.decide(_params("new.com"))).type == "allow"
        assert gate.snapshot() == {}

    async def test_allow_remember_persists(self):
        async def on_ask(_params):
            return AskOutcome.ALLOW_REMEMBER

        gate = NetworkPolicyGate(on_ask=on_ask)
        assert (await gate.decide(_params("new.com"))).type == "allow"
        assert gate.snapshot() == {"new.com": True}
        # 第二次不再问
        gate._on_ask = None
        assert (await gate.decide(_params("new.com"))).type == "allow"

    async def test_deny_remember_persists(self):
        async def on_ask(_params):
            return AskOutcome.DENY_REMEMBER

        gate = NetworkPolicyGate(on_ask=on_ask)
        assert (await gate.decide(_params("bad.com"))).type == "deny"
        assert gate.snapshot() == {"bad.com": False}


class TestPolicyDecisionSugar:
    async def test_typed_subscription_and_filter(self, monkeypatch):
        from nova_executor_client import ExecutorClient
        from nova_executor_client.protocol import NetworkPolicyDecisionNotification

        client = ExecutorClient(transport_factory=lambda: None)  # 不连接，仅测糖
        router = client.notifications

        async def emit(host: str, process_id: str):
            await router.dispatch(
                {
                    "method": "network/policyDecision",
                    "params": NetworkPolicyDecisionNotification(
                        processId=process_id,
                        timestamp="2026-09-01T00:00:00Z",
                        scope="process",
                        decision="allow",
                        source="decider",
                        reason="ok",
                        protocol=ExecServerNetworkProtocol.HTTPS_CONNECT,
                        host=host,
                        port=443,
                    ).model_dump(by_alias=True, exclude_none=True),
                }
            )

        seen = []

        async def collect():
            async for event in client.on_policy_decision(process_id="proc-1"):
                seen.append(event)
                if len(seen) == 1:
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0)  # 让订阅挂上
        await emit("skip.example.com", "proc-2")  # 应被过滤
        await emit("hit.example.com", "proc-1")
        await asyncio.wait_for(task, 2)
        assert [e.host for e in seen] == ["hit.example.com"]
        assert seen[0].process_id == "proc-1"
