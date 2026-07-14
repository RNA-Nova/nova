"""
RpcMethods 单元测试。

覆盖 JSON-RPC 方法实现。所有需要 runtime/session 的测试使用 fake runtime，
createSession 通过 patch 替换真实的 create_agent_session 工厂。
不依赖真实 LLM、文件系统或 stdin/stdout。
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_harness.core.types.package_manager import UninstallResult
from nova_harness.modes.rpc.errors import JSONRPCError
from nova_harness.modes.rpc.methods import RpcMethods


def _make_fake_runtime():
    """构造一个可直接注入 RpcMethods 的 fake runtime。"""
    runtime = MagicMock()
    runtime.session = MagicMock()
    runtime.session.session_id = "session-1"
    runtime.session.session_name = "Test Session"
    runtime.session.prompt = AsyncMock()
    runtime.session.abort = AsyncMock()
    runtime.session.set_model = AsyncMock(return_value=True)
    runtime.session.get_active_tool_names = MagicMock(return_value=["bash", "read"])
    runtime.session.change_agent = MagicMock()

    stats = MagicMock()
    stats.session_id = "session-1"
    stats.session_file = "/tmp/session.jsonl"
    stats.user_messages = 1
    stats.assistant_messages = 2
    stats.tool_calls = 3
    stats.tool_results = 4
    stats.total_messages = 10
    stats.tokens.input_tokens = 100
    stats.tokens.output_tokens = 50
    stats.tokens.cache_read = 10
    stats.tokens.cache_write = 5
    stats.tokens.total = 165
    stats.cost = 0.001
    runtime.session.get_session_stats.return_value = stats

    runtime.session.get_context_usage.return_value = {
        "context_window": 128000,
        "tokens": 150,
    }
    runtime.session.messages = []

    runtime.dispose = MagicMock()
    runtime.new_session = AsyncMock()
    runtime.switch_session = AsyncMock()
    return runtime


class TestRpcMethodsInitialize:
    """initialize 方法测试。"""

    async def test_initialize_returns_capabilities(self):
        """initialize 应返回版本与能力声明。"""
        methods = RpcMethods()
        result = await methods.initialize({})
        assert result["version"] == "0.1.0"
        assert result["capabilities"]["streaming"] is True
        assert result["capabilities"]["tools"] is True
        assert result["capabilities"]["sessions"] is True


class TestRpcMethodsCreateSession:
    """createSession 方法测试。"""

    @pytest.fixture
    def patched_create_session(self):
        """patch create_agent_session 并返回 fake runtime。"""
        fake_runtime = _make_fake_runtime()
        with patch(
            "nova_harness.modes.rpc.methods.create_agent_session_runtime",
            new=AsyncMock(return_value=fake_runtime),
        ) as mock:
            yield mock, fake_runtime

    async def test_create_session_success(self, patched_create_session):
        """createSession 成功时应返回 session 信息。"""
        mock_create, fake_runtime = patched_create_session
        methods = RpcMethods()

        result = await methods.createSession({"cwd": "/tmp"})

        mock_create.assert_awaited_once()
        assert result["session_id"] == "session-1"
        assert result["session_name"] == "Test Session"
        assert result["resumed"] is False
        assert methods.runtime is fake_runtime

    async def test_create_session_disposes_existing_runtime(
        self, patched_create_session
    ):
        """已有 runtime 时应先 dispose 旧实例。"""
        mock_create, fake_runtime = patched_create_session
        methods = RpcMethods()
        old_runtime = _make_fake_runtime()
        methods.set_runtime(old_runtime)

        await methods.createSession({})

        old_runtime.dispose.assert_called_once()
        assert methods.runtime is fake_runtime

    async def test_create_session_with_invalid_model_format(self):
        """model 字符串格式非法时应抛 JSONRPCError。"""
        methods = RpcMethods()
        with pytest.raises(JSONRPCError) as exc_info:
            await methods.createSession({"model": "invalid-model"})
        assert exc_info.value.code == -32602
        assert "Invalid model format" in exc_info.value.message


class TestRpcMethodsPrompt:
    """prompt 方法测试。"""

    @pytest.fixture
    def methods_with_runtime(self):
        """返回已注入 fake runtime 的 RpcMethods。"""
        methods = RpcMethods()
        methods.set_runtime(_make_fake_runtime())
        return methods

    async def test_prompt_success(self, methods_with_runtime):
        """prompt 应将 text 和 options 转发给 session.prompt。"""

        methods = methods_with_runtime
        result = await methods.prompt({"text": "hello"})
        assert result == {"ok": True}
        methods.runtime.session.prompt.assert_awaited_once()
        call_args = methods.runtime.session.prompt.await_args
        assert call_args.args == ("hello",)
        assert call_args.kwargs["options"].source == "rpc"

    async def test_prompt_without_session(self):
        """无活跃 session 时应抛 JSONRPCError。"""
        methods = RpcMethods()
        with pytest.raises(JSONRPCError) as exc_info:
            await methods.prompt({"text": "hello"})
        assert exc_info.value.code == -32000

    async def test_prompt_missing_text(self, methods_with_runtime):
        """缺少 text 参数时应抛 JSONRPCError。"""
        methods = methods_with_runtime
        with pytest.raises(JSONRPCError) as exc_info:
            await methods.prompt({})
        assert exc_info.value.code == -32602


class TestRpcMethodsAbort:
    """abort 方法测试。"""

    async def test_abort_without_session(self):
        """无 session 时返回 ok=False。"""
        methods = RpcMethods()
        result = await methods.abort({})
        assert result == {"ok": False, "reason": "no session"}

    async def test_abort_success(self):
        """有 session 时调用 abort 并返回 ok=True。"""
        methods = RpcMethods()
        methods.set_runtime(_make_fake_runtime())
        result = await methods.abort({})
        assert result == {"ok": True}
        methods.runtime.session.abort.assert_awaited_once()


class TestRpcMethodsSetModel:
    """setModel 方法测试。"""

    async def test_set_model_without_session(self):
        """无 session 时抛 JSONRPCError。"""
        methods = RpcMethods()
        with pytest.raises(JSONRPCError) as exc_info:
            await methods.setModel({"model": "provider/model"})
        assert exc_info.value.code == -32000

    async def test_set_model_success(self):
        """有 session 时解析模型并调用 set_model。"""
        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        methods.set_runtime(fake_runtime)
        fake_model = MagicMock()

        with patch.object(
            methods, "_resolve_model", return_value=fake_model
        ) as mock_resolve:
            result = await methods.setModel({"model": "provider/model"})

        assert result == {"ok": True}
        mock_resolve.assert_called_once_with("provider/model")
        fake_runtime.session.set_model.assert_awaited_once_with(fake_model)


class TestRpcMethodsSetThinkingLevel:
    """setThinkingLevel 方法测试。"""

    async def test_set_thinking_level_without_session(self):
        """无 session 时抛 JSONRPCError。"""
        methods = RpcMethods()
        with pytest.raises(JSONRPCError) as exc_info:
            await methods.setThinkingLevel({"level": "high"})
        assert exc_info.value.code == -32000

    async def test_set_thinking_level_high(self):
        """有 session 时映射 level 到 ThinkingLevel。"""
        from nova_ai import ThinkingLevel

        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        methods.set_runtime(fake_runtime)

        result = await methods.setThinkingLevel({"level": "high"})

        assert result == {"ok": True}
        fake_runtime.session.agent.set_thinking_level.assert_called_once_with(
            ThinkingLevel.HIGH
        )


class TestRpcMethodsGetSessionStats:
    """getSessionStats 方法测试。"""

    async def test_get_session_stats_without_session(self):
        """无 session 时抛 JSONRPCError。"""
        methods = RpcMethods()
        with pytest.raises(JSONRPCError) as exc_info:
            await methods.getSessionStats({})
        assert exc_info.value.code == -32000

    async def test_get_session_stats_success(self):
        """有 session 时返回统计信息。"""
        methods = RpcMethods()
        methods.set_runtime(_make_fake_runtime())
        result = await methods.getSessionStats({})
        assert result["session_id"] == "session-1"
        assert result["user_messages"] == 1
        assert result["assistant_messages"] == 2
        assert result["tool_calls"] == 3
        assert result["tool_results"] == 4
        assert result["total_messages"] == 10
        assert result["cost"] == 0.001
        assert result["tokens"]["input_tokens"] == 100
        assert result["tokens"]["output_tokens"] == 50
        assert result["tokens"]["cache_read"] == 10
        assert result["tokens"]["cache_write"] == 5
        assert result["tokens"]["total"] == 165


class TestRpcMethodsGetContextUsage:
    """getContextUsage 方法测试。"""

    async def test_get_context_usage_without_session(self):
        """无 session 时抛 JSONRPCError。"""
        methods = RpcMethods()
        with pytest.raises(JSONRPCError) as exc_info:
            await methods.getContextUsage({})
        assert exc_info.value.code == -32000

    async def test_get_context_usage_success(self):
        """有 session 时返回上下文用量。"""
        methods = RpcMethods()
        methods.set_runtime(_make_fake_runtime())
        result = await methods.getContextUsage({})
        assert result == {"context_window": 128000, "tokens": 150}


class TestRpcMethodsGetSessionMessages:
    """getSessionMessages 方法测试。"""

    async def test_get_session_messages_without_session(self):
        """无 session 时抛 JSONRPCError。"""
        methods = RpcMethods()
        with pytest.raises(JSONRPCError) as exc_info:
            await methods.getSessionMessages({})
        assert exc_info.value.code == -32000

    async def test_get_session_messages_empty(self):
        """无消息时返回空列表。"""
        methods = RpcMethods()
        methods.set_runtime(_make_fake_runtime())
        result = await methods.getSessionMessages({})
        assert result == {"messages": []}


class TestRpcMethodsNewSession:
    """newSession 方法测试。"""

    async def test_new_session_without_runtime(self):
        """无 runtime 时抛 JSONRPCError。"""
        methods = RpcMethods()
        with pytest.raises(JSONRPCError) as exc_info:
            await methods.newSession({})
        assert exc_info.value.code == -32000

    async def test_new_session_success(self):
        """有 runtime 时调用 new_session 并返回新 session 信息。"""
        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        fake_runtime.new_session = AsyncMock()
        methods.set_runtime(fake_runtime)

        result = await methods.newSession({})

        fake_runtime.new_session.assert_awaited_once()
        assert result["session_id"] == "session-1"
        assert result["session_name"] == "Test Session"


class TestRpcMethodsDispose:
    """dispose / shutdown 方法测试。"""

    async def test_dispose_without_runtime(self):
        """无 runtime 时 dispose 应安全返回 ok。"""
        methods = RpcMethods()
        result = await methods.dispose({})
        assert result == {"ok": True}

    async def test_dispose_with_runtime(self):
        """有 runtime 时 dispose 应释放并清空 runtime。"""
        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        methods.set_runtime(fake_runtime)
        result = await methods.dispose({})
        assert result == {"ok": True}
        fake_runtime.dispose.assert_called_once()
        assert methods.runtime is None

    async def test_shutdown_disposes_runtime(self):
        """shutdown 应 dispose runtime 并返回 ok。"""
        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        methods.set_runtime(fake_runtime)
        result = await methods.shutdown({})
        assert result == {"ok": True}
        fake_runtime.dispose.assert_called_once()
        assert methods.runtime is None


class TestRpcMethodsChangeAgent:
    """changeAgent 方法测试。"""

    async def test_change_agent_without_session(self):
        """无 session 时抛 JSONRPCError。"""
        methods = RpcMethods()
        with pytest.raises(JSONRPCError) as exc_info:
            await methods.changeAgent({"name": "coding"})
        assert exc_info.value.code == -32000

    async def test_change_agent_success(self):
        """有 session 时切换 agent 并返回工具列表。"""
        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        methods.set_runtime(fake_runtime)

        result = await methods.changeAgent({"name": "coding"})

        fake_runtime.session.change_agent.assert_called_once_with("coding")
        assert result["agent_name"] == "coding"
        assert result["available_tools"] == ["bash", "read"]

    async def test_change_agent_missing_name(self):
        """缺少 name 参数时抛 JSONRPCError。"""
        methods = RpcMethods()
        methods.set_runtime(_make_fake_runtime())
        with pytest.raises(JSONRPCError) as exc_info:

            await methods.changeAgent({})
        assert exc_info.value.code == -32602


# ------------------------------------------------------------------
# 补充测试：_resolve_model、_find_model 与 createSession 分支
# ------------------------------------------------------------------


class TestRpcMethodsResolveModel:
    """_resolve_model / _find_model 测试。"""

    def test_resolve_model_from_dict(self):
        methods = RpcMethods()
        fake_model = MagicMock()
        with patch("nova_ai.Model.model_validate", return_value=fake_model):
            result = methods._resolve_model({"provider": "test", "id": "model"})
        assert result is fake_model

    def test_resolve_model_invalid_type(self):
        methods = RpcMethods()
        with pytest.raises(JSONRPCError) as exc_info:
            methods._resolve_model(123)
        assert exc_info.value.code == -32602
        assert "Invalid model type" in exc_info.value.message

    def test_find_model_not_found(self):
        methods = RpcMethods()
        with patch("nova_harness.core.config.AuthStorage.create"):
            with patch(
                "nova_harness.core.config.ModelRegistry",
            ) as mock_registry_cls:
                registry = MagicMock()
                registry.find.return_value = None
                mock_registry_cls.return_value = registry
                with pytest.raises(JSONRPCError) as exc_info:
                    methods._find_model("missing", "model")
                assert exc_info.value.code == -32002
                assert "not found" in exc_info.value.message
                registry.find.assert_called_once_with("missing", "model")


class TestRpcMethodsCreateSessionBranches:
    """createSession 各分支测试。"""

    @pytest.fixture
    def patched_create_session(self):
        fake_runtime = _make_fake_runtime()
        with patch(
            "nova_harness.modes.rpc.methods.create_agent_session_runtime",
            new=AsyncMock(return_value=fake_runtime),
        ) as mock:
            yield mock, fake_runtime

    async def test_create_session_with_model_dict(self, patched_create_session):
        from nova_ai import Model

        mock_create, fake_runtime = patched_create_session
        methods = RpcMethods()
        fake_model = Model.model_construct(
            provider="test",
            id="m",
            name="m",
            api="openai",
            base_url="",
            reasoning=False,
            input_types=["text"],
            cost=MagicMock(),
            context_window=1000,
            max_tokens=1000,
        )
        with patch("nova_ai.Model.model_validate", return_value=fake_model):
            result = await methods.createSession(
                {"model": {"provider": "test", "id": "m"}}
            )
        assert result["session_id"] == "session-1"
        mock_create.assert_awaited_once()

    async def test_create_session_session_flag_empty(self, patched_create_session):
        mock_create, fake_runtime = patched_create_session
        methods = RpcMethods()
        result = await methods.createSession({"sessionFlag": ""})
        assert result["resumed"] is False
        fake_runtime.switch_session.assert_not_awaited()

    async def test_create_session_session_flag_not_found(self, patched_create_session):
        methods = RpcMethods()
        with patch.object(methods, "_find_session_path", return_value=None):
            with pytest.raises(JSONRPCError) as exc_info:
                await methods.createSession({"sessionFlag": "no-such-session"})
            assert exc_info.value.code == -32001

    async def test_create_session_session_flag_found(self, patched_create_session):
        mock_create, fake_runtime = patched_create_session
        methods = RpcMethods()
        with patch.object(
            methods, "_find_session_path", return_value="/tmp/session.jsonl"
        ):
            result = await methods.createSession({"sessionFlag": "sess-1"})
        assert result["resumed"] is True
        fake_runtime.switch_session.assert_awaited_once_with("/tmp/session.jsonl")

    async def test_create_session_continue_last_found(self, patched_create_session):
        mock_create, fake_runtime = patched_create_session
        methods = RpcMethods()
        with patch.object(
            methods, "_find_most_recent_session", return_value="/tmp/recent.jsonl"
        ):
            result = await methods.createSession({"continueLast": True})
        assert result["resumed"] is True
        fake_runtime.switch_session.assert_awaited_once_with("/tmp/recent.jsonl")

    async def test_create_session_continue_last_not_found(self, patched_create_session):
        mock_create, fake_runtime = patched_create_session
        methods = RpcMethods()
        with patch.object(methods, "_find_most_recent_session", return_value=None):
            result = await methods.createSession({"continueLast": True})
        assert result["resumed"] is False


class TestRpcMethodsFindSessionPath:
    """_find_session_path / _find_most_recent_session 测试。"""

    def test_find_session_path_returns_match(self, tmp_path: Path):
        methods = RpcMethods()
        session_file = tmp_path / "sess.jsonl"
        session_file.write_text(
            json.dumps({"type": "session", "id": "match-id"}) + "\n",
            encoding="utf-8",
        )
        with patch(
            "nova_harness.core.harness.session.utils.get_default_session_dir",
            return_value=str(tmp_path),
        ):
            result = methods._find_session_path("match-id", "/tmp")
        assert result == str(session_file)

    def test_find_session_path_no_dir(self):
        methods = RpcMethods()
        with patch(
            "nova_harness.core.harness.session.utils.get_default_session_dir",
            return_value="/nonexistent-dir-12345",
        ):
            assert methods._find_session_path("x", "/tmp") is None

    def test_find_most_recent_session(self, tmp_path: Path):
        methods = RpcMethods()
        old = tmp_path / "old.jsonl"
        new = tmp_path / "new.jsonl"
        old.write_text(
            json.dumps({"type": "session", "id": "old"}) + "\n", encoding="utf-8"
        )
        new.write_text(
            json.dumps({"type": "session", "id": "new"}) + "\n", encoding="utf-8"
        )
        # 确保修改时间不同
        import time

        time.sleep(0.01)
        new.write_text(
            json.dumps({"type": "session", "id": "new"}) + "\n", encoding="utf-8"
        )

        with patch(
            "nova_harness.core.harness.session.utils.get_default_session_dir",
            return_value=str(tmp_path),
        ):
            result = methods._find_most_recent_session("/tmp")
        assert result == str(new)


class TestRpcMethodsListSessions:
    """listSessions 测试。"""

    async def test_list_sessions_empty_dir(self):
        methods = RpcMethods()
        with patch(
            "nova_harness.core.harness.session.utils.get_default_session_dir",
            return_value="/nonexistent-dir-12345",
        ):
            assert await methods.listSessions({"cwd": "/tmp"}) == []

    async def test_list_sessions_skips_invalid_files(self, tmp_path: Path):
        methods = RpcMethods()
        valid = tmp_path / "valid.jsonl"
        invalid = tmp_path / "invalid.jsonl"
        valid.write_text(
            json.dumps({"type": "session", "id": "v", "name": "Valid"}) + "\n",
            encoding="utf-8",
        )
        invalid.write_text("not-json\n", encoding="utf-8")

        with patch(
            "nova_harness.core.harness.session.utils.get_default_session_dir",
            return_value=str(tmp_path),
        ):
            sessions = await methods.listSessions({"cwd": "/tmp"})
        assert len(sessions) == 1
        assert sessions[0]["id"] == "v"


class TestRpcMethodsGetSessionMessagesExtra:
    """getSessionMessages 序列化测试。"""

    async def test_get_session_messages_with_content(self):
        from nova_ai import (
            AssistantMessage,
            TextContent,
            ToolCall,
            ToolResultMessage,
            UserMessage,
        )

        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        fake_runtime.session.messages = [
            UserMessage(content="hello", timestamp=1),
            AssistantMessage(
                role="assistant",
                content=[
                    TextContent(type="text", text="hi"),
                    ToolCall(
                        type="toolCall", id="tc1", name="bash", arguments={"cmd": "ls"}
                    ),
                ],
                provider="test",
                model="m",
                timestamp=2,
            ),
            ToolResultMessage(
                tool_call_id="tc1",
                tool_name="bash",
                content=[TextContent(type="text", text="result")],
                is_error=False,
                timestamp=3,
            ),
        ]
        methods.set_runtime(fake_runtime)

        result = await methods.getSessionMessages({"limit": 10})
        messages = result["messages"]
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "toolResult"
        assert messages[2]["tool_args"] == {"cmd": "ls"}


class TestRpcMethodsGetSessionStatsExtra:
    """getSessionStats 边界测试。"""

    async def test_get_session_stats_no_tokens(self):
        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        stats = MagicMock()
        stats.tokens = None
        stats.session_id = "s"
        stats.session_file = None
        stats.user_messages = 0
        stats.assistant_messages = 0
        stats.tool_calls = 0
        stats.tool_results = 0
        stats.total_messages = 0
        stats.cost = 0.0
        fake_runtime.session.get_session_stats.return_value = stats
        methods.set_runtime(fake_runtime)

        result = await methods.getSessionStats({})
        assert result["tokens"] is None


class TestRpcMethodsGetContextUsageExtra:
    """getContextUsage 边界测试。"""

    async def test_get_context_usage_none(self):
        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        fake_runtime.session.get_context_usage.return_value = None
        methods.set_runtime(fake_runtime)

        result = await methods.getContextUsage({})
        assert result == {}


class TestRpcMethodsSetModelExtra:
    """setModel 错误分支。"""

    async def test_set_model_dict(self):
        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        methods.set_runtime(fake_runtime)
        fake_model = MagicMock()
        with patch("nova_ai.Model.model_validate", return_value=fake_model):
            result = await methods.setModel({"model": {"provider": "test", "id": "m"}})
        assert result == {"ok": True}
        fake_runtime.session.set_model.assert_awaited_once_with(fake_model)


class TestRpcMethodsSetThinkingLevelExtra:
    """setThinkingLevel 映射测试。"""

    async def test_set_thinking_level_default_for_unknown(self):
        from nova_ai import ThinkingLevel

        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        methods.set_runtime(fake_runtime)

        result = await methods.setThinkingLevel({"level": "unknown"})
        assert result == {"ok": True}
        fake_runtime.session.agent.set_thinking_level.assert_called_once_with(
            ThinkingLevel.MEDIUM
        )

    async def test_set_thinking_level_off(self):
        methods = RpcMethods()
        fake_runtime = _make_fake_runtime()
        methods.set_runtime(fake_runtime)

        result = await methods.setThinkingLevel({"level": "off"})
        assert result == {"ok": True}
        fake_runtime.session.agent.set_thinking_level.assert_called_once_with(None)


class TestRpcMethodsPackageManager:
    """pkg* 方法测试。"""

    async def test_pkg_list(self):
        methods = RpcMethods()
        fake_view = MagicMock()
        fake_view.model_dump.return_value = {"name": "bundle"}
        with patch("nova_harness.core.package.PackageManager") as mock_pm_cls:
            pm = MagicMock()
            pm.list_with_resources.return_value = {"bundle": fake_view}
            mock_pm_cls.return_value = pm

            result = await methods.pkgList({})
            assert result == {"bundle": {"name": "bundle"}}

    async def test_pkg_install(self):
        methods = RpcMethods()
        fake_meta = MagicMock()
        fake_meta.model_dump.return_value = {"name": "pkg"}
        with patch("nova_harness.core.package.PackageManager") as mock_pm_cls:
            pm = MagicMock()
            pm.install_and_persist.return_value = fake_meta
            mock_pm_cls.return_value = pm

            result = await methods.pkgInstall({"source": "/path"})
            assert result == {"name": "pkg"}
            pm.install_and_persist.assert_called_once_with("/path", local=False)

    async def test_pkg_uninstall(self):
        methods = RpcMethods()
        with patch("nova_harness.core.package.PackageManager") as mock_pm_cls:
            pm = MagicMock()
            pm.uninstall.return_value = UninstallResult(removed=True, messages=[])
            mock_pm_cls.return_value = pm

            result = await methods.pkgUninstall({"name_or_source": "pkg"})
            assert result == {"ok": True, "messages": []}
            pm.uninstall.assert_called_once_with("pkg", local=False)

    async def test_pkg_info_found(self):
        methods = RpcMethods()
        fake_meta = MagicMock()
        fake_meta.model_dump.return_value = {"name": "pkg"}
        with patch("nova_harness.core.package.PackageManager") as mock_pm_cls:
            pm = MagicMock()
            pm.info.return_value = fake_meta
            mock_pm_cls.return_value = pm

            result = await methods.pkgInfo({"name_or_source": "pkg"})
            assert result == {"name": "pkg"}

    async def test_pkg_info_not_found(self):
        methods = RpcMethods()
        with patch("nova_harness.core.package.PackageManager") as mock_pm_cls:
            pm = MagicMock()
            pm.info.return_value = None
            mock_pm_cls.return_value = pm

            result = await methods.pkgInfo({"name_or_source": "pkg"})
            assert result is None

    async def test_pkg_list_uses_session_trust_state(self):
        methods = RpcMethods()
        fake_settings = MagicMock()
        fake_settings.is_project_trusted.return_value = True
        runtime = _make_fake_runtime()
        runtime.session.settings_manager = fake_settings
        runtime.session.cwd = "/project"
        methods.set_runtime(runtime)

        fake_view = MagicMock()
        fake_view.model_dump.return_value = {"name": "bundle"}
        with patch("nova_harness.core.package.PackageManager") as mock_pm_cls:
            pm = MagicMock()
            pm.list_with_resources.return_value = {"bundle": fake_view}
            mock_pm_cls.return_value = pm

            await methods.pkgList({"local": True})
            mock_pm_cls.assert_called_once_with(
                cwd="/project",
                settings_manager=fake_settings,
                project_trusted=True,
            )
