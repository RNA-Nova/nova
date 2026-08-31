"""
AgentSessionRuntime 生命周期测试。

验证会话创建、切换、fork、导航与释放流程。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nova_ai import UserMessage

from nova_harness.core import AgentSession, AgentSessionRuntime, AgentSessionServices
from nova_harness.core.types.session import (
    ForkOptions,
    NavigateOptions,
    NewSessionOptions,
)
from nova_harness.core.types.session.factory import CreateAgentSessionRuntimeResult


def _make_old_session():
    """构造一个用于 runtime 测试的旧 session mock。"""
    session = MagicMock(spec=AgentSession)
    session.session_file = "old.jsonl"
    session.session_manager = MagicMock()
    session.session_manager.is_persisted.return_value = True
    session.session_manager.get_session_dir.return_value = "/tmp/sessions"
    session.extension_runner = None
    session.dispose = MagicMock()
    session.bind_extensions = AsyncMock()
    session.navigate_tree = AsyncMock(
        return_value={"cancelled": False, "editorText": None}
    )
    session.agent = MagicMock()
    session.agent.state.messages = []
    return session


def _make_services(session):
    """构造一个使用 mock 的 services 对象。"""
    return AgentSessionServices(
        cwd="/tmp",
        agent_dir="/tmp/agent",
        settings_manager=MagicMock(),
        model_runtime=MagicMock(),
        resource_loader=MagicMock(),
        auth_storage=MagicMock(),
        diagnostics=[],
    )


def _make_factory(new_session, new_services):
    """构造 create_runtime 工厂。"""
    return AsyncMock(
        return_value=CreateAgentSessionRuntimeResult(
            session=new_session,
            services=new_services,
            extensions_result=MagicMock(),
            diagnostics=[],
            model_fallback_message=None,
        )
    )


def test_runtime_properties():
    """runtime 暴露 session/services/cwd/diagnostics 属性。"""
    session = _make_old_session()
    services = _make_services(session)
    runtime = AgentSessionRuntime(session, services, AsyncMock())
    assert runtime.session is session
    assert runtime.services is services
    assert runtime.cwd == "/tmp"
    assert runtime.diagnostics == []
    assert runtime.model_fallback_message is None


@pytest.mark.asyncio
async def test_new_session_uses_factory_and_replaces_session():
    """new_session 通过工厂创建新 session 并替换 runtime 内部状态。"""
    old_session = _make_old_session()
    services = _make_services(old_session)

    new_session = _make_old_session()
    new_session.extension_runner = None
    new_services = _make_services(new_session)

    factory = _make_factory(new_session, new_services)
    runtime = AgentSessionRuntime(old_session, services, factory)

    with patch(
        "nova_harness.core.agent_session.runtime.SessionManager.create"
    ) as mock_create:
        new_sm = MagicMock()
        new_sm.get_session_file.return_value = "new.jsonl"
        new_sm.get_cwd.return_value = "/tmp"
        mock_create.return_value = new_sm

        result = await runtime.new_session(NewSessionOptions())

    assert result["cancelled"] is False
    assert runtime.session is new_session
    assert runtime.services is new_services
    factory.assert_awaited_once()
    old_session.dispose.assert_called_once()
    # runtime 不再反向绑定到 session；runner 通过 action-injection 获取能力


@pytest.mark.asyncio
async def test_new_session_in_memory_when_not_persisted():
    """非持久化 session 使用 in_memory SessionManager。"""
    old_session = _make_old_session()
    old_session.session_manager.is_persisted.return_value = False
    services = _make_services(old_session)

    new_session = _make_old_session()
    new_session.extension_runner = None
    new_services = _make_services(new_session)

    factory = _make_factory(new_session, new_services)
    runtime = AgentSessionRuntime(old_session, services, factory)

    with patch(
        "nova_harness.core.agent_session.runtime.SessionManager.in_memory"
    ) as mock_in_memory:
        new_sm = MagicMock()
        new_sm.get_session_file.return_value = None
        new_sm.get_cwd.return_value = "/tmp"
        mock_in_memory.return_value = new_sm

        await runtime.new_session(NewSessionOptions())

    mock_in_memory.assert_called_once_with("/tmp")


@pytest.mark.asyncio
async def test_new_session_cancelled_by_extension():
    """扩展取消 new_session 时，不应 teardown 当前 session。"""
    old_session = _make_old_session()
    old_session.extension_runner = MagicMock()
    old_session.extension_runner.emit = AsyncMock(return_value=MagicMock(cancel=True))
    services = _make_services(old_session)

    factory = AsyncMock()
    runtime = AgentSessionRuntime(old_session, services, factory)

    result = await runtime.new_session(NewSessionOptions())

    assert result["cancelled"] is True
    old_session.dispose.assert_not_called()
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_new_session_with_parent_and_setup():
    """new_session 支持 parent_session 与 setup 回调。"""
    old_session = _make_old_session()
    services = _make_services(old_session)

    new_session = _make_old_session()
    new_session.extension_runner = None
    new_session.agent.state.messages = []
    new_services = _make_services(new_session)

    async def factory_impl(options):
        new_session.session_manager = options.session_manager
        return CreateAgentSessionRuntimeResult(
            session=new_session,
            services=new_services,
            extensions_result=MagicMock(),
            diagnostics=[],
            model_fallback_message=None,
        )

    factory = AsyncMock(side_effect=factory_impl)
    runtime = AgentSessionRuntime(old_session, services, factory)

    setup = AsyncMock()

    with patch(
        "nova_harness.core.agent_session.runtime.SessionManager.create"
    ) as mock_create:
        new_sm = MagicMock()
        new_sm.get_session_file.return_value = "new.jsonl"
        new_sm.get_cwd.return_value = "/tmp"
        new_sm.build_session_context.return_value = MagicMock(messages=["msg"])
        mock_create.return_value = new_sm

        await runtime.new_session(
            NewSessionOptions(parent_session="parent.jsonl", setup=setup)
        )

    new_sm.new_session.assert_called_once_with(parent_session="parent.jsonl")
    setup.assert_awaited_once_with(new_sm)
    assert new_session.agent.state.messages == ["msg"]


@pytest.mark.asyncio
async def test_switch_session_opens_existing_file():
    """switch_session 打开指定会话文件并重建 session。"""
    old_session = _make_old_session()
    services = _make_services(old_session)

    new_session = _make_old_session()
    new_session.extension_runner = None
    new_services = _make_services(new_session)

    factory = _make_factory(new_session, new_services)
    runtime = AgentSessionRuntime(old_session, services, factory)

    with patch(
        "nova_harness.core.agent_session.runtime.SessionManager.open"
    ) as mock_open:
        new_sm = MagicMock()
        new_sm.get_session_file.return_value = "switched.jsonl"
        new_sm.get_cwd.return_value = "/tmp"
        mock_open.return_value = new_sm

        result = await runtime.switch_session("/path/to/session.jsonl")

    assert result["cancelled"] is False
    mock_open.assert_called_once_with("/path/to/session.jsonl", None, None)
    factory.assert_awaited_once()
    assert runtime.session is new_session


@pytest.mark.asyncio
async def test_switch_session_emits_session_replaced_after_rebind():
    """switch_session 在新 session 上发射 session_replaced（且在 rebind 之后）。

    前端靠这条 Bus 2 事件触发全量重同步——先于 rebind 发射会让 RPC
    事件桥把通知丢进旧 session 的订阅里。
    """
    from nova_harness.core.types.events import SessionReplacedEvent

    old_session = _make_old_session()
    services = _make_services(old_session)

    new_session = _make_old_session()
    new_session.extension_runner = None
    new_services = _make_services(new_session)

    factory = _make_factory(new_session, new_services)
    runtime = AgentSessionRuntime(old_session, services, factory)

    call_order: list = []
    rebind = AsyncMock(side_effect=lambda s: call_order.append("rebind"))
    new_session._emit = MagicMock(side_effect=lambda e: call_order.append("emit"))
    runtime.set_rebind_session(rebind)

    with patch(
        "nova_harness.core.agent_session.runtime.SessionManager.open"
    ) as mock_open:
        new_sm = MagicMock()
        new_sm.get_session_file.return_value = "switched.jsonl"
        new_sm.get_cwd.return_value = "/tmp"
        mock_open.return_value = new_sm

        result = await runtime.switch_session("/path/to/session.jsonl")

    assert result["cancelled"] is False
    new_session._emit.assert_called_once()
    event = new_session._emit.call_args.args[0]
    assert isinstance(event, SessionReplacedEvent)
    assert event.reason == "resume"
    assert call_order == ["rebind", "emit"]


@pytest.mark.asyncio
async def test_switch_session_cancelled_by_extension():
    """扩展取消 switch_session 时，不应切换 session。"""
    old_session = _make_old_session()
    old_session.extension_runner = MagicMock()
    old_session.extension_runner.emit = AsyncMock(return_value=MagicMock(cancel=True))
    services = _make_services(old_session)

    factory = AsyncMock()
    runtime = AgentSessionRuntime(old_session, services, factory)

    result = await runtime.switch_session("/path/to/session.jsonl")

    assert result["cancelled"] is True
    old_session.dispose.assert_not_called()


@pytest.mark.asyncio
async def test_fork_before_user_message():
    """fork 在 UserMessage 前创建分支并返回选中文本。"""
    old_session = _make_old_session()
    old_session.session_manager.is_persisted.return_value = True
    old_session.session_manager.get_entry.return_value = MagicMock(
        type="message",
        message=UserMessage(role="user", content="hello"),
        parent_id="parent-1",
        id="entry-1",
    )
    old_session.get_user_message_text.return_value = "hello"
    services = _make_services(old_session)

    new_session = _make_old_session()
    new_session.extension_runner = None
    new_services = _make_services(new_session)

    factory = _make_factory(new_session, new_services)
    runtime = AgentSessionRuntime(old_session, services, factory)

    with patch(
        "nova_harness.core.agent_session.runtime.SessionManager.open"
    ) as mock_open:
        new_sm = MagicMock()
        new_sm.get_session_file.return_value = "forked.jsonl"
        new_sm.get_cwd.return_value = "/tmp"
        new_sm.create_branched_session.return_value = "forked.jsonl"
        mock_open.return_value = new_sm

        result = await runtime.fork("entry-1", ForkOptions(position="before"))

    assert result["cancelled"] is False
    assert result["selected_text"] == "hello"
    new_sm.create_branched_session.assert_called_once_with("parent-1")


@pytest.mark.asyncio
async def test_fork_at_entry():
    """fork at 指定条目时，目标 leaf 为条目本身。"""
    old_session = _make_old_session()
    old_session.session_manager.is_persisted.return_value = True
    old_session.session_manager.get_entry.return_value = MagicMock(
        type="message",
        message=UserMessage(role="user", content="ok"),
        parent_id="parent-1",
        id="entry-1",
    )
    services = _make_services(old_session)

    new_session = _make_old_session()
    new_session.extension_runner = None
    new_services = _make_services(new_session)

    factory = _make_factory(new_session, new_services)
    runtime = AgentSessionRuntime(old_session, services, factory)

    with patch(
        "nova_harness.core.agent_session.runtime.SessionManager.open"
    ) as mock_open:
        new_sm = MagicMock()
        new_sm.get_session_file.return_value = "forked.jsonl"
        new_sm.get_cwd.return_value = "/tmp"
        new_sm.create_branched_session.return_value = "forked.jsonl"
        mock_open.return_value = new_sm

        result = await runtime.fork("entry-1", ForkOptions(position="at"))

    assert result["cancelled"] is False
    new_sm.create_branched_session.assert_called_once_with("entry-1")


@pytest.mark.asyncio
async def test_fork_invalid_entry_raises():
    """fork 时若 entry 不存在应抛出 ValueError。"""
    old_session = _make_old_session()
    old_session.session_manager.get_entry.return_value = None
    services = _make_services(old_session)

    runtime = AgentSessionRuntime(old_session, services, AsyncMock())

    with pytest.raises(ValueError, match="Invalid entry ID"):
        await runtime.fork("missing-id")


@pytest.mark.asyncio
async def test_fork_cancelled_by_extension():
    """扩展取消 fork 时，不应 teardown 当前 session。"""
    old_session = _make_old_session()
    old_session.extension_runner = MagicMock()
    old_session.extension_runner.emit = AsyncMock(return_value=MagicMock(cancel=True))
    services = _make_services(old_session)

    factory = AsyncMock()
    runtime = AgentSessionRuntime(old_session, services, factory)

    result = await runtime.fork("entry-1")

    assert result["cancelled"] is True
    old_session.dispose.assert_not_called()


@pytest.mark.asyncio
async def test_dispose_emits_shutdown_and_disposes_session():
    """dispose 发射 shutdown 事件并释放 session 资源。"""
    old_session = _make_old_session()
    old_session.extension_runner = MagicMock()
    old_session.extension_runner.emit = AsyncMock()
    services = _make_services(old_session)

    callback = MagicMock()
    runtime = AgentSessionRuntime(old_session, services, AsyncMock())
    runtime.set_before_session_invalidate(callback)

    await runtime.dispose()

    old_session.extension_runner.emit.assert_awaited_once()
    callback.assert_called_once()
    old_session.dispose.assert_called_once()


@pytest.mark.asyncio
async def test_navigate_tree_delegates_to_session():
    """runtime 将 navigate_tree 委托给当前 session。"""
    session = _make_old_session()
    session.extension_runner = None
    services = _make_services(session)

    runtime = AgentSessionRuntime(session, services, AsyncMock())
    result = await runtime.session.navigate_tree(
        "target-1", NavigateOptions(summarize=False)
    )

    session.navigate_tree.assert_awaited_once()
    assert result["cancelled"] is False


@pytest.mark.asyncio
async def test_rebind_and_with_session_callbacks():
    """new_session 完成后调用 rebind_session 与 with_session 回调。"""
    old_session = _make_old_session()
    services = _make_services(old_session)

    new_session = _make_old_session()
    new_session.extension_runner = None
    new_services = _make_services(new_session)

    factory = _make_factory(new_session, new_services)
    runtime = AgentSessionRuntime(old_session, services, factory)

    rebind = AsyncMock()
    with_session = AsyncMock()
    runtime.set_rebind_session(rebind)

    replaced_context = MagicMock()
    new_session.create_replaced_session_context = MagicMock(
        return_value=replaced_context
    )

    with patch(
        "nova_harness.core.agent_session.runtime.SessionManager.create"
    ) as mock_create:
        new_sm = MagicMock()
        new_sm.get_session_file.return_value = "new.jsonl"
        new_sm.get_cwd.return_value = "/tmp"
        mock_create.return_value = new_sm

        await runtime.new_session(NewSessionOptions(with_session=with_session))

    rebind.assert_awaited_once_with(new_session)
    new_session.create_replaced_session_context.assert_called_once()
    with_session.assert_awaited_once_with(replaced_context)


@pytest.mark.asyncio
async def test_import_from_jsonl(tmp_path):
    """import_from_jsonl 复制 JSONL 到 session dir 并切换到该会话。"""
    from nova_ai import UserMessage

    from nova_harness.core.harness.session import SessionManager

    # 准备一个源 JSONL 会话
    source_session_dir = tmp_path / "source_sessions"
    source_session_dir.mkdir()
    source_manager = SessionManager.create(str(tmp_path), str(source_session_dir))
    source_manager.append_message(UserMessage(role="user", content="hello import"))
    source_manager._rewrite_file()
    source_file = source_manager.get_session_file()
    assert source_file is not None

    # 构造 runtime
    old_session = _make_old_session()
    old_session.session_manager.get_session_dir.return_value = str(
        tmp_path / "sessions"
    )
    services = _make_services(old_session)

    imported_session = _make_old_session()
    imported_session.extension_runner = None
    imported_session.session_manager = MagicMock()
    imported_session.session_manager.get_cwd.return_value = str(tmp_path)
    imported_services = _make_services(imported_session)

    factory = _make_factory(imported_session, imported_services)
    runtime = AgentSessionRuntime(old_session, services, factory)

    result = await runtime.import_from_jsonl(source_file)

    assert result["cancelled"] is False
    assert runtime.session is imported_session
    factory.assert_awaited_once()
    old_session.dispose.assert_called_once()


@pytest.mark.asyncio
async def test_import_from_jsonl_file_not_found(tmp_path):
    """import_from_jsonl 在文件不存在时抛出 SessionImportFileNotFoundError。"""
    from nova_harness.core.agent_session.runtime import SessionImportFileNotFoundError

    old_session = _make_old_session()
    services = _make_services(old_session)
    runtime = AgentSessionRuntime(old_session, services, AsyncMock())

    with pytest.raises(SessionImportFileNotFoundError):
        await runtime.import_from_jsonl(str(tmp_path / "nonexistent.jsonl"))
