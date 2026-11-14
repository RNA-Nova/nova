"""session_resources.py 测试——会话级资源清理注册处（pi session-resources 对位）。"""

import pytest
from nova_ai.session_resources import (
    cleanup_session_resources,
    register_session_resource_cleanup,
)


class TestSessionResources:
    def test_cleanup_calls_registered_with_session_id(self):
        seen = []
        unregister = register_session_resource_cleanup(
            lambda session_id: seen.append(session_id)
        )
        try:
            cleanup_session_resources("s-1")
        finally:
            unregister()
        assert seen == ["s-1"]

    def test_unregister_stops_future_calls(self):
        seen = []
        unregister = register_session_resource_cleanup(
            lambda session_id: seen.append(session_id)
        )
        unregister()
        cleanup_session_resources("s-1")
        assert seen == []

    def test_failure_aggregates_and_does_not_block_others(self):
        """单个清理失败不阻其余，汇聚后统一抛出（pi AggregateError 对位）。"""
        calls = []

        def _boom(_session_id):
            raise RuntimeError("boom")

        unregister_boom = register_session_resource_cleanup(_boom)
        unregister_ok = register_session_resource_cleanup(
            lambda session_id: calls.append(session_id)
        )
        try:
            with pytest.raises(ExceptionGroup) as exc_info:
                cleanup_session_resources()
            assert len(exc_info.value.exceptions) == 1
            assert calls == [None]  # 失败的没挡住后面的
        finally:
            unregister_boom()
            unregister_ok()
