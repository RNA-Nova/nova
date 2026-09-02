"""trust_project 持久化与重载事件回归测试。

锁定：``AgentSession.trust_project`` 必须同时做到——
① 进程内翻转（settings_manager 即时生效）；
② 持久化到 trust.json（此前只做内存翻转，/trust 重启即忘，形同虚设）；
③ ``session.reload()`` 末尾发射 ``session_reloaded``（前端刷新包 UI 贡献的触发点）。
"""

import json

import pytest
from nova_harness.core.sdk import create_agent_session_runtime
from nova_harness.core.types.session.config import CreateAgentSessionOptions


@pytest.mark.asyncio
async def test_trust_project_persists_and_flips(tmp_path, monkeypatch):
    """trust_project：内存翻转 + trust.json 落盘（重启不忘）。"""
    monkeypatch.setenv("HOME", str(tmp_path))  # trust.json 落到隔离 HOME
    rt = await create_agent_session_runtime(
        CreateAgentSessionOptions(cwd=str(tmp_path))
    )
    try:
        session = rt.session
        assert session.settings_manager.is_project_trusted() is False

        session.trust_project(True)

        assert session.settings_manager.is_project_trusted() is True
        trust_file = tmp_path / ".nova" / "agent" / "trust.json"
        assert trust_file.exists(), "trust.json 未持久化"
        assert json.loads(trust_file.read_text()) == {str(tmp_path): True}

        session.trust_project(False)
        assert json.loads(trust_file.read_text()) == {str(tmp_path): False}
    finally:
        await rt.dispose()


@pytest.mark.asyncio
async def test_reload_emits_session_reloaded(tmp_path, monkeypatch):
    """session.reload() 末尾发射 session_reloaded（前端刷新包 UI 贡献）。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    rt = await create_agent_session_runtime(
        CreateAgentSessionOptions(cwd=str(tmp_path))
    )
    try:
        events = []
        rt.session.subscribe(lambda e: events.append(getattr(e, "type", None)))
        await rt.session.reload()
        assert "session_reloaded" in events
    finally:
        await rt.dispose()
