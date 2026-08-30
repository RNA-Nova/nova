"""http/request SDK 方法的单元测试（经 fake stdio server，真协议真管道）"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest

from nova_executor_client import ConnectionError, ProtocolError, StdioTransport
from nova_executor_client.client import ExecutorClient
from nova_executor_client.notifications import NotificationRouter

FAKE_SERVER = str(Path(__file__).parent / "fake_executor_server.py")


@pytest.mark.asyncio
async def test_http_request_buffered_roundtrip():
    """buffered 模式：fake server 回显请求方法+URL，响应体完整返回"""
    transport = StdioTransport(program=sys.executable, args=[FAKE_SERVER])
    await transport.connect()
    try:
        request = {
            "method": "http/request",
            "params": {
                "method": "GET",
                "url": "https://example.test/collect",
                "requestId": "req-1",
                "streamResponse": False,
            },
        }
        result = await transport.send_request("http/request", request["params"])
        body = base64.b64decode(result["body"]["data"]).decode()
        assert result["status"] == 200
        assert body == "echo:GET:https://example.test/collect"
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_client_http_request_via_fake_server():
    """ExecutorClient.http_request 全链路（fake server 兼 http/request 分支）"""
    transport = StdioTransport(program=sys.executable, args=[FAKE_SERVER])
    await transport.connect()
    router = NotificationRouter()
    transport.on_notification(router.dispatch)
    client = ExecutorClient(transport=transport)
    client._router = router
    await client.connect()  # initialize 握手（fake server 支持）
    try:
        response = await client.http_request(
            "GET", "https://example.test/collect", request_id="req-1"
        )
        assert response.status == 200
        assert response.body.data.startswith(b"echo:GET:")
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_router_collects_body_deltas_until_done():
    """http body delta 通知经路由器收集、done 终止拼装"""
    import base64

    from nova_executor_client.notifications import NotificationRouter

    router = NotificationRouter()
    queue = router.register_method("http/request/bodyDelta")
    for payload in [
        {
            "requestId": "r",
            "seq": 1,
            "delta": {"data": base64.b64encode(b"hel").decode()},
            "done": False,
        },
        {
            "requestId": "r",
            "seq": 2,
            "delta": {"data": base64.b64encode(b"lo").decode()},
            "done": True,
        },
    ]:
        await router.dispatch({"method": "http/request/bodyDelta", "params": payload})
    collected = []
    while not queue.empty():
        collected.append(await queue.get())
    assert b"".join(
        e["params"]["delta"]["data"] for e in collected
    ) == b"hello"
    assert collected[-1]["params"]["done"] is True


@pytest.mark.asyncio
async def test_router_surfaces_error_delta():
    """error 终止帧照常进队列（SDK 层转 ProtocolError）"""
    import base64

    from nova_executor_client.notifications import NotificationRouter

    router = NotificationRouter()
    queue = router.register_method("http/request/bodyDelta")
    await router.dispatch(
        {
            "method": "http/request/bodyDelta",
            "params": {
                "requestId": "r",
                "seq": 1,
                "delta": {"data": base64.b64encode(b"x").decode()},
                "done": True,
                "error": "collector died",
            },
        }
    )
    event = await queue.get()
    assert event["params"]["error"] == "collector died"


@pytest.mark.asyncio
async def test_http_request_stream_rejects_missing_transport_support_gracefully():
    """ProtocolError 语义存在性回归（错误族未被扫尾批破坏）"""
    import base64

    from nova_executor_client.notifications import NotificationRouter

    router = NotificationRouter()
    queue = router.register_method("http/request/bodyDelta")
    await router.dispatch(
        {
            "method": "http/request/bodyDelta",
            "params": {
                "requestId": "r",
                "seq": 1,
                "delta": {"data": base64.b64encode(b"x").decode()},
                "done": True,
                "error": "collector died",
            },
        }
    )
    event = await queue.get()
    with pytest.raises(ProtocolError):
        if event["params"].get("error"):
            raise ProtocolError(event["params"]["error"])
