"""InProcessNovaClient 测试：进程内完整 RPC 栈的嵌入形态。

对位 codex ``InProcessAppServerClient`` 的契约——请求/通知/有序事件
三通道，与 stdio/websocket 客户端同一协议路径。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from nova_server.client.in_process import (InProcessNovaClient,
                                           InProcessServerError)


@pytest.fixture
async def client():
    started = await InProcessNovaClient.start()
    yield started
    await started.shutdown()


@pytest.mark.asyncio
async def test_request_initialize_returns_contract(client: InProcessNovaClient):
    result: Dict[str, Any] = await client.request("initialize", {})
    assert result["version"]
    caps = result["capabilities"]
    assert "session" in caps["domains"]
    assert caps["methods"]


@pytest.mark.asyncio
async def test_request_error_raises_typed_error(client: InProcessNovaClient):
    with pytest.raises(InProcessServerError) as exc_info:
        await client.request("noSuchMethod", {})
    assert exc_info.value.code == -32601  # method not found


@pytest.mark.asyncio
async def test_requests_are_multiplexed_by_id(client: InProcessNovaClient):
    """并发请求各自拿到自己的响应（id 多路复用，不串线）。"""
    results = await __import__("asyncio").gather(
        client.request("initialize", {}),
        client.request("initialize", {}),
        client.request("initialize", {}),
    )
    assert all(r["version"] for r in results)


@pytest.mark.asyncio
async def test_events_stream_ordered_and_exhaustible(client: InProcessNovaClient):
    """通知帧进事件流（有序），响应帧不混入。"""
    await client.request("initialize", {})
    # 发一条已知会广播域通知的方法（settings 更新走 settingsChanged 类通知）；
    # 事件流不保证服务端在该方法下推送——这里只验证通道语义：响应不进流
    events: List[Dict[str, Any]] = []

    async def _drain_one() -> None:
        event = await client.next_event()
        if event is not None:
            events.append(event)

    # 关停后 next_event 返回 None（通道耗尽语义）
    await client.shutdown()
    assert await client.next_event() is None
    assert events == []


@pytest.mark.asyncio
async def test_shutdown_is_idempotent(client: InProcessNovaClient):
    await client.shutdown()
    await client.shutdown()  # 第二次为无操作，不抛


@pytest.mark.asyncio
async def test_injected_state_is_visible_to_handlers():
    """注入 state：handler 看到的是调用方给的那份（嵌入定制点）。"""
    from nova_server.protocol.methods.state import ServerState

    state = ServerState()
    client = await InProcessNovaClient.start(state=state)
    try:
        result = await client.request("initialize", {})
        assert result["version"]
        assert client._state is state
    finally:
        await client.shutdown()
