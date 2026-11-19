"""受控上下文的字段、嵌套恢复与 capture_logs 测试。"""

from typing import Any, cast

import pytest
import structlog
from nova_logging import log_context
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs


def test_context_is_merged_and_nested_values_are_restored():
    with capture_logs(processors=[merge_contextvars]) as entries:
        with log_context(requestId="req-outer"):
            with log_context(provider="kimi-coding", model="kimi-for-coding"):
                structlog.get_logger().info("gateway.request.started")
            structlog.get_logger().info("gateway.request.completed")

    assert entries == [
        {
            "event": "gateway.request.started",
            "requestId": "req-outer",
            "provider": "kimi-coding",
            "model": "kimi-for-coding",
            "log_level": "info",
        },
        {
            "event": "gateway.request.completed",
            "requestId": "req-outer",
            "log_level": "info",
        },
    ]


def test_context_rejects_uncontrolled_fields_and_non_string_values():
    with pytest.raises(ValueError, match="status"):
        with log_context(status="200"):
            pass
    with pytest.raises(TypeError, match="requestId"):
        with log_context(**cast(Any, {"requestId": 1})):
            pass
