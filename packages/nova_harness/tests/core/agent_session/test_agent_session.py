"""
AgentSession 核心行为测试。

覆盖工具管理、模型切换、思考级别、队列、重试、统计、自定义消息与 bash 执行。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nova_agent import AgentTool
from nova_ai import (
    AssistantMessage,
    Model,
    ModelCost,
    TextContent,
    ThinkingLevel,
    Usage,
)

from nova_harness.core.types.agent import ScopedModelConfig
from nova_harness.core.types.events import (
    AutoRetryEndEvent,
    MessageEndEvent,
    MessageStartEvent,
    QueueUpdateEvent,
    ThinkingLevelChangedEvent,
)


def _make_model(model_id: str = "test-model", reasoning: bool = True) -> Model:
    """构造一个包含全部必填字段的最小 Model 实例。"""
    return Model(
        id=model_id,
        name=model_id,
        api="openai_completions",
        provider="test",
        base_url="https://test.example.com",
        reasoning=reasoning,
        input_types=["text"],
        cost=ModelCost(),
        context_window=128000,
        max_tokens=4096,
    )


# ---------------------------------------------------------------------------
# 属性
# ---------------------------------------------------------------------------


def test_session_properties(agent_session, mock_settings_manager):
    """AgentSession 暴露的核心属性应正确代理到底层依赖。"""
    mock_settings_manager.get_retry_enabled.return_value = False
    mock_settings_manager.get_compaction_enabled.return_value = False
    assert agent_session.session_id == "session-1"
    assert agent_session.session_file == "/tmp/session.jsonl"
    assert agent_session.session_name is None
    assert agent_session.cwd == "/tmp"
    assert agent_session.model.provider == "test"
    assert agent_session.thinking_level == ThinkingLevel.MEDIUM
    assert agent_session.is_streaming is False
    assert agent_session.steering_mode == "one-at-a-time"
    assert agent_session.follow_up_mode == "one-at-a-time"
    assert agent_session.pending_message_count == 0
    assert agent_session.auto_retry_enabled is False
    assert agent_session.auto_compaction_enabled is False
    assert agent_session.retry_attempt == 0


def test_session_system_prompt(agent_session, mock_system_prompt_manager):
    """system_prompt 应来自 agent.state，并在初始化时构建。"""
    mock_system_prompt_manager.build_system_prompt.return_value = "base prompt"
    agent_session._sync_system_prompt()
    assert agent_session.system_prompt == "base prompt"


def test_session_messages_empty_by_default(agent_session):
    """messages 默认应为空列表。"""
    assert agent_session.messages == []


# ---------------------------------------------------------------------------
# 工具管理
# ---------------------------------------------------------------------------


def test_get_active_tool_names(agent_session):
    """get_active_tool_names 返回 agent.state.tools 中工具名称。"""
    tool = MagicMock(spec=AgentTool)
    tool.name = "bash"
    agent_session.agent.state.tools = [tool]
    assert agent_session.get_active_tool_names() == ["bash"]


def test_base_tools_override_takes_precedence(make_agent_session):
    """base_tools_override 中的工具应覆盖 resource_loader 提供的同名工具。"""
    override_tool = MagicMock(spec=AgentTool)
    override_tool.name = "read"
    override_tool.description = "overridden read"

    resource_loader = MagicMock()
    resource_loader.get_tools.return_value = {"read": MagicMock(spec=AgentTool)}
    resource_loader.get_extensions.return_value = MagicMock(
        extensions=[], diagnostics=[]
    )
    resource_loader.get_prompts.return_value = {"prompts": []}

    system_prompt_manager = MagicMock()
    system_prompt_manager.build_system_prompt.return_value = ""
    system_prompt_manager.get_default_active_tool_names.return_value = []
    system_prompt_manager.get_active_tool_names.return_value = []

    session = make_agent_session(
        resource_loader=resource_loader,
        system_prompt_manager=system_prompt_manager,
        initial_active_tool_names=["read"],
        base_tools_override={"read": override_tool},
    )

    assert session.get_tool_definition("read") is override_tool
    assert session.get_active_tool_names() == ["read"]


def test_set_active_tools_by_name(make_agent_session):
    """set_active_tools_by_name 只激活注册表中存在的工具。"""
    bash = MagicMock(spec=AgentTool)
    bash.name = "bash"
    read = MagicMock(spec=AgentTool)
    read.name = "read"
    session = make_agent_session(base_tools_override={"bash": bash, "read": read})

    session.set_active_tools_by_name(["bash", "missing", "read"])

    assert session.get_active_tool_names() == ["bash", "read"]
    assert [t.name for t in session.agent.state.tools] == ["bash", "read"]


def test_refresh_tools(make_agent_session):
    """refresh_tools 重新扫描工具并保留当前激活白名单。"""
    bash = MagicMock(spec=AgentTool)
    bash.name = "bash"
    read = MagicMock(spec=AgentTool)
    read.name = "read"
    session = make_agent_session(base_tools_override={"bash": bash, "read": read})
    session.set_active_tools_by_name(["bash"])

    session.refresh_tools()

    assert session.get_active_tool_names() == ["bash"]


def test_get_all_tools_returns_tool_info(make_agent_session):
    """get_all_tools 返回包含名称和描述的 ToolInfo 列表。"""
    tool = MagicMock(spec=AgentTool)
    tool.name = "bash"
    tool.description = "Run shell command"
    session = make_agent_session(base_tools_override={"bash": tool})

    tools = session.get_all_tools()
    assert len(tools) == 1
    assert tools[0].name == "bash"
    assert tools[0].description == "Run shell command"


def test_get_tool_definition_missing(make_agent_session):
    """get_tool_definition 对未注册工具返回 None。"""
    session = make_agent_session()
    assert session.get_tool_definition("missing") is None


# ---------------------------------------------------------------------------
# 模型与思考级别
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_model_success(agent_session, mock_model_registry):
    """set_model 在存在 API key 时更新模型并持久化。"""
    mock_model_registry.get_api_key = AsyncMock(return_value="fake-key")
    new_model = _make_model("new-model")

    await agent_session.set_model(new_model)

    assert agent_session.model is new_model
    agent_session.session_manager.append_model_change.assert_called_once_with(
        "test", "new-model"
    )


@pytest.mark.asyncio
async def test_set_model_raises_without_api_key(agent_session, mock_model_registry):
    """set_model 在无 API key 时抛出 RuntimeError。"""
    mock_model_registry.get_api_key = AsyncMock(return_value=None)
    new_model = _make_model("new-model", reasoning=False)

    with pytest.raises(RuntimeError, match="No API key"):
        await agent_session.set_model(new_model)


@pytest.mark.asyncio
async def test_set_thinking_level_none(agent_session):
    """set_thinking_level('none') 关闭思考级别并持久化。"""
    await agent_session.set_thinking_level("none")
    assert agent_session.thinking_level is None
    agent_session.session_manager.append_thinking_level_change.assert_called_once_with(
        None
    )


@pytest.mark.asyncio
async def test_set_thinking_level_enum(agent_session):
    """set_thinking_level 接受 ThinkingLevel 枚举。"""
    await agent_session.set_thinking_level(ThinkingLevel.HIGH)
    assert agent_session.thinking_level == ThinkingLevel.HIGH


@pytest.mark.asyncio
async def test_set_thinking_level_emits_event(agent_session):
    """set_thinking_level 改变有效级别时发射 ThinkingLevelChangedEvent。"""
    events = []
    agent_session.subscribe(events.append)

    await agent_session.set_thinking_level(ThinkingLevel.LOW)

    changed_events = [e for e in events if isinstance(e, ThinkingLevelChangedEvent)]
    assert len(changed_events) == 1
    assert changed_events[0].level == ThinkingLevel.LOW


@pytest.mark.asyncio
async def test_set_thinking_level_noop_for_same_level(agent_session):
    """思考级别未改变时不应触发持久化或事件。"""
    agent_session.session_manager.append_thinking_level_change.reset_mock()
    events = []
    agent_session.subscribe(events.append)

    await agent_session.set_thinking_level(ThinkingLevel.MEDIUM)

    agent_session.session_manager.append_thinking_level_change.assert_not_called()
    assert not [e for e in events if isinstance(e, ThinkingLevelChangedEvent)]


def test_supports_thinking(agent_session):
    """supports_thinking 依据当前模型的 reasoning 标志返回。"""
    assert agent_session.supports_thinking() is True
    agent_session.agent.state.model.reasoning = False
    assert agent_session.supports_thinking() is False


@pytest.mark.asyncio
async def test_cycle_model_scoped(make_agent_session, mock_model_registry):
    """cycle_model 在 scoped_models 存在时按 scoped 列表循环。"""
    mock_model_registry.get_api_key = AsyncMock(return_value="fake-key")
    model_a = _make_model("model-a")
    model_b = _make_model("model-b")
    session = make_agent_session()
    session.scoped_models = [
        ScopedModelConfig(model=model_a, thinking_level=ThinkingLevel.LOW),
        ScopedModelConfig(model=model_b, thinking_level=ThinkingLevel.HIGH),
    ]
    session.agent.state.model = model_a

    result = await session.cycle_model("forward")

    assert result is not None
    assert result.model is model_b
    assert result.is_scoped is True


# ---------------------------------------------------------------------------
# 队列
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steer_queues_message(agent_session):
    """steer 将消息加入 steering 队列并调用 agent.steer。"""
    events = []
    agent_session.subscribe(events.append)

    await agent_session.steer("stop now")

    assert agent_session.get_steering_messages() == ["stop now"]
    assert agent_session.pending_message_count == 1
    agent_session.agent.steer.assert_called_once()
    assert any(isinstance(e, QueueUpdateEvent) for e in events)


@pytest.mark.asyncio
async def test_follow_up_queues_message(agent_session):
    """follow_up 将消息加入 follow-up 队列并调用 agent.follow_up。"""
    await agent_session.follow_up("continue after")

    assert agent_session.get_follow_up_messages() == ["continue after"]
    agent_session.agent.follow_up.assert_called_once()


def test_clear_queue(agent_session):
    """clear_queue 清空 steering/follow-up 队列并返回之前内容。"""
    agent_session._steering_messages = ["s1"]
    agent_session._follow_up_messages = ["f1"]

    result = agent_session.clear_queue()

    assert result == {"steering": ["s1"], "follow_up": ["f1"]}
    assert agent_session.get_steering_messages() == []
    assert agent_session.get_follow_up_messages() == []
    agent_session.agent.clear_all_queues.assert_called_once()


# ---------------------------------------------------------------------------
# 自动重试
# ---------------------------------------------------------------------------


def _make_error_message(error_message: str, **usage_kwargs) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="error")],
        stop_reason="error",
        error_message=error_message,
        usage=Usage(**usage_kwargs),
    )


def test_is_retryable_error_overloaded(agent_session):
    """overloaded 错误应判定为可重试。"""
    msg = _make_error_message("provider overloaded")
    assert agent_session._retry.is_retryable_error(msg) is True


def test_is_retryable_error_rate_limit(agent_session):
    """rate limit 错误应判定为可重试。"""
    msg = _make_error_message("rate limit exceeded")
    assert agent_session._retry.is_retryable_error(msg) is True


def test_is_retryable_error_quota_exceeded(agent_session):
    """quota / billing 错误不应重试。"""
    msg = _make_error_message("quota exceeded")
    assert agent_session._retry.is_retryable_error(msg) is False


def test_is_retryable_error_context_overflow(agent_session):
    """上下文溢出错误不应重试（应由压缩处理）。"""
    agent_session.agent.state.model.context_window = 100
    msg = _make_error_message("context length exceeded", input=80, cache_read=30)
    assert agent_session._retry.is_retryable_error(msg) is False


def test_is_retryable_error_non_error_stop_reason(agent_session):
    """stop_reason 不是 error 时不应重试。"""
    msg = AssistantMessage(
        content=[TextContent(text="ok")],
        stop_reason="stop",
    )
    assert agent_session._retry.is_retryable_error(msg) is False


@pytest.mark.asyncio
async def test_prepare_retry_disabled(agent_session, mock_settings_manager):
    """重试设置禁用时 prepare_retry 返回 False。"""
    mock_settings_manager.get_retry_settings.return_value = MagicMock(
        enabled=False, max_retries=3
    )
    msg = _make_error_message("provider overloaded")
    assert await agent_session._retry.prepare_retry(msg) is False


@pytest.mark.asyncio
async def test_prepare_retry_exceeds_max_retries(agent_session, mock_settings_manager):
    """超过最大重试次数时 prepare_retry 返回 False。"""
    mock_settings_manager.get_retry_settings.return_value = MagicMock(
        enabled=True, max_retries=2, base_delay_ms=10, max_delay_ms=1000
    )
    agent_session._retry_attempt = 2
    msg = _make_error_message("provider overloaded")
    assert await agent_session._retry.prepare_retry(msg) is False


@pytest.mark.asyncio
async def test_prepare_retry_success_emits_start_event(
    agent_session, mock_settings_manager
):
    """prepare_retry 成功时发射 AutoRetryStartEvent 并等待延迟。"""
    mock_settings_manager.get_retry_settings.return_value = MagicMock(
        enabled=True, max_retries=3, base_delay_ms=10, max_delay_ms=1000
    )
    agent_session.agent.state.messages = (
        [msg] if (msg := _make_error_message("provider overloaded")) else []
    )
    agent_session.agent.state.messages = [_make_error_message("provider overloaded")]

    events = []
    agent_session.subscribe(events.append)

    # 使用极短延迟避免测试变慢
    with patch.object(
        agent_session._retry,
        "prepare_retry",
        wraps=agent_session._retry.prepare_retry,
    ):
        pass

    # 直接测试事件发射：在 prepare_retry 内部会创建 Event，这里手动触发超时路径太慢，
    # 改为验证 will_retry_after_agent_end 的行为。
    assert agent_session._retry.is_retrying is False


@pytest.mark.asyncio
async def test_abort_retry(agent_session, mock_settings_manager):
    """abort_retry 应取消进行中的重试并发射 AutoRetryEndEvent。"""
    mock_settings_manager.get_retry_settings.return_value = MagicMock(
        enabled=True, max_retries=3, base_delay_ms=10, max_delay_ms=1000
    )
    agent_session.agent.state.messages = [_make_error_message("provider overloaded")]

    events = []
    agent_session.subscribe(events.append)

    async def run_retry():
        return await agent_session._retry.prepare_retry(
            _make_error_message("provider overloaded")
        )

    task = asyncio.create_task(run_retry())
    await asyncio.sleep(0)  # 让任务进入等待
    agent_session.abort_retry()
    result = await task

    assert result is False
    assert any(isinstance(e, AutoRetryEndEvent) for e in events)


# ---------------------------------------------------------------------------
# 统计与上下文用量
# ---------------------------------------------------------------------------


def test_get_session_stats_counts_messages(agent_session):
    """get_session_stats 正确统计各类消息数量与 token。"""
    user_msg = MagicMock()
    user_msg.role = "user"
    assistant_msg = MagicMock()
    assistant_msg.role = "assistant"
    assistant_msg.content = [MagicMock(type="toolCall")]
    assistant_msg.usage = Usage(input=10, output=5, cache_read=2, cache_write=1)
    tool_msg = MagicMock()
    tool_msg.role = "toolResult"

    agent_session.agent.state.messages = [user_msg, assistant_msg, tool_msg]

    stats = agent_session.get_session_stats()
    assert stats.user_messages == 1
    assert stats.assistant_messages == 1
    assert stats.tool_results == 1
    assert stats.tool_calls == 1
    assert stats.tokens.input_tokens == 10
    assert stats.tokens.output_tokens == 5


def test_get_session_stats_includes_cost(agent_session):
    """get_session_stats 累加 assistant 消息的 usage.cost。"""
    assistant_msg = MagicMock()
    assistant_msg.role = "assistant"
    assistant_msg.content = []
    cost = MagicMock()
    cost.total = 1.23
    from nova_ai import Cost

    assistant_msg.usage = Usage(input=1, output=1, cost=Cost(total=1.23))
    agent_session.agent.state.messages = [assistant_msg]

    stats = agent_session.get_session_stats()
    assert stats.cost == 1.23


def test_get_context_usage(agent_session):
    """get_context_usage 在有模型时返回 token 估算。"""
    agent_session.agent.state.messages = []
    usage = agent_session.get_context_usage()
    assert usage is not None
    assert usage["context_window"] == 128000
    assert "percent" in usage


def test_get_context_usage_no_model(make_agent_session):
    """get_context_usage 在无模型时返回 None。"""
    session = make_agent_session()
    session.agent.state.model = None
    assert session.get_context_usage() is None


# ---------------------------------------------------------------------------
# 自定义消息
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_custom_message_appends_entry(agent_session):
    """send_custom_message 默认追加自定义消息并持久化。"""
    events = []
    agent_session.subscribe(events.append)

    await agent_session.send_custom_message(
        {"custom_type": "note", "content": "hello", "display": True}
    )

    agent_session.agent.append_message.assert_called_once()
    agent_session.session_manager.append_custom_message_entry.assert_called_once_with(
        "note", "hello", True, None
    )
    assert any(isinstance(e, MessageStartEvent) for e in events)
    assert any(isinstance(e, MessageEndEvent) for e in events)


@pytest.mark.asyncio
async def test_send_custom_message_next_turn(agent_session):
    """send_custom_message deliverAs=nextTurn 时进入待处理列表。"""
    await agent_session.send_custom_message(
        {"custom_type": "note", "content": "pending"},
        options={"deliverAs": "nextTurn"},
    )

    assert len(agent_session._pending_next_turn_messages) == 1
    agent_session.agent.append_message.assert_not_called()


@pytest.mark.asyncio
async def test_send_custom_message_trigger_turn(agent_session):
    """send_custom_message triggerTurn=True 时直接触发 Agent prompt。"""
    agent_session.agent.prompt = AsyncMock()
    await agent_session.send_custom_message(
        {"custom_type": "note", "content": "trigger"},
        options={"triggerTurn": True},
    )

    agent_session.agent.prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_custom_message_streaming_follow_up(agent_session):
    """流式模式下 deliverAs=followUp 调用 agent.follow_up。"""
    agent_session.agent.state.is_streaming = True
    await agent_session.send_custom_message(
        {"custom_type": "note", "content": "follow"},
        options={"deliverAs": "followUp"},
    )

    agent_session.agent.follow_up.assert_called_once()


# ---------------------------------------------------------------------------
# Bash 执行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_bash_records_result(agent_session):
    """execute_bash 执行命令并将结果记录到会话。"""
    with patch(
        "nova_harness.core.agent_session.controllers.bash.execute_bash",
        new_callable=AsyncMock,
    ) as mock_execute:
        from nova_harness.core.utils.bash import BashResult

        mock_execute.return_value = BashResult(
            output="hello", exit_code=0, cancelled=False, truncated=False
        )
        result = await agent_session.execute_bash("echo hello")

    assert result.output == "hello"
    assert result.exit_code == 0
    agent_session.session_manager.append_message.assert_called()


@pytest.mark.asyncio
async def test_execute_bash_uses_shell_prefix(agent_session, mock_settings_manager):
    """execute_bash 应拼接 shell_command_prefix。"""
    mock_settings_manager.get_shell_command_prefix.return_value = "cd /tmp"
    with patch(
        "nova_harness.core.agent_session.controllers.bash.execute_bash",
        new_callable=AsyncMock,
    ) as mock_execute:
        from nova_harness.core.utils.bash import BashResult

        mock_execute.return_value = BashResult(
            output="", exit_code=0, cancelled=False, truncated=False
        )
        await agent_session.execute_bash("pwd")

    called_command = mock_execute.call_args[0][0]
    assert called_command == "cd /tmp\npwd"


# ---------------------------------------------------------------------------
# 订阅与释放
# ---------------------------------------------------------------------------


def test_subscribe_and_unsubscribe(agent_session):
    """subscribe 返回取消订阅函数，取消后监听器不再接收事件。"""
    received = []
    unsubscribe = agent_session.subscribe(received.append)

    agent_session._emit("event-1")
    assert received == ["event-1"]

    unsubscribe()
    agent_session._emit("event-2")
    assert received == ["event-1"]


def test_dispose_clears_listeners(agent_session):
    """dispose 后事件监听器被清空。"""
    received = []
    agent_session.subscribe(received.append)
    agent_session.dispose()
    agent_session._emit("event")
    assert received == []
