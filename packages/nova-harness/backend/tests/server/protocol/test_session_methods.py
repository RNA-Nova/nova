"""session RPC 方法补全测试：getSessionState / compact / steer /
followUp / setActiveTools / navigateTree / fork。"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from nova_ai import ModelThinkingLevel

from nova_harness.core.types.compaction.compaction import CompactionResult
from nova_harness.server.protocol import JSONRPCError, MethodRegistry
from nova_harness.server.protocol.methods import session as session_methods
from nova_harness.server.protocol.methods.state import ServerState


class FakeSession:
    """记录调用的轻量 AgentSession 替身。"""

    def __init__(self):
        self.session_id = "s-1"
        self.session_file = "/tmp/s-1.jsonl"
        self.session_name = "demo"
        self.cwd = "/tmp"
        self.model = None
        self.thinking_level = ModelThinkingLevel.MEDIUM
        self.messages: List[Any] = []
        self.pending_message_count = 0
        self.is_streaming = False
        self.is_compacting = False
        self.is_retrying = False
        self.auto_retry_enabled = True
        self.auto_compaction_enabled = True
        self.steering_mode = "one-at-a-time"
        self.follow_up_mode = "one-at-a-time"
        self.settings_manager = None

        self.calls: List[tuple] = []
        self._active_tools = ["read", "bash"]

    def supports_thinking(self):
        return True

    def get_available_thinking_levels(self):
        return [
            ModelThinkingLevel.OFF,
            ModelThinkingLevel.MEDIUM,
            ModelThinkingLevel.HIGH,
        ]

    def get_active_tool_names(self):
        return list(self._active_tools)

    def set_active_tools_by_name(self, names):
        self.calls.append(("set_active_tools", list(names)))
        self._active_tools = list(names)

    def get_steering_messages(self):
        return ["s1"]

    def get_follow_up_messages(self):
        return ["f1"]

    def get_allowed_command_names(self):
        return None

    def get_disabled_command_names(self):
        return set()

    async def steer(self, text, images=None):
        self.calls.append(("steer", text, images))

    async def follow_up(self, text, images=None):
        self.calls.append(("follow_up", text, images))

    async def compact(self, custom_instructions=None):
        self.calls.append(("compact", custom_instructions))
        return CompactionResult(
            summary="s", first_kept_entry_id="e1", tokens_before=100
        )

    async def navigate_tree(self, target_id, options=None):
        self.calls.append(("navigate_tree", target_id, options))
        return {"navigated": target_id}

    async def fork_session(self, entry_id, position="before"):
        self.calls.append(("fork_session", entry_id, position))
        return {"sessionId": "s-2"}

    def set_session_name(self, name):
        self.calls.append(("set_session_name", name))
        self.session_name = name

    def set_steering_mode(self, mode):
        self.calls.append(("set_steering_mode", mode))
        self.steering_mode = mode

    def set_follow_up_mode(self, mode):
        self.calls.append(("set_follow_up_mode", mode))
        self.follow_up_mode = mode

    def clear_queue(self):
        self.calls.append(("clear_queue",))
        return {"steering": ["s1"], "follow_up": ["f1"]}

    def set_label(self, entry_id, label):
        self.calls.append(("set_label", entry_id, label))

    def abort_retry(self):
        self.calls.append(("abort_retry",))

    def abort_compaction(self):
        self.calls.append(("abort_compaction",))

    def set_auto_retry_enabled(self, enabled):
        self.calls.append(("set_auto_retry_enabled", enabled))
        self.auto_retry_enabled = enabled

    def set_auto_compaction_enabled(self, enabled):
        self.calls.append(("set_auto_compaction_enabled", enabled))
        self.auto_compaction_enabled = enabled

    async def reload(self):
        self.calls.append(("reload",))

    async def save_agent(self, as_name=None):
        self.calls.append(("save_agent", as_name))
        return {
            "name": as_name or "coding_agent",
            "path": "/tmp/agents/coding_agent.yaml",
            "shadowed": as_name is None,
        }

    def get_session_stats(self):
        return SimpleNamespace(
            session_id="s-1",
            session_file="/tmp/s-1.jsonl",
            user_messages=1,
            assistant_messages=1,
            tool_calls=0,
            tool_results=0,
            total_messages=2,
            tokens=None,
            cost=0.0,
        )

    def get_cache_waste(self):
        return SimpleNamespace(
            model_dump=lambda: {"missedTokens": 0, "missedCost": 0.0, "misses": 0},
            dump_wire=lambda: {"missedTokens": 0, "missedCost": 0.0, "misses": 0},
        )

    @property
    def session_manager(self):
        entry_dump = {
            "id": "e1",
            "parentId": None,
            "type": "message",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "user", "content": "hi"},
        }
        entry = SimpleNamespace(
            model_dump=lambda mode="json": entry_dump,
            dump_wire=lambda mode="json": entry_dump,
        )
        return SimpleNamespace(get_entries=lambda: [entry], get_leaf_id=lambda: "e1")


class FakeRuntime:
    def __init__(self, session):
        self.session = session


@pytest.fixture
def registry():
    session = FakeSession()
    state = ServerState(ui_context=SimpleNamespace())
    state.set_runtime(FakeRuntime(session))
    reg = MethodRegistry()
    session_methods.register(reg, state)
    return reg, session


async def _call(registry, method: str, params: Optional[Dict[str, Any]] = None):
    msg = SimpleNamespace(method=method, params=params or {}, id=1)
    resp = await registry.dispatch(msg)
    assert resp is not None
    return resp


def _result(resp) -> Dict[str, Any]:
    assert resp.error is None, f"unexpected error: {resp.error}"
    return resp.result


@pytest.mark.asyncio
async def test_get_session_state_snapshot(registry):
    reg, _ = registry
    resp = await _call(reg, "getSessionState")
    result = _result(resp)

    assert result["sessionId"] == "s-1"
    assert result["sessionName"] == "demo"
    assert result["thinkingLevel"] == "medium"
    assert result["activeTools"] == ["read", "bash"]
    assert result["steeringMessages"] == ["s1"]
    assert result["followUpMessages"] == ["f1"]
    assert result["isStreaming"] is False
    assert result["steeringMode"] == "one-at-a-time"
    # settings_manager 为 None 时 trust 缺省 True（RPC 无会话场景与 SDK 同款）
    assert result["projectTrusted"] is True


@pytest.mark.asyncio
async def test_compact_invokes_session(registry):
    reg, session = registry
    resp = await _call(reg, "compact", {"customInstructions": "focus on X"})
    result = _result(resp)

    assert session.calls == [("compact", "focus on X")]
    # 实例直通：core 返回的 CompactionResult 经 dispatch 单道 dump_wire
    # 出货（camel 键、None 字段以 null 上线；compact 进度走事件流）
    assert result == {
        "summary": "s",
        "firstKeptEntryId": "e1",
        "tokensBefore": 100,
        "estimatedTokensAfter": None,
        "details": None,
    }


@pytest.mark.asyncio
async def test_steer_and_follow_up(registry):
    reg, session = registry
    resp = await _call(reg, "steer", {"text": "hold on"})
    assert _result(resp)["success"] is True
    resp = await _call(reg, "followUp", {"text": "next task"})
    assert _result(resp)["success"] is True

    assert session.calls == [
        ("steer", "hold on", None),
        ("follow_up", "next task", None),
    ]


@pytest.mark.asyncio
async def test_steer_requires_text(registry):
    reg, _ = registry
    resp = await _call(reg, "steer", {})
    assert resp.error is not None
    assert "text" in str(resp.error["message"])


@pytest.mark.asyncio
async def test_set_active_tools(registry):
    reg, session = registry
    resp = await _call(reg, "setActiveTools", {"toolNames": ["grep", "read"]})
    result = _result(resp)

    assert session.calls == [("set_active_tools", ["grep", "read"])]
    assert result["activeTools"] == ["grep", "read"]


@pytest.mark.asyncio
async def test_navigate_tree_and_fork(registry):
    reg, session = registry
    resp = await _call(reg, "navigateTree", {"targetId": "e1"})
    assert _result(resp)["navigated"] == "e1"

    resp = await _call(reg, "fork", {"entryId": "e2", "position": "after"})
    assert _result(resp)["sessionId"] == "s-2"

    assert session.calls == [
        ("navigate_tree", "e1", None),
        ("fork_session", "e2", "after"),
    ]


@pytest.mark.asyncio
async def test_set_session_name(registry):
    reg, session = registry
    resp = await _call(reg, "setSessionName", {"name": "  新名字  "})
    result = _result(resp)
    assert result["success"] is True
    assert result["sessionName"] == "新名字"
    assert session.calls == [("set_session_name", "新名字")]


@pytest.mark.asyncio
async def test_set_session_name_requires_name(registry):
    reg, _ = registry
    resp = await _call(reg, "setSessionName", {"name": "   "})
    assert resp.error is not None


@pytest.mark.asyncio
async def test_queue_mode_setters(registry):
    reg, session = registry
    resp = await _call(reg, "setSteeringMode", {"mode": "all"})
    assert _result(resp)["steeringMode"] == "all"
    resp = await _call(reg, "setFollowUpMode", {"mode": "one-at-a-time"})
    assert _result(resp)["followUpMode"] == "one-at-a-time"
    assert session.calls == [
        ("set_steering_mode", "all"),
        ("set_follow_up_mode", "one-at-a-time"),
    ]


@pytest.mark.asyncio
async def test_queue_mode_rejects_invalid(registry):
    reg, _ = registry
    resp = await _call(reg, "setSteeringMode", {"mode": "sometimes"})
    assert resp.error is not None


@pytest.mark.asyncio
async def test_clear_queue(registry):
    reg, session = registry
    result = _result(await _call(reg, "clearQueue"))
    assert result == {"steering": ["s1"], "followUp": ["f1"]}
    assert session.calls == [("clear_queue",)]


@pytest.mark.asyncio
async def test_set_label(registry):
    reg, session = registry
    resp = await _call(reg, "setLabel", {"entryId": "e1", "label": "里程碑"})
    assert _result(resp)["success"] is True
    assert session.calls == [("set_label", "e1", "里程碑")]

    resp = await _call(reg, "setLabel", {"entryId": "e1"})
    assert session.calls[-1] == ("set_label", "e1", None)


@pytest.mark.asyncio
async def test_get_session_stats_includes_cache_waste(registry):
    reg, _ = registry
    result = _result(await _call(reg, "getSessionStats"))
    assert result["sessionId"] == "s-1"
    assert result["cacheWaste"] == {
        "missedTokens": 0,
        "missedCost": 0.0,
        "misses": 0,
    }


@pytest.mark.asyncio
async def test_get_session_entries_full_fidelity(registry):
    """getSessionEntries：全保真条目（含 id/type/parent），原样 dump 无裁剪。"""
    reg, _ = registry
    result = _result(await _call(reg, "getSessionEntries"))
    assert result["entries"] == [
        {
            "id": "e1",
            "parentId": None,
            "type": "message",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "user", "content": "hi"},
        }
    ]


@pytest.mark.asyncio
async def test_retry_controls(registry):
    reg, session = registry
    resp = await _call(reg, "abortRetry")
    assert _result(resp)["success"] is True
    resp = await _call(reg, "setAutoRetry", {"enabled": False})
    assert _result(resp)["autoRetryEnabled"] is False
    assert session.calls == [
        ("abort_retry",),
        ("set_auto_retry_enabled", False),
    ]

    resp = await _call(reg, "setAutoRetry", {"enabled": "yes"})
    assert resp.error is not None


@pytest.mark.asyncio
async def test_set_auto_compaction_enabled(registry):
    """setAutoCompactionEnabled：开关自动压缩，形状对齐 setAutoRetry。"""
    reg, session = registry
    resp = await _call(reg, "setAutoCompactionEnabled", {"enabled": False})
    assert _result(resp)["success"] is True
    assert _result(resp)["autoCompactionEnabled"] is False
    assert session.calls == [("set_auto_compaction_enabled", False)]
    assert session.auto_compaction_enabled is False

    # enabled 非 bool 被 params 模型拦截
    resp = await _call(reg, "setAutoCompactionEnabled", {"enabled": "yes"})
    assert resp.error is not None


@pytest.mark.asyncio
async def test_abort_compaction(registry):
    """abortCompaction：域级中止（只停压缩，不动 run/retry/用户工具）。"""
    reg, session = registry
    resp = await _call(reg, "abortCompaction")
    assert _result(resp)["success"] is True
    assert session.calls == [("abort_compaction",)]


@pytest.mark.asyncio
async def test_reload_invokes_session(registry):
    reg, session = registry
    resp = await _call(reg, "reload")
    assert _result(resp)["success"] is True
    assert session.calls == [("reload",)]


@pytest.mark.asyncio
async def test_save_agent_in_place(registry):
    """saveAgent（无 name）：就地/影子保存当前角色，结果透出 savedTo/shadowed。"""
    reg, session = registry
    resp = await _call(reg, "saveAgent")
    result = _result(resp)
    assert session.calls == [("save_agent", None)]
    assert result == {
        "name": "coding_agent",
        "savedTo": "/tmp/agents/coding_agent.yaml",
        "shadowed": True,
    }


@pytest.mark.asyncio
async def test_save_agent_as_new_name(registry):
    """saveAgent（带 name）：save-as 新名写 user 级。"""
    reg, session = registry
    resp = await _call(reg, "saveAgent", {"name": "my_agent"})
    result = _result(resp)
    assert session.calls == [("save_agent", "my_agent")]
    assert result["name"] == "my_agent"
    assert result["shadowed"] is False


@pytest.mark.asyncio
async def test_methods_require_active_session():
    state = ServerState(ui_context=SimpleNamespace())
    reg = MethodRegistry()
    session_methods.register(reg, state)  # runtime 为 None

    resp = await _call(reg, "getSessionState")
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.NO_ACTIVE_SESSION
