"""SSE 事件判别联合的金标与非法输入测试。"""

import pytest
from nova_protocol import NovaErrorKind, Usage
from pydantic import ValidationError

from nova_gateway_protocol import (
    GATEWAY_EVENT_ADAPTER,
    GatewayDone,
    GatewayError,
    GatewayTextDelta,
)


def test_text_delta_roundtrip():
    event = GatewayTextDelta(content_index=0, delta="你好")
    payload = event.dump_wire()
    assert payload == {"type": "text_delta", "contentIndex": 0, "delta": "你好"}
    assert GATEWAY_EVENT_ADAPTER.validate_python(payload) == event


def test_terminal_events_keep_usage_and_error_metadata():
    done = GatewayDone(reason="stop", usage=Usage())
    error = GatewayError(
        reason="error",
        usage=Usage(),
        error_message="slow down",
        error_kind=NovaErrorKind.RATE_LIMITED,
        retry_after_ms=2500,
    )
    assert GATEWAY_EVENT_ADAPTER.validate_python(done.dump_wire()) == done
    restored = GATEWAY_EVENT_ADAPTER.validate_python(error.dump_wire())
    assert restored == error
    assert restored.retry_after_ms == 2500


@pytest.mark.parametrize("value", [True, "0", -1])
def test_invalid_content_index_is_rejected(value):
    with pytest.raises(ValidationError):
        GATEWAY_EVENT_ADAPTER.validate_python(
            {"type": "text_start", "contentIndex": value}
        )


def test_unknown_event_type_is_rejected():
    with pytest.raises(ValidationError):
        GATEWAY_EVENT_ADAPTER.validate_python({"type": "future_event"})
