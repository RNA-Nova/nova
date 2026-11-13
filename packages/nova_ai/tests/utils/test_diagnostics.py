"""utils/diagnostics.py 测试——诊断追加单点（时间戳 + 出口脱敏）。"""

from nova_protocol import AssistantMessage, Usage

from nova_ai.utils.diagnostics import append_diagnostic


def _message() -> AssistantMessage:
    return AssistantMessage(role="assistant", content=[], usage=Usage())


def _first_diagnostic(message: AssistantMessage):
    diagnostics = message.diagnostics
    assert diagnostics is not None
    return diagnostics[0]


class TestAppendDiagnostic:
    def test_appends_with_timestamp(self):
        message = _message()
        append_diagnostic(message, {"type": "x", "details": {"reason": "r"}})
        entry = _first_diagnostic(message)
        assert entry["type"] == "x"
        assert isinstance(entry["timestamp"], int)
        assert entry["details"] == {"reason": "r"}

    def test_secret_values_redacted(self):
        """错误报文里的凭据形态在出口即脱敏。"""
        message = _message()
        append_diagnostic(
            message,
            {
                "type": "anthropic_thread_fallback",
                "error": {"message": "401: invalid x-api-key: sk-ant-secret123"},
                "details": {"auth": "Bearer sk-abcdef0123456789"},
            },
        )
        entry = _first_diagnostic(message)
        assert "sk-ant-secret123" not in entry["error"]["message"]
        assert "sk-abcdef0123456789" not in entry["details"]["auth"]

    def test_nested_containers_redacted(self):
        message = _message()
        append_diagnostic(
            message,
            {
                "type": "x",
                "details": {
                    "items": [{"token": "sk-live-key-1234567890"}],
                    "tuple": ("Bearer sk-tuple-key-1234567890",),
                },
            },
        )
        assert "sk-live-key-1234567890" not in str(_first_diagnostic(message))
        assert "sk-tuple-key-1234567890" not in str(_first_diagnostic(message))
