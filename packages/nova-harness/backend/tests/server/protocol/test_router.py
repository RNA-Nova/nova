"""JSON-RPC 方法路由测试。"""

import pytest

from nova_harness.server.protocol import (
    JSONRPCError,
    MethodRegistry,
    build_notification,
    build_request,
)


@pytest.mark.asyncio
async def test_register_and_dispatch_sync_handler():
    registry = MethodRegistry()
    registry.register("echo", lambda params: params)

    req = build_request("echo", {"x": 1}, id=1)
    resp = await registry.dispatch(req)

    assert resp is not None
    assert resp.id == 1
    assert resp.result == {"x": 1}


@pytest.mark.asyncio
async def test_register_and_dispatch_async_handler():
    async def handler(params):
        return {"ok": params}

    registry = MethodRegistry()
    registry.register("async_echo", handler)

    req = build_request("async_echo", {"x": 2}, id=2)
    resp = await registry.dispatch(req)

    assert resp is not None
    assert resp.id == 2
    assert resp.result == {"ok": {"x": 2}}


@pytest.mark.asyncio
async def test_method_not_found():
    registry = MethodRegistry()
    req = build_request("missing", {}, id=3)
    resp = await registry.dispatch(req)

    assert resp is not None
    assert resp.id == 3
    assert resp.error["code"] == JSONRPCError.METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_notification_no_response():
    registry = MethodRegistry()
    registry.register("ping", lambda params: "pong")

    notif = build_notification("ping", {})
    resp = await registry.dispatch(notif)

    assert resp is None


@pytest.mark.asyncio
async def test_jsonrpc_error_returned():
    def handler(params):
        raise JSONRPCError(JSONRPCError.INVALID_PARAMS, "bad")

    registry = MethodRegistry()
    registry.register("fail", handler)

    req = build_request("fail", {}, id=4)
    resp = await registry.dispatch(req)

    assert resp is not None
    assert resp.id == 4
    assert resp.error["code"] == JSONRPCError.INVALID_PARAMS
    assert resp.error["message"] == "bad"


@pytest.mark.asyncio
async def test_internal_error_caught():
    registry = MethodRegistry()
    registry.register("boom", lambda params: 1 / 0)

    req = build_request("boom", {}, id=5)
    resp = await registry.dispatch(req)

    assert resp is not None
    assert resp.id == 5
    assert resp.error["code"] == JSONRPCError.INTERNAL_ERROR


@pytest.mark.asyncio
async def test_notification_error_silent():
    registry = MethodRegistry()
    registry.register("boom", lambda params: 1 / 0)

    notif = build_notification("boom", {})
    resp = await registry.dispatch(notif)

    assert resp is None


def test_register_many_and_unregister():
    registry = MethodRegistry()
    registry.register_many({"a": lambda p: 1, "b": lambda p: 2})

    assert registry.has("a")
    assert registry.has("b")

    registry.unregister("a")
    assert not registry.has("a")
    assert registry.has("b")
