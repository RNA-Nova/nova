"""http/request SDK 方法的单元测试（经 fake stdio server，真协议真管道）"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest
from nova_protocol import HttpRequestBodyDeltaNotification

from nova_exec_server_client import StdioTransport
from nova_exec_server_client.client import ExecutorClient
from nova_exec_server_client.errors import ProtocolError
from nova_exec_server_client.notifications import (
    MAX_ENCODED_HTTP_BODY_DELTA_BYTES,
    NotificationRouter,
)

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
        body = base64.b64decode(result["bodyBase64"]).decode()
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
    router = NotificationRouter()
    queue = router.register_http_body_stream("r")
    for payload in [
        {
            "requestId": "r",
            "seq": 1,
            "deltaBase64": base64.b64encode(b"hel").decode(),
            "done": False,
        },
        {
            "requestId": "r",
            "seq": 2,
            "deltaBase64": base64.b64encode(b"lo").decode(),
            "done": True,
        },
    ]:
        await router.dispatch({"method": "http/request/bodyDelta", "params": payload})
    collected = []
    while not queue.empty():
        collected.append(await queue.get())
    assert (
        b"".join(base64.b64decode(e["params"]["deltaBase64"]) for e in collected)
        == b"hello"
    )
    assert collected[-1]["params"]["done"] is True


@pytest.mark.asyncio
async def test_router_surfaces_error_delta():
    """error 终止帧照常进队列（SDK 层转 ProtocolError）"""
    router = NotificationRouter()
    queue = router.register_http_body_stream("r")
    await router.dispatch(
        {
            "method": "http/request/bodyDelta",
            "params": {
                "requestId": "r",
                "seq": 1,
                "deltaBase64": base64.b64encode(b"x").decode(),
                "done": True,
                "error": "collector died",
            },
        }
    )
    event = await queue.get()
    assert event["params"]["error"] == "collector died"


@pytest.mark.asyncio
async def test_http_request_stream_assembles_body_until_done():
    """流式模式全链路：fake server 分两帧推 bodyDelta，client 收齐 done 拼装完整响应体"""
    transport = StdioTransport(program=sys.executable, args=[FAKE_SERVER])
    await transport.connect()
    router = NotificationRouter()
    transport.on_notification(router.dispatch)
    client = ExecutorClient(transport=transport)
    client._router = router
    await client.connect()  # initialize 握手（fake server 支持）
    try:
        response = await client.http_request(
            "GET",
            "https://example.test/stream",
            request_id="req-2",
            stream_response=True,
        )
        assert response.status == 200
        assert response.body.data == b"echo:GET:https://example.test/stream"
    finally:
        await client.disconnect()


def test_http_header_value_env_var_wire_shape():
    """HttpHeader 的 valueEnvVar 别名与 None 不出场（凭据不跨线委派 wire 形态）"""
    from nova_exec_server_client import HttpHeader, HttpRequestParams

    params = HttpRequestParams(
        method="GET",
        url="https://example.test",
        headers=[
            HttpHeader(name="authorization", value="Bearer ", valueEnvVar="MY_TOKEN")
        ],
        requestId="r1",
    )
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert dumped["headers"] == [
        {"name": "authorization", "value": "Bearer ", "valueEnvVar": "MY_TOKEN"}
    ]

    plain = HttpRequestParams(
        method="GET",
        url="https://example.test",
        headers=[HttpHeader(name="x", value="y")],
        requestId="r2",
    )
    assert (
        "valueEnvVar"
        not in plain.model_dump(by_alias=True, exclude_none=True)["headers"][0]
    )


@pytest.mark.asyncio
async def test_http_body_stream_seq_gap_is_protocol_error():
    """bodyDelta seq 缺口即协议错误（对位 Rust recv() 的严格序校验）——
    S 级断言：缺段不得当成功拼装"""
    router = NotificationRouter()
    queue = router.register_http_body_stream("r")
    for seq, payload in [(1, b"a"), (3, b"c")]:  # 跳过 seq=2
        await router.dispatch(
            {
                "method": "http/request/bodyDelta",
                "params": {
                    "requestId": "r",
                    "seq": seq,
                    "deltaBase64": base64.b64encode(payload).decode(),
                    "done": False,
                },
            }
        )
    # 消费端（client.py http_request 同款逻辑）：seq 2 缺失即协议错误
    expected_seq = 1
    with pytest.raises(ProtocolError, match="seq gap"):
        while True:
            event = await queue.get()
            delta = HttpRequestBodyDeltaNotification.model_validate(event["params"])
            if delta.seq != expected_seq:
                raise ProtocolError(
                    f"http body stream seq gap: expected {expected_seq}, got {delta.seq}"
                )
            expected_seq += 1
            if delta.done:
                break


@pytest.mark.asyncio
async def test_http_body_stream_queue_full_fails_loudly():
    """bodyDelta 队列满 → 摘除路由 + 响亮终态（S 级断言：不静默丢帧截断）"""
    router = NotificationRouter()
    queue = router.register_http_body_stream("r")
    # 灌满队列（256 格，不消费）
    for seq in range(1, 260):
        await router.dispatch(
            {
                "method": "http/request/bodyDelta",
                "params": {
                    "requestId": "r",
                    "seq": seq,
                    "deltaBase64": base64.b64encode(b"x").decode(),
                    "done": False,
                },
            }
        )
    # 路由已摘除（再来的增量被丢弃）+ 队列里有响亮终态
    assert "r" not in router._http_streams
    events = []
    while not queue.empty():
        events.append(await queue.get())
    terminal = events[-1]
    assert terminal["params"]["error"] is not None
    assert "queue is full" in terminal["params"]["error"]


@pytest.mark.asyncio
async def test_http_body_streams_isolated_by_request_id():
    """并发双流按 requestId 隔离（S 级断言：r1 的增量绝不进 r2）"""
    router = NotificationRouter()
    q1 = router.register_http_body_stream("r1")
    q2 = router.register_http_body_stream("r2")
    for rid, seq, payload in [("r1", 1, b"one"), ("r2", 1, b"two")]:
        await router.dispatch(
            {
                "method": "http/request/bodyDelta",
                "params": {
                    "requestId": rid,
                    "seq": seq,
                    "deltaBase64": base64.b64encode(payload).decode(),
                    "done": True,
                },
            }
        )
    e1 = await q1.get()
    e2 = await q2.get()
    assert base64.b64decode(e1["params"]["deltaBase64"]) == b"one"
    assert base64.b64decode(e2["params"]["deltaBase64"]) == b"two"
    assert q1.empty() and q2.empty()


async def _push_delta(
    router: NotificationRouter, rid: str, seq: int, size: int
) -> None:
    """推一条指定解码字节数的 bodyDelta（预算测试用）"""
    await router.dispatch(
        {
            "method": "http/request/bodyDelta",
            "params": {
                "requestId": rid,
                "seq": seq,
                "deltaBase64": base64.b64encode(b"x" * size).decode(),
                "done": False,
            },
        }
    )


@pytest.mark.asyncio
async def test_http_body_byte_budget_is_connection_level_shared():
    """字节预算是连接级共享（对位 Rust Inner.http_body_stream_byte_budget 单一
    信号量）：并发双流合计超 16MB 时只杀抢不到预算的那条，其余流不受影响；
    在队字节随取扣账（排空后预算归还可继续推流）"""
    router = NotificationRouter()
    q1 = router.register_http_body_stream("r1")
    q2 = router.register_http_body_stream("r2")
    mib = 1024 * 1024

    # r1 慢消费：12MB 压队（在队 12MB，未超 16MB 预算，流正常）
    for seq in range(1, 13):
        await _push_delta(router, "r1", seq, mib)
    assert "r1" in router._http_streams

    # r2 再推：前 4MB 把在队总量顶到恰好 16MB（取等不杀），第 5MB 超限
    # → 只杀 r2（摘除路由 + 响亮终态）
    for seq in range(1, 6):
        await _push_delta(router, "r2", seq, mib)
    assert "r1" in router._http_streams  # 其余流不受影响
    assert "r2" not in router._http_streams
    events = []
    while not q2.empty():
        events.append(await q2.get())
    terminal = events[-1]
    assert terminal["params"]["error"] is not None
    assert "budget" in terminal["params"]["error"]
    # r2 前 4 条增量一个不少（终态不丢数据纪律）
    assert len(events) == 5

    # r1 完整流完 12MB（在队字节随取扣账，排空后预算归还）
    drained = 0
    while not q1.empty():
        event = await q1.get()
        drained += len(base64.b64decode(event["params"]["deltaBase64"]))
    assert drained == 12 * mib
    assert router._http_byte_budget.in_flight_bytes == 0

    # 预算归还后新流照常推进（排空够快任意大 body 都能流完——20MB 分两批
    # 过 16MB 预算；单条 delta 受 per-delta 1MB 帽约束，按 1MiB 粒度推）
    q3 = router.register_http_body_stream("r3")
    for batch in range(2):
        for seq in range(1, 11):
            await _push_delta(router, "r3", batch * 10 + seq, mib)
        for _ in range(10):
            assert (await q3.get())["params"]["error"] is None
    assert router._http_byte_budget.in_flight_bytes == 0
    assert "r3" in router._http_streams


@pytest.mark.asyncio
async def test_http_body_unregister_releases_queued_bytes():
    """注销时仍压队的在队字节归还连接级预算（消费者已走，残留占账不得漏成
    幽灵字节耗尽预算误杀后续流）"""
    router = NotificationRouter()
    queue = router.register_http_body_stream("r")
    for seq in range(1, 13):
        await _push_delta(router, "r", seq, 1024 * 1024)
    assert router._http_byte_budget.in_flight_bytes == 12 * 1024 * 1024
    router.unregister_http_body_stream("r")
    assert router._http_byte_budget.in_flight_bytes == 0
    # 后续流预算充足照常推进（15MB > 旧按流累计帽也不会误杀——预算是
    # 在队字节不是流累计；单条 delta 受 per-delta 1MB 帽约束按 1MiB 粒度推）
    q2 = router.register_http_body_stream("r2")
    for seq in range(1, 16):
        await _push_delta(router, "r2", seq, 1024 * 1024)
    for _ in range(15):
        assert (await q2.get())["params"]["error"] is None
    assert "r2" in router._http_streams
    assert router._http_byte_budget.in_flight_bytes == 0
    assert queue.empty()


@pytest.mark.asyncio
async def test_http_body_delta_over_encoded_cap_fails_stream():
    """per-delta 帽之一：deltaBase64 编码串超长（对位 Rust MAX_ENCODED 预检）
    → 协议错误走畸形同路径：fail 对应流（响亮终态 + 摘路由），其余流不受影响"""
    router = NotificationRouter()
    q1 = router.register_http_body_stream("r1")
    q2 = router.register_http_body_stream("r2")
    oversized = base64.b64encode(b"x" * (3 * 1024 * 1024)).decode()  # 4MB 编码串

    await router.dispatch(
        {
            "method": "http/request/bodyDelta",
            "params": {
                "requestId": "r1",
                "seq": 1,
                "deltaBase64": oversized,
                "done": False,
            },
        }
    )

    assert "r1" not in router._http_streams
    event = await asyncio.wait_for(q1.get(), 1)
    assert event["params"]["error"] is not None
    assert "malformed" in event["params"]["error"]
    # 其余流不受影响；超限流的后续增量按未知请求丢弃
    assert "r2" in router._http_streams
    await router.dispatch(
        {
            "method": "http/request/bodyDelta",
            "params": {
                "requestId": "r1",
                "seq": 2,
                "deltaBase64": base64.b64encode(b"y").decode(),
                "done": True,
            },
        }
    )
    await _push_delta(router, "r2", 1, 3)
    assert base64.b64decode((await q2.get())["params"]["deltaBase64"]) == b"x" * 3


@pytest.mark.asyncio
async def test_http_body_delta_over_decoded_cap_fails_stream():
    """per-delta 帽之二：解码后超 1MB（编码帽取等放行的边界载荷由解码帽兜底，
    对位 Rust MAX_HTTP_BODY_DELTA_BYTES 复检）→ 同畸形路径 fail 对应流"""
    router = NotificationRouter()
    queue = router.register_http_body_stream("r")
    # 1MiB+1 字节：编码串长恰为帽值（1398104，取等放行），解码后超限
    payload = b"x" * (1024 * 1024 + 1)
    encoded = base64.b64encode(payload).decode()
    assert len(encoded) <= MAX_ENCODED_HTTP_BODY_DELTA_BYTES

    await router.dispatch(
        {
            "method": "http/request/bodyDelta",
            "params": {
                "requestId": "r",
                "seq": 1,
                "deltaBase64": encoded,
                "done": False,
            },
        }
    )

    assert "r" not in router._http_streams
    event = await asyncio.wait_for(queue.get(), 1)
    assert event["params"]["error"] is not None
    # 连接级预算未被幽灵字节占用（超限载荷从未入队占账）
    assert router._http_byte_budget.in_flight_bytes == 0


@pytest.mark.asyncio
async def test_http_body_delta_at_caps_passes():
    """帽值取等放行：解码恰好 1MiB 的 delta 正常入队（预算口径独立——单流
    1MiB 远低于 16MB 连接级预算）"""
    router = NotificationRouter()
    queue = router.register_http_body_stream("r")
    payload = b"x" * (1024 * 1024)
    await router.dispatch(
        {
            "method": "http/request/bodyDelta",
            "params": {
                "requestId": "r",
                "seq": 1,
                "deltaBase64": base64.b64encode(payload).decode(),
                "done": True,
            },
        }
    )
    event = await asyncio.wait_for(queue.get(), 1)
    assert base64.b64decode(event["params"]["deltaBase64"]) == payload
    assert event["params"]["done"] is True
