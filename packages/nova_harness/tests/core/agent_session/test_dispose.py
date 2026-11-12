"""AgentSession.dispose 的跨包资源清理接线测试。"""

from types import SimpleNamespace

from nova_harness.core.agent_session.agent import AgentSession


def test_dispose_cleans_nova_ai_session_resources(monkeypatch):
    cleaned = []
    monkeypatch.setattr(
        "nova_harness.core.agent_session.agent.cleanup_session_resources",
        cleaned.append,
    )

    class Tasks:
        def close(self):
            pass

        def cancel_all(self):
            pass

    session = SimpleNamespace(
        _tasks=Tasks(),
        abort_retry=lambda: None,
        abort_compaction=lambda: None,
        abort_branch_summary=lambda: None,
        abort_user_tool=lambda: None,
        agent=SimpleNamespace(abort=lambda: None),
        _extension_runner=None,
        _disconnect_from_agent=lambda: None,
        _event_listeners=[],
        session_id="session-1",
    )
    AgentSession.dispose(session)
    assert cleaned == ["session-1"]
