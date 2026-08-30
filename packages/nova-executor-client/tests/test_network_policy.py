"""网络沙箱裁决的客户端半场测试（network/policyRequest 反向请求）。

对位 rust 侧 client_recovery.rs 应答 + client/tests/network_policy_tests.rs：
服务端（fake）经 stdio 发反向裁决请求，客户端处理器应答 allow/deny/ask；
无处理器时回 METHOD_NOT_FOUND，服务端按 fail-closed 拒决（安全缺省）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nova_executor_client import (
    NETWORK_POLICY_REQUEST,
    ExecutorClient,
    NetworkPolicyDecision,
    NetworkPolicyRequestParams,
    StdioTransport,
)

FAKE_SERVER = str(Path(__file__).parent / "fake_executor_server.py")

PROBE_SAW = "saw"


@pytest.mark.asyncio
async def test_transport_request_handler_answers_deny():
    """传输层：注册的处理器收到裁决请求，deny+reason 原样回到服务端"""
    transport = StdioTransport(program=sys.executable, args=[FAKE_SERVER])
    await transport.connect()
    try:

        async def deny_all(params):
            # wire 要求响应为 NetworkPolicyRequestResponse 形态（{"decision": ...}）
            return {"decision": NetworkPolicyDecision.deny("blocked host").model_dump()}

        transport.register_request_handler(NETWORK_POLICY_REQUEST, deny_all)
        result = await transport.send_request("policy/probe", {})
        assert result[PROBE_SAW] == {"type": "deny", "reason": "blocked host"}
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_client_network_policy_callback_gets_typed_params():
    """客户端层：回调收类型化 params（host/port/protocol/process_id），
    allow 裁决 round-trip 回服务端"""
    captured = {}

    async def policy(params: NetworkPolicyRequestParams) -> NetworkPolicyDecision:
        captured["process_id"] = params.process_id
        captured["host"] = params.request.host
        captured["port"] = params.request.port
        captured["protocol"] = params.request.protocol.value
        return NetworkPolicyDecision.allow()

    transport = StdioTransport(program=sys.executable, args=[FAKE_SERVER])
    client = ExecutorClient(transport=transport, network_policy=policy)
    await client.connect()
    try:
        result = await client.transport.send_request("policy/probe", {})
        assert result[PROBE_SAW] == {"type": "allow"}
        assert captured == {
            "process_id": "p1",
            "host": "api.example.com",
            "port": 443,
            "protocol": "https_connect",
        }
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_no_handler_replies_method_not_found():
    """安全缺省：无处理器 → 回 METHOD_NOT_FOUND（服务端据此 fail-closed 拒决）"""
    transport = StdioTransport(program=sys.executable, args=[FAKE_SERVER])
    await transport.connect()
    try:
        result = await transport.send_request("policy/probe", {})
        assert "no handler" in result[PROBE_SAW]["error"]
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_handler_exception_turns_into_error_reply():
    """处理器崩溃 → 内部错误回执（服务端 fail-closed 拒决，裁决不干等）"""
    transport = StdioTransport(program=sys.executable, args=[FAKE_SERVER])
    await transport.connect()
    try:

        async def broken(params):
            raise RuntimeError("boom")

        transport.register_request_handler(NETWORK_POLICY_REQUEST, broken)
        result = await transport.send_request("policy/probe", {})
        assert "boom" in result[PROBE_SAW]["error"]
    finally:
        await transport.disconnect()
