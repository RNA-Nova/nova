"""
AgentSession 单元测试：不依赖真实模型，验证消息发送、思考级别等核心行为。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from nova_ai import AssistantMessage, TextContent, ThinkingLevel, Usage

from nova_harness.core import AgentSession
from nova_harness.core.agent_session.options import AgentSessionConfig
from nova_harness.core.harness.system_prompt.builder import (
    compose_system_prompt,
    render_guidelines,
    render_tools,
)
from nova_harness.core.types.agent import NewSessionOptions
from nova_harness.core.types.agent_config import AgentConfig, ToolInfo
from nova_harness.core.types.events import SessionCompactEvent
from nova_harness.core.types.tools import ToolDefinition


@pytest.fixture
def session():
    """构造一个使用 mock 依赖的 AgentSession。"""
    agent = MagicMock()
    agent.state.model = MagicMock()
    agent.state.model.reasoning = True
    agent.state.model.context_window = 128000
    agent.state.thinking_level = ThinkingLevel.MEDIUM
    agent.state.is_streaming = False
    agent.state.messages = []
    agent.subscribe.return_value = lambda: None

    session_manager = MagicMock()
    settings_manager = MagicMock()
    settings_manager.get_steering_mode.return_value = "none"
    settings_manager.get_follow_up_mode.return_value = "none"
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
    resource_loader.get_prompts.return_value = {"prompts": []}
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
    return AgentSession(config), agent, session_manager, settings_manager


def test_new_session_options_default():
    """NewSessionOptions 默认值可正常构造，不应引用不存在的字段。"""
    opts = NewSessionOptions()
    assert opts.parent_session is None
    assert opts.setup is None


def test_new_session_options_with_parent():
    """NewSessionOptions 可携带 parent_session。"""
    opts = NewSessionOptions(parent_session="parent.jsonl")
    assert opts.parent_session == "parent.jsonl"


@pytest.mark.asyncio
async def test_send_custom_message_appends_entry(session):
    """send_custom_message 应调用正确的 session_manager 方法追加条目。"""
    sess, agent, session_manager, _ = session
    await sess.send_custom_message(
        {"custom_type": "note", "content": "hello", "display": True}
    )
    agent.append_message.assert_called_once()
    session_manager.append_custom_message_entry.assert_called_once_with(
        "note", "hello", True, None
    )


@pytest.mark.asyncio
async def test_set_thinking_level_none(session):
    """set_thinking_level('none') 不应抛错，且应把思考级别设为 None。"""
    sess, agent, session_manager, settings_manager = session
    await sess.set_thinking_level("none")
    agent.set_thinking_level.assert_called_once_with(None)
    session_manager.append_thinking_level_change.assert_called_once_with(None)
    settings_manager.set_default_thinking_level.assert_called_once_with(None)


def test_is_retryable_error_skips_context_overflow(session):
    """上下文溢出错误不应被判定为可重试。"""
    sess, agent, _, _ = session
    agent.state.model.context_window = 100
    msg = AssistantMessage(
        content=[TextContent(text="context overflow")],
        stop_reason="error",
        error_message="context length exceeded",
    )
    # 构造 usage 使 input + cache_read 超过 context_window
    msg.usage = Usage(input=80, cache_read=30, output=5)

    assert sess._retry.is_retryable_error(msg) is False


def test_render_tools_uses_prompt_snippet():
    """render_tools 应优先使用 ToolDefinition 的 prompt_snippet。"""
    tools = [ToolInfo(name="bash", description="Run shell command")]
    definitions = {
        "bash": ToolDefinition(
            name="bash",
            description="Run shell command",
            prompt_snippet="bash: run commands in the project directory",
        )
    }
    md = render_tools(tools, tool_definitions=definitions)
    assert "bash: run commands in the project directory" in md
    assert "Run shell command" not in md


def test_render_guidelines():
    """render_guidelines 应输出规范列表。"""
    md = render_guidelines(["Use absolute paths.", "No interactive commands."])
    assert "# Tool Guidelines" in md
    assert "Use absolute paths." in md
    assert "No interactive commands." in md


def test_compose_system_prompt_includes_guidelines():
    """compose_system_prompt 应把激活工具的 prompt_guidelines 渲染到提示词中。"""
    config = AgentConfig(
        name="test",
        agent_dir="",
        description="test agent",
        tools=[ToolInfo(name="bash", description="Run shell command")],
    )
    definitions = {
        "bash": ToolDefinition(
            name="bash",
            description="Run shell command",
            prompt_guidelines=["Use absolute paths."],
        )
    }
    prompt = compose_system_prompt(
        config,
        selected_tools=["bash"],
        tool_definitions=definitions,
    )
    assert "# Tool Guidelines" in prompt
    assert "Use absolute paths." in prompt


@pytest.mark.asyncio
async def test_compact_emits_session_compact_event(session):
    """compact() 成功完成后应发射 session_compact 事件。"""
    sess, agent, session_manager, _ = session

    # 模拟鉴权和压缩依赖
    sess.model_registry.get_api_key = AsyncMock(return_value="fake-key")
    session_manager.get_branch.return_value = []
    session_manager.build_session_context.return_value.messages = []

    events = []
    sess.subscribe(events.append)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "nova_harness.core.harness.compaction.compaction.prepare_compaction",
            lambda _entries, _settings: MagicMock(
                first_kept_entry_id="e1",
                messages_to_summarize=[],
                turn_prefix_messages=[],
                is_split_turn=False,
                tokens_before=100,
            ),
        )
        mp.setattr(
            "nova_harness.core.harness.compaction.compaction.compact",
            AsyncMock(
                return_value=MagicMock(
                    summary="summary",
                    first_kept_entry_id="e1",
                    tokens_before=100,
                    details=None,
                )
            ),
        )
        await sess.compact()

    compact_events = [e for e in events if isinstance(e, SessionCompactEvent)]
    assert len(compact_events) == 1
    assert compact_events[0].from_extension is False
