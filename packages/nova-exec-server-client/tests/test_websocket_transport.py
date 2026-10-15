"""WebSocketTransport 单元测试（本地回环假服务端，真 socket 真帧）。

覆盖入站限流三档（A13，对位 Rust connection.rs 的 WS 侧生效分支）与
反向请求并发裁决（A6）——与 stdio 侧共用同一闸门/准入机械，这里钉死
WS 承载下的行为。
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets.asyncio.client
import websockets.asyncio.server

from nova_exec_server_client import (
    NETWORK_POLICY_REQUEST,
    ConnectionError,
    WebSocketTransport,
)


async def start_server(handler) -> tuple:
    """起本地回环 WS 假服务端，返回 (server, url)"""
    server = await websockets.asyncio.server.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


def make_push_server(replies_sink: list | None = None, binary: bool = False):
    """echo/push 假服务端：echo 原样回 params；push 先回 result 再按
    params.messages 逐条推送（binary=True 时推送用二进制帧——对位 Rust
    对 Text/Binary 同一限流）；客户端的应答（无 method 的消息）进 sink"""

    async def server(ws) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if "method" not in msg:
                if replies_sink is not None:
                    replies_sink.append(msg)
                continue
            if msg["method"] == "echo":
                await ws.send(
                    json.dumps({"id": msg["id"], "result": msg.get("params")})
                )
            elif msg["method"] == "push":
                await ws.send(json.dumps({"id": msg["id"], "result": {}}))
                for pushed in msg["params"]["messages"]:
                    encoded = json.dumps(pushed)
                    await ws.send(encoded.encode() if binary else encoded)

    return server


async def wait_disconnected(transport: WebSocketTransport) -> None:
    for _ in range(100):
        if not transport.is_connected:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("transport stayed connected")


@pytest.mark.asyncio
async def test_ws_inbound_request_over_limit_breaks_connection():
    """A13：反向请求超 8KiB 即断开（对位 Rust
    client_websocket_connection_rejects_overlong_request）"""
    replies: list[dict] = []
    server, url = await start_server(make_push_server(replies))
    transport = WebSocketTransport(url)
    reasons: list[str | None] = []
    transport.on_disconnect(reasons.append)
    try:
        await transport.connect()
        await transport.send_request(
            "push",
            {
                "messages": [
                    {
                        "id": 9,
                        "method": "network/policyRequest",
                        "params": {"padding": "x" * 9000},
                    }
                ]
            },
        )
        await wait_disconnected(transport)
        assert reasons and "exceeds maximum length of 8192 bytes" in (reasons[0] or "")
    finally:
        await transport.disconnect()
        server.close()


@pytest.mark.asyncio
async def test_ws_inbound_binary_request_over_limit_breaks_connection():
    """A13：二进制帧同口径限流（对位 Rust parse_jsonrpc_frame 的
    Text/Binary 双分支）"""
    server, url = await start_server(make_push_server(binary=True))
    transport = WebSocketTransport(url)
    try:
        await transport.connect()
        await transport.send_request(
            "push",
            {
                "messages": [
                    {
                        "id": 9,
                        "method": "network/policyRequest",
                        "params": {"padding": "x" * 9000},
                    }
                ]
            },
        )
        await wait_disconnected(transport)
    finally:
        await transport.disconnect()
        server.close()


@pytest.mark.asyncio
async def test_ws_malformed_message_breaks_connection():
    """畸形帧零容忍（对位 Rust rpc.rs MalformedMessage → 断开 + 恢复）"""
    server, url = await start_server(make_push_server())
    transport = WebSocketTransport(url)
    reasons: list[str | None] = []
    transport.on_disconnect(reasons.append)
    try:
        await transport.connect()
        # 直接推一行非 JSON（绕过 push 的 json.dumps——纯手工帧）
        await transport.send_request("push", {"messages": []})
        ws_server = list(server.connections)[0]
        await ws_server.send("this-is-not-json")
        await wait_disconnected(transport)
        assert reasons and "failed to parse JSON-RPC message" in (reasons[0] or "")
    finally:
        await transport.disconnect()
        server.close()


@pytest.mark.asyncio
async def test_ws_data_plane_notification_bypasses_request_limit():
    """数据面通知放宽 2MiB 档：>8KiB 的 process/output 照常送达，连接不断"""
    server, url = await start_server(make_push_server())
    transport = WebSocketTransport(url)
    received: list[dict] = []

    async def handler(msg: dict) -> None:
        received.append(msg)

    transport.on_notification(handler)
    try:
        await transport.connect()
        padding = "x" * (100 * 1024)
        await transport.send_request(
            "push",
            {
                "messages": [
                    {
                        "method": "process/output",
                        "params": {
                            "processId": "p1",
                            "stream": "stdout",
                            "data": padding,
                        },
                    }
                ]
            },
        )
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.02)
        assert received and received[0]["params"]["data"] == padding
        assert transport.is_connected
    finally:
        await transport.disconnect()
        server.close()


@pytest.mark.asyncio
async def test_ws_large_response_bypasses_inbound_limits():
    """响应不受类别限流：>8KiB 的大响应照常归 pending（对位 Rust：
    Response/Error 直接 return None）"""
    server, url = await start_server(make_push_server())
    transport = WebSocketTransport(url)
    try:
        await transport.connect()
        padding = "x" * (256 * 1024)
        result = await transport.send_request("echo", {"data": padding})
        assert result == {"data": padding}
        assert transport.is_connected
    finally:
        await transport.disconnect()
        server.close()


@pytest.mark.asyncio
async def test_ws_server_requests_adjudicated_concurrently():
    """A6：WS 侧反向请求同样并发裁决——首个回调未返回时第二个照常准入并
    先行应答（对位 Rust 的 tokio::spawn 并发执行，接收循环不堵）"""
    replies: list[dict] = []
    server, url = await start_server(make_push_server(replies))
    transport = WebSocketTransport(url)
    started: list[str] = []

    async def handler(params: dict) -> dict:
        host = params["request"]["host"]
        started.append(host)
        if host == "h1.example.com":
            # 第一个裁决等待第二个到达——串行分发下这永远等不到（死锁）
            for _ in range(200):
                if "h2.example.com" in started:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("second request never dispatched concurrently")
            return {"decision": {"type": "deny", "reason": "first"}}
        return {"decision": {"type": "allow"}}

    transport.register_request_handler(NETWORK_POLICY_REQUEST, handler)
    try:
        await transport.connect()
        await transport.send_request(
            "push",
            {
                "messages": [
                    {
                        "id": rid,
                        "method": "network/policyRequest",
                        "params": {
                            "processId": f"p{rid}",
                            "request": {
                                "protocol": "https_connect",
                                "host": f"h{rid}.example.com",
                                "port": 443,
                            },
                        },
                    }
                    for rid in (1, 2)
                ]
            },
        )
        for _ in range(200):
            if len(replies) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(replies) == 2
        # 第二个先应答——并发铁证
        assert replies[0]["id"] == 2
        assert replies[0]["result"] == {"decision": {"type": "allow"}}
        assert replies[1]["id"] == 1
        assert replies[1]["result"] == {"decision": {"type": "deny", "reason": "first"}}
    finally:
        await transport.disconnect()
        server.close()


# ---------------------------------------------------------------------------
# A4/A5 WS 连接参数（对位 tungstenite max_message_size=64MiB 与 Rust
# connection.rs from_websocket 的 ping_interval None）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_connect_defaults_64mib_and_disables_ping(monkeypatch):
    """max_payload 默认 64MiB（对位 tungstenite 默认 max_message_size）；
    显式 ping_interval=None——客户端不发 keepalive ping、不靠 pong 看门狗
    （websockets 库默认 20s 一发，须显式关掉）"""
    captured = {}

    class _FakeWS:
        async def close(self):
            pass

        def __aiter__(self):
            async def _gen():
                await asyncio.Event().wait()  # 永不产出——等 disconnect 取消
                yield

            return _gen()

    async def fake_connect(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _FakeWS()

    monkeypatch.setattr(websockets.asyncio.client, "connect", fake_connect)
    transport = WebSocketTransport("ws://127.0.0.1:1")
    await transport.connect()
    try:
        assert captured["ping_interval"] is None
        assert captured["max_size"] == 64 * 1024 * 1024
        assert transport.is_connected
    finally:
        await transport.disconnect()


# ---------------------------------------------------------------------------
# A2 出站 NaN/Infinity 防线（json allow_nan=False）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_outbound_nan_rejected_before_write():
    """WS 出站 NaN 同样本地拒发（服务端词法拒收会判连接级致命）；
    线上未污染，连接照常服务"""
    server, url = await start_server(make_push_server())
    transport = WebSocketTransport(url)
    try:
        await transport.connect()
        with pytest.raises(ConnectionError, match="Out of range float values"):
            await transport.send_request("echo", {"v": float("nan")})
        assert transport.is_connected
        assert await transport.send_request("echo", {"ok": True}) == {"ok": True}
    finally:
        await transport.disconnect()
        server.close()


# ---------------------------------------------------------------------------
# A7 result+error 并存时 result 优先（对位 serde contains_key("result")）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_result_wins_when_error_also_present():
    """并存消息按 Response 归：result 落定、error 键被忽略——线上端到端
    （闸门归类与 _resolve_response 同纪律）"""

    async def both_keys_server(ws) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            await ws.send(
                json.dumps(
                    {
                        "id": msg["id"],
                        "result": {"winner": "result"},
                        "error": {"code": -32603, "message": "ignored"},
                    }
                )
            )

    server, url = await start_server(both_keys_server)
    transport = WebSocketTransport(url)
    try:
        await transport.connect()
        result = await transport.send_request("doctor")
        assert result == {"winner": "result"}
        assert transport.is_connected
    finally:
        await transport.disconnect()
        server.close()
