"""JSON-RPC 协议测试。"""

import pytest

from nova_harness.server.protocol import (
    JSONRPCError,
    build_notification,
    build_request,
    build_response,
    parse_message,
)


def test_parse_request():
    msg = parse_message(
        {"jsonrpc": "2.0", "id": 1, "method": "foo", "params": {"x": 1}}
    )
    assert msg.is_request
    assert msg.id == 1
    assert msg.method == "foo"
    assert msg.params == {"x": 1}


def test_parse_notification():
    msg = parse_message({"jsonrpc": "2.0", "method": "bar", "params": {"y": 2}})
    assert msg.is_notification
    assert msg.id is None


def test_parse_response():
    msg = parse_message({"jsonrpc": "2.0", "id": 3, "result": {"ok": True}})
    assert msg.is_response
    assert msg.result == {"ok": True}


def test_parse_invalid():
    with pytest.raises(JSONRPCError):
        parse_message({"method": "foo"})


def test_build_roundtrip():
    req = build_request("test", {"a": 1}, id=5)
    assert req.to_dict() == {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "test",
        "params": {"a": 1},
    }

    resp = build_response(5, {"ok": True})
    assert resp.to_dict() == {"jsonrpc": "2.0", "id": 5, "result": {"ok": True}}

    notif = build_notification("event", {"x": 2})
    assert notif.to_dict() == {"jsonrpc": "2.0", "method": "event", "params": {"x": 2}}
