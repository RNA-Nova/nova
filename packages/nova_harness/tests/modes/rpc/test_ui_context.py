"""RpcUIContext 单元测试。"""

import asyncio

import pytest

from nova_harness.modes.rpc.ui_context import RpcUIContext


@pytest.mark.asyncio
async def test_rpc_ui_context_select_round_trip():
    """select 使用独立的 extension/ui/select method。"""
    requests = []
    ctx = RpcUIContext(
        send_request=requests.append,
        default_timeout=1.0,
        capabilities={"select"},
    )

    async def resolve_later():
        await asyncio.sleep(0.01)
        ctx.resolve_response(requests[0]["params"]["id"], "b")

    asyncio.create_task(resolve_later())
    result = await ctx.select("choose", ["a", "b"])

    assert result == "b"
    assert len(requests) == 1
    assert requests[0]["method"] == "extension/ui/select"
    assert requests[0]["params"]["title"] == "choose"
    assert requests[0]["params"]["options"] == ["a", "b"]
    assert "id" in requests[0]["params"]


@pytest.mark.asyncio
async def test_rpc_ui_context_confirm():
    """confirm 使用 extension/ui/confirm。"""
    requests = []
    ctx = RpcUIContext(
        send_request=requests.append,
        default_timeout=1.0,
        capabilities={"confirm"},
    )

    async def resolve_later():
        await asyncio.sleep(0.01)
        ctx.resolve_response(requests[0]["params"]["id"], True)

    asyncio.create_task(resolve_later())
    result = await ctx.confirm("sure?", "msg")
    assert result is True
    assert requests[0]["method"] == "extension/ui/confirm"


@pytest.mark.asyncio
async def test_rpc_ui_context_confirm_returns_dict():
    """confirm 支持前端返回结构化响应。"""
    requests = []
    ctx = RpcUIContext(
        send_request=requests.append,
        default_timeout=1.0,
        capabilities={"confirm"},
    )

    async def resolve_later():
        await asyncio.sleep(0.01)
        ctx.resolve_response(requests[0]["params"]["id"], {"confirmed": True})

    asyncio.create_task(resolve_later())
    result = await ctx.confirm("sure?", "msg")
    assert result is True


@pytest.mark.asyncio
async def test_rpc_ui_context_timeout():
    """超时返回 None。"""
    ctx = RpcUIContext(
        send_request=lambda p: None,
        default_timeout=0.01,
        capabilities={"input"},
    )
    result = await ctx.input("name")
    assert result is None


def test_rpc_ui_context_notify_is_sync():
    """notify 使用独立的 extension/ui/notify notification。"""
    requests = []
    ctx = RpcUIContext(
        send_request=lambda p: requests.append(p),
        capabilities={"notify"},
    )
    ctx.notify_message("hello", "warning")
    assert requests[0]["method"] == "extension/ui/notify"
    assert requests[0]["params"]["message"] == "hello"
    assert requests[0]["params"]["type"] == "warning"


def test_rpc_ui_context_set_status_is_sync():
    """setStatus 使用独立的 extension/ui/setStatus notification。"""
    requests = []
    ctx = RpcUIContext(
        send_request=lambda p: requests.append(p),
        capabilities={"setStatus"},
    )
    ctx.set_status("model", "gpt-4")
    assert requests[0]["method"] == "extension/ui/setStatus"
    assert requests[0]["params"]["key"] == "model"
    assert requests[0]["params"]["text"] == "gpt-4"


@pytest.mark.asyncio
async def test_rpc_ui_context_unsupported_method_is_noop():
    """前端不支持的方法不会发送请求并返回 cancelled 响应。"""
    requests = []
    ctx = RpcUIContext(send_request=lambda p: requests.append(p))
    resp = await ctx.request("select", {"title": "x"})
    assert resp.cancelled is True
    assert resp.value is None
    assert len(requests) == 0


@pytest.mark.asyncio
async def test_rpc_ui_context_custom_fallback_to_generic_request():
    """非标准 request method 回退到通用 extension/ui/request。"""
    requests = []
    ctx = RpcUIContext(
        send_request=requests.append,
        default_timeout=1.0,
        capabilities={"my_dialog"},
    )

    async def resolve_later():
        await asyncio.sleep(0.01)
        ctx.resolve_response(requests[0]["params"]["id"], {"data": "ok"})

    asyncio.create_task(resolve_later())
    resp = await ctx.request("my_dialog", {"foo": "bar"})
    assert resp.value == {"data": "ok"}
    assert requests[0]["method"] == "extension/ui/request"
    assert requests[0]["params"]["method"] == "my_dialog"


def test_rpc_ui_context_update_capabilities():
    """前端可以通过 update_capabilities 上报能力。"""
    ctx = RpcUIContext(send_request=lambda p: None)
    assert ctx.has_capability("select") is False
    ctx.update_capabilities({"select", "confirm"})
    assert ctx.has_capability("select") is True


def test_rpc_ui_context_terminal_input_event():
    """前端可以通过 handle_event 反向推送 terminal input。"""
    ctx = RpcUIContext(send_request=lambda p: None)
    received = []

    def handler(data: str):
        received.append(data)
        return {"consume": True}

    ctx.on_terminal_input(handler)
    ctx.handle_event("terminalInput", "hello")
    assert received == ["hello"]


def test_rpc_ui_context_state_sync():
    """前端可以通过 update_state 同步编辑器文本等状态。"""
    ctx = RpcUIContext(send_request=lambda p: None)
    ctx.update_state({"editorText": "print('hi')"})
    assert ctx.get_editor_text() == "print('hi')"
