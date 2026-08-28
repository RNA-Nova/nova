"""ToolExecContext.agents 注入测试（subagent 工具的注册表消费面）。

- 有 loader：``AgentSession.get_tool_exec_context()`` 现取
  ``resource_loader.get_agents()`` 快照；
- loader 异常/非 dict：防御为空注册表；
- NULL 兜底：``NULL_TOOL_EXEC_CONTEXT.agents == {}``。
"""

from pathlib import Path

import pytest

from nova_harness.core.agent_session.services import AgentSessionServices
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.sdk import create_agent_session_from_services
from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolExecContext,
)
from nova_harness.core.types.session.config import CreateAgentSessionOptions


def _write_agent(agents_dir: Path, name: str) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.yaml").write_text(
        f"description: {name} desc\n", encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_exec_context_agents_snapshot_from_loader(tmp_path: Path):
    """有 loader：执行期上下文携带会话 agents 注册表快照。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    _write_agent(cwd / ".nova" / "agents", "aaa_agent")
    _write_agent(cwd / ".nova" / "agents", "zzz_agent")

    services = await AgentSessionServices.create(
        cwd=str(cwd), agent_dir=str(agent_dir), project_trusted=True
    )
    session_manager = SessionManager.in_memory(str(cwd))
    result = await create_agent_session_from_services(
        services, session_manager, CreateAgentSessionOptions()
    )

    agents = result.session.get_tool_exec_context().agents
    assert set(agents.keys()) == {"aaa_agent", "zzz_agent"}
    assert agents["aaa_agent"].description == "aaa_agent desc"


def test_null_exec_context_agents_empty():
    """NULL 兜底：无会话来源的执行期上下文 agents 为空注册表。"""
    assert NULL_TOOL_EXEC_CONTEXT.agents == {}
    # 冻结值对象默认形态同样为空
    assert ToolExecContext().agents == {}
