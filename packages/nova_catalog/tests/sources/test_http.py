"""目录源 HTTP 拉取的状态、超时与取消边界测试。"""

import asyncio

import httpx
import pytest
import respx
from nova_catalog.sources.http import FetchError, fetch_json


@pytest.mark.asyncio
@respx.mock
async def test_http_single_get_json():
    url = "https://models.dev/experimental/v2.0/api.json"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"ok": True}))
    result = await fetch_json(url)
    assert result == {"ok": True}
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["accept"] == "application/json"
    assert request.extensions["timeout"] == {
        "connect": 10.0,
        "pool": 10.0,
        "read": 30.0,
        "write": 30.0,
    }


@pytest.mark.asyncio
@respx.mock
async def test_http_non_200_fails_without_retry():
    url = "https://models.dev/experimental/v2.0/api.json"
    route = respx.get(url).mock(return_value=httpx.Response(500))
    with pytest.raises(FetchError, match="returned 500"):
        await fetch_json(url)
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_http_native_task_cancellation_interrupts_in_flight_request():
    """CLI 经 asyncio 任务取消收摊；这里锁原生取消，不冒充用户 abort 词汇。"""
    url = "https://models.dev/experimental/v2.0/api.json"
    started = asyncio.Event()
    never = asyncio.Event()

    async def hold_request(_request):
        started.set()
        await never.wait()
        return httpx.Response(200, json={})

    respx.get(url).mock(side_effect=hold_request)
    task = asyncio.create_task(fetch_json(url))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
