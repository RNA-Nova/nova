"""
Nova Harness 共享测试 fixtures。

提供 mock 依赖，用于快速构造 AgentSession 及其子系统，
避免每个测试文件重复构造 MagicMock。
"""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from nova_ai import AssistantMessage, TextContent, ThinkingLevel, Usage

from nova_harness.core import AgentSession
from nova_harness.core.agent_session.options import AgentSessionConfig
from nova_harness.core.types.agent_config import AgentConfig


@pytest.fixture
def mock_agent():
    """构造一个可直接传入 AgentSession 的 mock Agent。"""
    agent = MagicMock()
    agent.state.model = MagicMock()
    agent.state.model.provider = "test"
    agent.state.model.id = "test-model"
    agent.state.model.reasoning = True
    agent.state.model.context_window = 128000
    agent.state.thinking_level = ThinkingLevel.MEDIUM
    agent.state.is_streaming = False
    agent.state.messages = []
    agent.state.tools = []
    agent.state.system_prompt = ""
    agent.steering_mode = "one-at-a-time"
    agent.follow_up_mode = "one-at-a-time"

    def _subscribe(callback):
        agent._event_callback = callback
        return lambda: None

    agent.subscribe.side_effect = _subscribe
    agent.prompt = AsyncMock()
    agent.continue_ = AsyncMock()
    agent.steer = MagicMock()
    agent.follow_up = MagicMock()
    agent.clear_all_queues = MagicMock()
    agent.has_queued_messages.return_value = False
    agent.wait_for_idle = AsyncMock()
    agent.abort = MagicMock()
    return agent


@pytest.fixture
def mock_session_manager():
    """构造一个 mock SessionManager。"""
    sm = MagicMock()
    sm.get_session_id.return_value = "session-1"
    sm.get_session_file.return_value = "/tmp/session.jsonl"
    sm.get_session_name.return_value = None
    sm.get_cwd.return_value = "/tmp"
    sm.get_branch.return_value = []
    sm.get_entries.return_value = []
    sm.build_session_context.return_value = MagicMock(
        messages=[],
        active_tool_names=[],
        model=None,
        thinking_level=None,
    )
    return sm


@pytest.fixture
def mock_settings_manager():
    """构造一个 mock SettingsManager，返回默认设置。"""
    sm = MagicMock()
    sm.get_steering_mode.return_value = "one-at-a-time"
    sm.get_follow_up_mode.return_value = "one-at-a-time"
    sm.get_retry_settings.return_value = MagicMock(
        enabled=False,
        max_retries=3,
        base_delay_ms=1000,
        max_delay_ms=60000,
    )
    sm.get_compaction_settings.return_value = MagicMock(
        enabled=False,
        reserve_tokens=16384,
        keep_recent_tokens=1000,
    )
    sm.get_branch_summary_settings.return_value = MagicMock(reserve_tokens=16384)
    sm.get_default_thinking_level.return_value = ThinkingLevel.MEDIUM
    sm.get_default_provider.return_value = None
    sm.get_default_model.return_value = None
    sm.get_shell_command_prefix.return_value = None
    sm.get_shell_path.return_value = None
    sm.get_thinking_budgets.return_value = None
    return sm


@pytest.fixture
def mock_system_prompt_manager():
    """构造一个 mock SystemPromptManager。"""
    spm = MagicMock()
    spm.get_default_active_tool_names.return_value = []
    spm.get_active_tool_names.return_value = []
    spm.get_available_tool_names.return_value = []
    spm.build_system_prompt.return_value = ""
    spm.set_active_tools = MagicMock()
    spm.set_extension_tools = MagicMock()
    spm.set_tool_definitions = MagicMock()
    return spm


@pytest.fixture
def mock_resource_loader():
    """构造一个 mock ResourceLoader。"""
    rl = MagicMock()
    rl.get_prompts.return_value = {"prompts": [], "diagnostics": []}
    rl.get_extensions.return_value = MagicMock(extensions=[], diagnostics=[])
    rl.get_agents.return_value = {}
    rl.get_agent_names.return_value = []
    rl.get_skills.return_value = {}
    rl.get_tools.return_value = {}
    rl.reload = AsyncMock()
    rl.extend_resources = MagicMock()
    return rl


@pytest.fixture
def mock_model_registry():
    """构造一个 mock ModelRegistry。"""
    mr = MagicMock()
    mr.find.return_value = None
    mr.get_available.return_value = []
    mr.get_api_key = AsyncMock(return_value="fake-key")
    return mr


@pytest.fixture
def make_agent_session(
    mock_agent,
    mock_session_manager,
    mock_settings_manager,
    mock_system_prompt_manager,
    mock_resource_loader,
    mock_model_registry,
):
    """返回一个工厂函数，用于按给定覆盖构造 AgentSession。"""

    def _make(
        *,
        agent=None,
        session_manager=None,
        settings_manager=None,
        system_prompt_manager=None,
        resource_loader=None,
        model_registry=None,
        cwd: str = "/tmp",
        initial_active_tool_names: Optional[List[str]] = None,
        base_tools_override: Optional[Dict[str, Any]] = None,
    ) -> AgentSession:
        config = AgentSessionConfig.model_construct(
            agent=agent or mock_agent,
            session_manager=session_manager or mock_session_manager,
            settings_manager=settings_manager or mock_settings_manager,
            cwd=cwd,
            system_prompt_manager=system_prompt_manager or mock_system_prompt_manager,
            resource_loader=resource_loader or mock_resource_loader,
            model_registry=model_registry or mock_model_registry,
            scoped_models=[],
            initial_active_tool_names=initial_active_tool_names or [],
            base_tools_override=base_tools_override or {},
        )
        return AgentSession(config)

    return _make


@pytest.fixture
def agent_session(make_agent_session):
    """构造一个使用全 mock 依赖的 AgentSession。"""
    return make_agent_session()


@pytest.fixture
def assistant_message_factory():
    """返回一个构造 AssistantMessage 的工厂。"""

    def _make(
        text: str = "hello",
        stop_reason: str = "stop",
        error_message: Optional[str] = None,
        usage: Usage = None,
    ) -> AssistantMessage:
        if usage is None:
            usage = Usage()
        return AssistantMessage(
            role="assistant",
            content=[TextContent(type="text", text=text)],
            provider="test",
            model="test-model",
            stop_reason=stop_reason,
            error_message=error_message,
            usage=usage,
        )

    return _make


@pytest.fixture
def agent_config_factory():
    """返回一个构造 AgentConfig 的工厂。"""

    def _make(
        name: str = "base_agent",
        description: str = "",
        tools=None,
        sections=None,
        user_sections=None,
    ) -> AgentConfig:
        return AgentConfig(
            name=name,
            agent_dir="",
            description=description,
            tools=tools or [],
            sections=sections or [],
            user_sections=user_sections or [],
        )

    return _make
