"""agent_name 透传链路测试（CreateAgentSessionOptions → AgentSession）。"""

from pathlib import Path

import pytest
from nova_harness.core.agent_session.services import AgentSessionServices
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.sdk import create_agent_session_from_services
from nova_harness.core.types.session.config import CreateAgentSessionOptions


def _write_agent(agents_dir: Path, name: str) -> None:
    """写一个 agent 组合声明文件（``agents/<name>.yaml``，name 取文件名 stem）。"""
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.yaml").write_text(
        f"description: {name} desc\n", encoding="utf-8"
    )


@pytest.fixture
def two_agent_dirs(tmp_path: Path):
    """准备两个 agent（aaa_agent 字母序在前，用于验证显式选择不被 names[0] 抢走）。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    _write_agent(cwd / ".nova" / "agents", "aaa_agent")
    _write_agent(cwd / ".nova" / "agents", "zzz_agent")
    return cwd, agent_dir


async def _create_session(cwd, agent_dir, options):
    services = await AgentSessionServices.create(
        cwd=str(cwd), agent_dir=str(agent_dir), project_trusted=True
    )
    session_manager = SessionManager.in_memory(str(cwd))
    return await create_agent_session_from_services(services, session_manager, options)


@pytest.mark.asyncio
async def test_explicit_agent_name_wins_over_first_available(two_agent_dirs):
    """显式 agent_name 必须压过字母序兜底（链路曾整体断裂，永远落到 names[0]）。"""
    cwd, agent_dir = two_agent_dirs
    result = await _create_session(
        cwd, agent_dir, CreateAgentSessionOptions(agent_name="zzz_agent")
    )
    assert result.session.agent_manager.current == "zzz_agent"
    assert result.session.tools_manager.agent_name == "zzz_agent"


@pytest.mark.asyncio
async def test_default_agent_name_falls_back_to_first(two_agent_dirs):
    """未显式指定时回退到第一个可用 agent。"""
    cwd, agent_dir = two_agent_dirs
    result = await _create_session(cwd, agent_dir, CreateAgentSessionOptions())
    assert result.session.agent_manager.current == "aaa_agent"


@pytest.mark.asyncio
async def test_unknown_agent_name_raises(two_agent_dirs):
    """显式配置了不存在的 agent 名称时立即报错（不能静默落到别的 agent）。"""
    cwd, agent_dir = two_agent_dirs
    with pytest.raises(ValueError, match="not found"):
        await _create_session(
            cwd, agent_dir, CreateAgentSessionOptions(agent_name="nonexistent")
        )


@pytest.mark.asyncio
async def test_change_agent_rebuilds_runtime(two_agent_dirs):
    """change_agent 后 SystemPromptManager 与 ToolsManager 都指向新 agent。"""
    cwd, agent_dir = two_agent_dirs
    result = await _create_session(
        cwd, agent_dir, CreateAgentSessionOptions(agent_name="aaa_agent")
    )
    session = result.session

    await session.change_agent("zzz_agent")

    assert session.agent_manager.current == "zzz_agent"
    assert session.tools_manager.agent_name == "zzz_agent"


@pytest.mark.asyncio
async def test_change_agent_unknown_name_raises(two_agent_dirs):
    """change_agent 到不存在的名称时报错。"""
    cwd, agent_dir = two_agent_dirs
    result = await _create_session(cwd, agent_dir, CreateAgentSessionOptions())
    with pytest.raises(ValueError, match="not found"):
        await result.session.change_agent("nonexistent")
