"""AgentSession 会话命令相关方法测试。"""

import os
import tempfile
from unittest.mock import MagicMock

import pytest
from nova_harness.core import AgentSession
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.types.events import SessionStartEvent
from nova_harness.core.types.session.config import AgentSessionConfig


def _make_config(session_manager, **overrides):
    """构造一个最小可用的 AgentSessionConfig。"""
    agent = MagicMock()
    agent.state.messages = []
    agent.state.is_streaming = False
    defaults = {
        "agent": agent,
        "session_manager": session_manager,
        "settings_manager": MagicMock(),
        "cwd": "/tmp",
        "system_prompt_manager": MagicMock(),
        "tools_manager": MagicMock(),
        "resource_loader": MagicMock(),
        "model_runtime": MagicMock(),
        "scoped_models": [],
        "initial_active_tool_names": [],
        "base_tools_override": None,
        "extension_runner_ref": None,
        "session_start_event": None,
    }
    defaults.update(overrides)
    return AgentSessionConfig(**defaults)


@pytest.fixture
def persisted_session_manager(tmp_path):
    """创建持久化的 SessionManager，并写入一条 assistant 消息确保文件已 flush。"""
    session_dir = str(tmp_path / "sessions")
    os.makedirs(session_dir, exist_ok=True)
    sm = SessionManager(
        cwd="/tmp", session_dir=session_dir, session_file=None, persist=True
    )
    # 持久化需要至少一条 assistant 消息才会 flush 文件
    from nova_ai import AssistantMessage, TextContent
    from nova_harness.core.types.session import SessionMessageEntry

    msg = AssistantMessage(
        role="assistant", content=[TextContent(type="text", text="hi")]
    )
    sm.append_message(msg)
    return sm


@pytest.mark.asyncio
async def test_export_session_copies_file(persisted_session_manager, tmp_path):
    """export_session 应把当前会话文件复制到指定路径。"""
    session = AgentSession(_make_config(persisted_session_manager))
    dest = str(tmp_path / "exported.jsonl")

    result = await session.export_session(dest)

    assert os.path.exists(dest)
    assert result["exported_to"] == os.path.abspath(dest)
    with open(dest, "r", encoding="utf-8") as f:
        assert "hi" in f.read()


@pytest.mark.asyncio
async def test_clone_session_creates_new_file(persisted_session_manager):
    """clone_session 应创建新会话文件并切换过去。"""
    original_file = persisted_session_manager.get_session_file()
    session = AgentSession(_make_config(persisted_session_manager))

    result = await session.clone_session()

    assert result["cancelled"] is False
    new_file = session.session_manager.get_session_file()
    assert new_file != original_file
    assert os.path.exists(new_file)
    assert (
        session.session_manager.get_session_id()
        != persisted_session_manager.get_session_id()
    )


@pytest.mark.asyncio
async def test_import_session_copies_and_switches(persisted_session_manager, tmp_path):
    """import_session 应复制外部 JSONL 并切换到该会话。"""
    external = str(tmp_path / "external.jsonl")
    with open(external, "w", encoding="utf-8") as f:
        f.write(
            '{"type":"session","version":2,"id":"imported-id","timestamp":"2024-01-01T00:00:00","cwd":"/tmp"}\n'
        )

    session = AgentSession(_make_config(persisted_session_manager))
    result = await session.import_session(external)

    assert result["cancelled"] is False
    assert session.session_manager.get_session_id() == "imported-id"


@pytest.mark.asyncio
async def test_new_agent_session_clears_session(persisted_session_manager):
    """new_agent_session 应创建新会话并清空消息。"""
    session = AgentSession(_make_config(persisted_session_manager))
    old_id = session.session_manager.get_session_id()

    result = await session.new_agent_session()

    assert result["cancelled"] is False
    assert session.session_manager.get_session_id() != old_id


@pytest.mark.asyncio
async def test_switch_agent_session_opens_other_file(
    persisted_session_manager, tmp_path
):
    """switch_agent_session 应打开指定会话文件。"""
    other_file = str(tmp_path / "other.jsonl")
    with open(other_file, "w", encoding="utf-8") as f:
        f.write(
            '{"type":"session","version":2,"id":"other-id","timestamp":"2024-01-01T00:00:00","cwd":"/tmp"}\n'
        )

    session = AgentSession(_make_config(persisted_session_manager))
    result = await session.switch_agent_session(other_file)

    assert result["cancelled"] is False
    assert session.session_manager.get_session_id() == "other-id"


def test_get_session_info_returns_summary(persisted_session_manager):
    """get_session_info 应返回会话摘要。"""
    session = AgentSession(_make_config(persisted_session_manager))
    info = session.get_session_info()

    assert info["id"] == session.session_manager.get_session_id()
    assert info["cwd"] == "/tmp"
    assert info["persisted"] is True
    assert info["entry_count"] == len(session.session_manager.get_entries())


def test_trust_project_delegates_to_settings_manager(persisted_session_manager):
    """trust_project 应调用 settings_manager.set_project_trusted。"""
    settings = MagicMock()
    session = AgentSession(
        _make_config(persisted_session_manager, settings_manager=settings)
    )

    session.trust_project(True)
    settings.set_project_trusted.assert_called_once_with(True)

    settings.reset_mock()
    session.trust_project(False)
    settings.set_project_trusted.assert_called_once_with(False)
