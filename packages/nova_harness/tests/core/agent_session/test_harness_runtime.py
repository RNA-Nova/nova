"""
AgentSessionRuntime / AgentSessionServices 结构对齐测试。

验证：
- AgentSessionServices 创建后 diagnostics 字段可用。
- AgentSessionRuntime 通过工厂替换 session 时更新内部状态。
- navigate_tree 位于 AgentSession 内且直接更新 agent.messages。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nova_ai import UserMessage

from nova_harness.core import (
    AgentSession,
    AgentSessionRuntime,
    AgentSessionServices,
)
from nova_harness.core.agent_session.options import AgentSessionConfig
from nova_harness.core.agent_session.runtime import NewSessionOptions
from nova_harness.core.agent_session.services import CreateAgentSessionRuntimeResult
from nova_harness.core.types.diagnostics import AgentSessionRuntimeDiagnostic


def _make_services(tmp_path):
    """构造一个使用 mocks 的 services 对象（绕过 Pydantic 校验）。"""
    return AgentSessionServices.model_construct(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        session_manager=None,
        settings_manager=MagicMock(),
        model_registry=MagicMock(),
        resource_loader=MagicMock(),
        system_prompt_manager=MagicMock(),
        auth_storage=MagicMock(),
        diagnostics=[],
    )


def test_agent_session_services_diagnostics_field(tmp_path):
    """AgentSessionServices 创建后 diagnostics 字段可用。"""
    services = AgentSessionServices.model_construct(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        session_manager=None,
        settings_manager=MagicMock(),
        model_registry=MagicMock(),
        resource_loader=MagicMock(),
        system_prompt_manager=MagicMock(),
        auth_storage=MagicMock(),
        diagnostics=[
            AgentSessionRuntimeDiagnostic(
                type="error", message="provider registration failed"
            )
        ],
    )
    assert len(services.diagnostics) == 1
    assert services.diagnostics[0].type == "error"


@pytest.mark.asyncio
async def test_agent_session_runtime_new_session_uses_factory(tmp_path):
    """new_session 通过 create_runtime 工厂创建新 session。"""
    old_session = MagicMock(spec=AgentSession)
    old_session.session_file = "old.jsonl"
    old_session.session_manager = MagicMock()
    old_session.session_manager.is_persisted.return_value = True
    old_session.session_manager.get_session_dir.return_value = str(
        tmp_path / "sessions"
    )
    old_session.dispose = MagicMock()
    old_session.extension_runner = None

    new_session = MagicMock(spec=AgentSession)
    new_session.extension_runner = None
    new_session.session_manager = MagicMock()
    new_session.agent.state.messages = []

    services = AgentSessionServices.model_construct(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        session_manager=old_session.session_manager,
        settings_manager=MagicMock(),
        model_registry=MagicMock(),
        resource_loader=MagicMock(),
        system_prompt_manager=MagicMock(),
        auth_storage=MagicMock(),
        diagnostics=[],
    )

    new_services = MagicMock(spec=AgentSessionServices)

    factory = AsyncMock(
        return_value=CreateAgentSessionRuntimeResult(
            session=new_session,
            services=new_services,
            diagnostics=[],
            model_fallback_message=None,
        )
    )

    runtime = AgentSessionRuntime(old_session, services, factory)
    result = await runtime.new_session(NewSessionOptions())

    assert result["cancelled"] is False
    assert runtime.session is new_session
    assert runtime.services is new_services
    factory.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_session_navigate_tree_updates_messages_directly():
    """navigate_tree 直接修改 agent.state.messages，不重建 AgentSession。"""
    agent = MagicMock()
    agent.state.model = None
    agent.state.thinking_level = None
    agent.state.messages = []
    agent.set_thinking_level = MagicMock()
    agent.subscribe.return_value = lambda: None

    session_manager = MagicMock()
    session_manager.get_leaf_id.return_value = "leaf-1"
    session_manager.get_entry.return_value = MagicMock(
        type="message",
        message=UserMessage(content=[]),
        parent_id="parent-1",
        id="target-1",
    )
    session_manager.get_entries.return_value = []
    session_manager.build_session_context.return_value = MagicMock(messages=["msg"])

    settings_manager = MagicMock()
    settings_manager.get_default_thinking_level.return_value = None
    settings_manager.get_branch_summary_settings.return_value = MagicMock(
        reserve_tokens=100
    )
    settings_manager.get_steering_mode.return_value = "one-at-a-time"
    settings_manager.get_follow_up_mode.return_value = "one-at-a-time"
    settings_manager.get_retry_settings.return_value = MagicMock(
        enabled=False,
        max_retries=0,
        base_delay_ms=0,
        max_delay_ms=0,
    )
    settings_manager.get_compaction_settings.return_value = MagicMock(enabled=False)

    system_prompt_manager = MagicMock()
    system_prompt_manager.get_default_active_tool_names.return_value = []
    system_prompt_manager.get_active_tool_names.return_value = []
    system_prompt_manager.build_system_prompt.return_value = ""
    system_prompt_manager.set_active_tools = MagicMock()
    system_prompt_manager.set_extension_tools = MagicMock()

    resource_loader = MagicMock()
    resource_loader.get_tools.return_value = {}

    config = AgentSessionConfig.model_construct(
        agent=agent,
        session_manager=session_manager,
        settings_manager=settings_manager,
        cwd="/tmp",
        system_prompt_manager=system_prompt_manager,
        resource_loader=resource_loader,
        model_registry=MagicMock(),
        scoped_models=[],
        initial_active_tool_names=[],
        base_tools_override={},
    )
    session = AgentSession(config)

    with patch(
        "nova_harness.core.harness.compaction.branch_summarization.collect_entries_for_branch_summary",
        return_value=MagicMock(entries=[], common_ancestor_id=None),
    ):
        result = await session.navigate_tree("target-1")

    assert result["cancelled"] is False
    session_manager.branch.assert_called_once_with("parent-1")
    assert agent.state.messages == ["msg"]
