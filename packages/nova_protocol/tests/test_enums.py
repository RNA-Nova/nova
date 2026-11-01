"""报告分类学枚举（enums.py）测试——NovaErrorKind/WarningKind/RETRYABLE 集合。"""

from nova_protocol import RETRYABLE_ERROR_KINDS, NovaErrorKind, WarningKind


class TestNovaErrorKind:
    def test_wire_values_camel_case(self):
        """线值 camelCase（随 StopReason.TOOL_USE 先例）。"""
        assert NovaErrorKind.HTTP_CONNECTION_FAILED.value == "httpConnectionFailed"
        assert NovaErrorKind.STREAM_DISCONNECTED.value == "streamDisconnected"
        assert NovaErrorKind.REQUEST_TIMEOUT.value == "requestTimeout"
        assert NovaErrorKind.OVERLOADED.value == "overloaded"
        assert NovaErrorKind.RATE_LIMITED.value == "rateLimited"
        assert NovaErrorKind.UPSTREAM_ERROR.value == "upstreamError"
        assert NovaErrorKind.UNAUTHORIZED.value == "unauthorized"
        assert NovaErrorKind.LOGIN_REQUIRED.value == "loginRequired"
        assert NovaErrorKind.USAGE_LIMIT_EXCEEDED.value == "usageLimitExceeded"
        assert NovaErrorKind.BAD_REQUEST.value == "badRequest"
        assert NovaErrorKind.CONTEXT_OVERFLOW.value == "contextOverflow"
        assert NovaErrorKind.MODEL_NOT_FOUND.value == "modelNotFound"
        assert NovaErrorKind.SANDBOX_DENIED.value == "sandboxDenied"
        assert NovaErrorKind.EXECUTOR_UNAVAILABLE.value == "executorUnavailable"
        assert NovaErrorKind.INTERNAL.value == "internal"

    def test_variant_count(self):
        """15 变体（分类学的完整性锁——增减都要过评审）。"""
        assert len(NovaErrorKind) == 15


class TestRetryableErrorKinds:
    def test_membership(self):
        """六个可重试类（408/409/429/5xx/连接层全集）。"""
        assert RETRYABLE_ERROR_KINDS == frozenset(
            {
                NovaErrorKind.HTTP_CONNECTION_FAILED,
                NovaErrorKind.STREAM_DISCONNECTED,
                NovaErrorKind.REQUEST_TIMEOUT,
                NovaErrorKind.OVERLOADED,
                NovaErrorKind.RATE_LIMITED,
                NovaErrorKind.UPSTREAM_ERROR,
            }
        )

    def test_non_retryable_excluded(self):
        """鉴权/配额/溢出/兜底一律不可重试。"""
        for kind in (
            NovaErrorKind.UNAUTHORIZED,
            NovaErrorKind.LOGIN_REQUIRED,
            NovaErrorKind.USAGE_LIMIT_EXCEEDED,
            NovaErrorKind.CONTEXT_OVERFLOW,
            NovaErrorKind.MODEL_NOT_FOUND,
            NovaErrorKind.BAD_REQUEST,
            NovaErrorKind.SANDBOX_DENIED,
            NovaErrorKind.EXECUTOR_UNAVAILABLE,
            NovaErrorKind.INTERNAL,
        ):
            assert kind not in RETRYABLE_ERROR_KINDS


class TestWarningKind:
    def test_values(self):
        assert [k.value for k in WarningKind] == [
            "general",
            "config",
            "capability",
            "deprecation",
        ]


class TestWireEnumValues:
    """线值锁定（schema 漂移门禁之外的第二道——枚举线值即线上/落盘契约）。"""

    def test_known_api_values(self):
        from nova_protocol import KnownApi

        assert KnownApi.OPENAI_COMPLETIONS.value == "openai-completions"
        assert KnownApi.OPENAI_RESPONSES.value == "openai-responses"
        assert KnownApi.ANTHROPIC_MESSAGES.value == "anthropic-messages"

    def test_stop_reason_values(self):
        from nova_protocol import StopReason

        assert StopReason.STOP.value == "stop"
        assert StopReason.TOOL_USE.value == "toolUse"
        assert StopReason.ERROR.value == "error"
        assert StopReason.ABORTED.value == "aborted"

    def test_thinking_level_families(self):
        """请求侧（ThinkingLevel，不含 off）与模型/状态侧（ModelThinkingLevel，含 off）。"""
        from nova_protocol import ModelThinkingLevel, ThinkingLevel

        assert ThinkingLevel.XHIGH.value == "xhigh"
        assert not hasattr(ThinkingLevel, "OFF")
        assert ModelThinkingLevel.OFF.value == "off"

    def test_cache_retention_and_transport(self):
        from nova_protocol import CacheRetention, Transport

        assert CacheRetention.SHORT.value == "short"
        assert Transport.WEBSOCKET.value == "websocket"

    def test_thinking_format_values(self):
        from nova_protocol import ThinkingFormat

        assert ThinkingFormat.OPENAI.value == "openai"
        assert ThinkingFormat.QWEN.value == "qwen"
        assert ThinkingFormat.ZAI.value == "zai"
