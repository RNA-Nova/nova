"""会话文件管理 RPC 方法测试：

- listSessions：富字段（messageCount/firstMessage/cwd/parentSessionPath）与
  scope（current/all）；
- deleteSession：删文件成功 / 删当前活跃会话拒绝 / 幂等；
- renameSession：改任意文件 / 不触碰当前会话 / 空名清除；
- createSession sessionFile：绝对路径 / 裸 id 解析 / 互斥 / 不存在报错；
- fork：返回值携带 selectedText/editorText（被选 user 消息原文）。

全部使用真实临时目录中的会话文件（不 mock 文件系统）。
"""

import json
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
from nova_ai import AssistantMessage, TextContent, UserMessage

from nova_harness.core import AgentSession
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.harness.session.utils import get_default_session_dir_path
from nova_harness.core.rpc.protocol import JSONRPCError, MethodRegistry
from nova_harness.core.rpc.protocol.methods import session as session_methods
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.types.session.config import AgentSessionConfig


def _make_state(runtime: Any = None) -> ServerState:
    state = ServerState(ui_context=SimpleNamespace())
    if runtime is not None:
        state.set_runtime(runtime)
    return state


def _session_registry(runtime: Any = None) -> MethodRegistry:
    reg = MethodRegistry()
    session_methods.register(reg, _make_state(runtime))
    return reg


async def _call(
    registry: MethodRegistry, method: str, params: Optional[Dict[str, Any]] = None
):
    msg = SimpleNamespace(method=method, params=params or {}, id=1)
    resp = await registry.dispatch(msg)
    assert resp is not None
    return resp


def _write_session(
    path: str,
    session_id: str,
    cwd: str,
    *,
    timestamp: str = "2024-01-01T00:00:00",
    user_texts: Optional[List[str]] = None,
    name: Optional[str] = None,
    parent_session: Optional[str] = None,
) -> None:
    """以行级原文写一个合法会话文件（磁盘格式保持 snake_case）。"""
    header: Dict[str, Any] = {
        "type": "session",
        "version": 3,
        "id": session_id,
        "timestamp": timestamp,
        "cwd": cwd,
    }
    if parent_session:
        header["parent_session"] = parent_session
    lines = [json.dumps(header, ensure_ascii=False)]
    parent_id: Optional[str] = None
    seq = 0
    if name is not None:
        lines.append(
            json.dumps(
                {
                    "type": "session_info",
                    "id": f"i{seq}",
                    "parent_id": parent_id,
                    "timestamp": timestamp,
                    "name": name,
                },
                ensure_ascii=False,
            )
        )
        parent_id = f"i{seq}"
        seq += 1
    for text in user_texts or []:
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "id": f"e{seq}",
                    "parent_id": parent_id,
                    "timestamp": timestamp,
                    "message": {"role": "user", "content": text},
                },
                ensure_ascii=False,
            )
        )
        parent_id = f"e{seq}"
        seq += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


@pytest.fixture
def agent_dir(tmp_path, monkeypatch):
    """把全局 agent 目录指到临时目录（get_agent_dir 每次调用现读环境变量）。"""
    monkeypatch.setenv("NOVA_AGENT_DIR", str(tmp_path / "agent"))
    return tmp_path / "agent"


def _default_dir_for(cwd: str) -> str:
    session_dir = get_default_session_dir_path(cwd)
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


# ---------------------------------------------------------------------------
# listSessions：富字段 + scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_rich_fields(agent_dir, tmp_path):
    cwd = str(tmp_path / "proj-a")
    session_dir = _default_dir_for(cwd)
    other = str(tmp_path / "other.jsonl")
    _write_session(
        os.path.join(session_dir, "s1.jsonl"),
        "sid-1",
        cwd,
        user_texts=["hello nova", "second question"],
        name="演示会话",
        parent_session=other,
    )

    reg = _session_registry()
    resp = await _call(reg, "listSessions", {"cwd": cwd})
    assert resp.error is None
    items = resp.result
    assert len(items) == 1
    item = items[0]
    # 初版契约字段保留
    assert item["id"] == "sid-1"
    assert item["name"] == "演示会话"
    assert item["path"].endswith("s1.jsonl")
    assert isinstance(item["modified"], float)
    # 新增的富字段（线上 camelCase）
    assert item["messageCount"] == 2
    assert item["firstMessage"] == "hello nova"
    assert item["cwd"] == cwd
    assert item["parentSessionPath"] == other


@pytest.mark.asyncio
async def test_list_sessions_scope(agent_dir, tmp_path):
    cwd_a = str(tmp_path / "proj-a")
    cwd_b = str(tmp_path / "proj-b")
    dir_a = _default_dir_for(cwd_a)
    dir_b = _default_dir_for(cwd_b)
    _write_session(
        os.path.join(dir_a, "a.jsonl"), "sid-a", cwd_a, user_texts=["from A"]
    )
    _write_session(
        os.path.join(dir_b, "b.jsonl"), "sid-b", cwd_b, user_texts=["from B"]
    )

    reg = _session_registry()

    # 默认 scope=current：只列当前 cwd 的会话目录
    resp = await _call(reg, "listSessions", {"cwd": cwd_a})
    assert resp.error is None
    assert [s["id"] for s in resp.result] == ["sid-a"]

    resp = await _call(reg, "listSessions", {"cwd": cwd_a, "scope": "current"})
    assert [s["id"] for s in resp.result] == ["sid-a"]

    # scope=all：遍历全局 sessions 根下所有项目目录
    resp = await _call(reg, "listSessions", {"cwd": cwd_a, "scope": "all"})
    assert resp.error is None
    assert sorted(s["id"] for s in resp.result) == ["sid-a", "sid-b"]

    # 非法 scope 被 params 模型拦截
    resp = await _call(reg, "listSessions", {"scope": "everything"})
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.INVALID_PARAMS


@pytest.mark.asyncio
async def test_list_sessions_empty_when_no_dir(agent_dir):
    reg = _session_registry()
    resp = await _call(reg, "listSessions", {"cwd": "/nonexistent-cwd-xyz"})
    assert resp.error is None
    assert resp.result == []


# ---------------------------------------------------------------------------
# deleteSession
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session_removes_file(agent_dir, tmp_path):
    path = str(tmp_path / "s.jsonl")
    _write_session(path, "sid-del", "/tmp", user_texts=["bye"])

    reg = _session_registry()
    resp = await _call(reg, "deleteSession", {"path": path})
    assert resp.error is None
    assert resp.result == {"deleted": True}
    assert not os.path.exists(path)


@pytest.mark.asyncio
async def test_delete_session_idempotent_when_missing(agent_dir, tmp_path):
    path = str(tmp_path / "missing.jsonl")
    reg = _session_registry()
    resp = await _call(reg, "deleteSession", {"path": path})
    assert resp.error is None
    assert resp.result == {"deleted": True}


@pytest.mark.asyncio
async def test_delete_session_rejects_active_session(agent_dir, tmp_path):
    path = str(tmp_path / "active.jsonl")
    _write_session(path, "sid-active", "/tmp", user_texts=["still here"])

    runtime = SimpleNamespace(session=SimpleNamespace(session_file=path))
    reg = _session_registry(runtime)
    resp = await _call(reg, "deleteSession", {"path": path})
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.SESSION_IN_USE
    # 文件必须还在
    assert os.path.exists(path)


@pytest.mark.asyncio
async def test_delete_session_allows_other_file_with_active_session(
    agent_dir, tmp_path
):
    active = str(tmp_path / "active.jsonl")
    other = str(tmp_path / "other.jsonl")
    _write_session(active, "sid-active", "/tmp", user_texts=["keep"])
    _write_session(other, "sid-other", "/tmp", user_texts=["drop"])

    runtime = SimpleNamespace(session=SimpleNamespace(session_file=active))
    reg = _session_registry(runtime)
    resp = await _call(reg, "deleteSession", {"path": other})
    assert resp.error is None
    assert resp.result == {"deleted": True}
    assert os.path.exists(active)
    assert not os.path.exists(other)


# ---------------------------------------------------------------------------
# renameSession
# ---------------------------------------------------------------------------


class _RenameFakeSession:
    """记录 set_session_name 调用的轻量活跃会话替身。"""

    def __init__(self, session_file: str):
        self.session_file = session_file
        self.calls: List[str] = []
        self.session_name: Optional[str] = None

    def set_session_name(self, name: str) -> None:
        self.calls.append(name)
        self.session_name = name.strip() or None


@pytest.mark.asyncio
async def test_rename_session_arbitrary_file(agent_dir, tmp_path):
    path = str(tmp_path / "s.jsonl")
    _write_session(path, "sid-ren", "/tmp", user_texts=["hello"])

    reg = _session_registry()
    resp = await _call(reg, "renameSession", {"path": path, "name": "  新名字  "})
    assert resp.error is None
    assert resp.result == {"ok": True, "sessionName": "新名字"}

    # 重新打开文件验证：名字来自最新一条 session_info
    manager = SessionManager.open(path)
    assert manager.get_session_name() == "新名字"


@pytest.mark.asyncio
async def test_rename_session_blank_clears_name(agent_dir, tmp_path):
    """空名（trim 后）= 显式清除名字（对齐 append_session_info 语义）。"""
    path = str(tmp_path / "s.jsonl")
    _write_session(path, "sid-clr", "/tmp", user_texts=["hello"], name="旧名字")

    reg = _session_registry()
    resp = await _call(reg, "renameSession", {"path": path, "name": "   "})
    assert resp.error is None
    assert resp.result == {"ok": True, "sessionName": None}

    manager = SessionManager.open(path)
    assert manager.get_session_name() is None


@pytest.mark.asyncio
async def test_rename_session_missing_file(agent_dir, tmp_path):
    reg = _session_registry()
    resp = await _call(
        reg, "renameSession", {"path": str(tmp_path / "nope.jsonl"), "name": "x"}
    )
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.SESSION_NOT_FOUND


@pytest.mark.asyncio
async def test_rename_session_does_not_touch_active_session(agent_dir, tmp_path):
    """重命名其他会话文件时，当前活跃会话的文件与 live 状态都不受影响。"""
    active = str(tmp_path / "active.jsonl")
    other = str(tmp_path / "other.jsonl")
    _write_session(active, "sid-active", "/tmp", user_texts=["keep"])
    _write_session(other, "sid-other", "/tmp", user_texts=["rename me"])
    with open(active, "r", encoding="utf-8") as f:
        active_before = f.read()

    session = _RenameFakeSession(active)
    reg = _session_registry(SimpleNamespace(session=session))
    resp = await _call(reg, "renameSession", {"path": other, "name": "别人的"})
    assert resp.error is None
    assert resp.result == {"ok": True, "sessionName": "别人的"}

    # 没有走 live 通道，当前会话文件一字节未动
    assert session.calls == []
    with open(active, "r", encoding="utf-8") as f:
        assert f.read() == active_before
    assert SessionManager.open(other).get_session_name() == "别人的"


@pytest.mark.asyncio
async def test_rename_session_active_routes_through_live_session(agent_dir, tmp_path):
    """目标就是当前活跃会话时，走 live 通道（内存索引/事件广播保持一致）。"""
    active = str(tmp_path / "active.jsonl")
    _write_session(active, "sid-active", "/tmp", user_texts=["keep"])

    session = _RenameFakeSession(active)
    reg = _session_registry(SimpleNamespace(session=session))
    resp = await _call(reg, "renameSession", {"path": active, "name": "活跃会话"})
    assert resp.error is None
    assert resp.result == {"ok": True, "sessionName": "活跃会话"}
    assert session.calls == ["活跃会话"]


# ---------------------------------------------------------------------------
# fork：selectedText / editorText
# ---------------------------------------------------------------------------


def _make_agent_config(session_manager) -> AgentSessionConfig:
    """构造最小可用的 AgentSessionConfig（对齐 test_session_commands 的写法）。"""
    agent = MagicMock()
    agent.state.messages = []
    agent.state.is_streaming = False
    return AgentSessionConfig(
        agent=agent,
        session_manager=session_manager,
        settings_manager=MagicMock(),
        cwd="/tmp",
        system_prompt_manager=MagicMock(),
        tools_manager=MagicMock(),
        resource_loader=MagicMock(),
        model_runtime=MagicMock(),
        scoped_models=[],
        initial_active_tool_names=[],
        base_tools_override=None,
        extension_runner_ref=None,
        session_start_event=None,
    )


@pytest.fixture
def forked_session(tmp_path):
    """真实持久化会话：一条 user 消息 + 一条 assistant 消息（触发 flush）。"""
    session_dir = str(tmp_path / "sessions")
    os.makedirs(session_dir, exist_ok=True)
    sm = SessionManager(
        cwd="/tmp", session_dir=session_dir, session_file=None, persist=True
    )
    sm.append_message(UserMessage(role="user", content="fork 我这句话"))
    sm.append_message(
        AssistantMessage(
            role="assistant", content=[TextContent(type="text", text="好的")]
        )
    )
    session = AgentSession(_make_agent_config(sm))
    user_entry_id = next(
        e.id
        for e in sm.get_entries()
        if e.type == "message" and e.message.role == "user"
    )
    return session, user_entry_id


@pytest.mark.asyncio
async def test_fork_returns_selected_text(forked_session):
    session, user_entry_id = forked_session
    reg = _session_registry(SimpleNamespace(session=session))

    resp = await _call(reg, "fork", {"entryId": user_entry_id, "position": "before"})
    assert resp.error is None
    result = resp.result
    assert result["cancelled"] is False
    # 被选 user 消息原文回填编辑器（pi 语义）；editorText 与 navigateTree 对齐
    assert result["selectedText"] == "fork 我这句话"
    assert result["editorText"] == "fork 我这句话"


@pytest.mark.asyncio
async def test_fork_at_position_has_no_selected_text(forked_session):
    """position="at" 不选消息：无回填文本（RPC 契约不放行 "at"，直测会话层——
    内部契约为 snake；线上 camel 翻译归 fork handler，见 test_fork_returns_selected_text）。"""
    session, user_entry_id = forked_session

    result = await session.fork_session(user_entry_id, "at")
    assert result["cancelled"] is False
    assert result["selected_text"] is None
    assert result["editor_text"] is None


# ---------------------------------------------------------------------------
# createSession：sessionFile（pi --session <file|id> 显式会话文件恢复）
# ---------------------------------------------------------------------------


class _CreateFakeRuntime:
    """记录 switch_session 调用的轻量 runtime 替身。"""

    def __init__(self):
        self.session = SimpleNamespace(session_id="new-s", session_name=None)
        self.switched: List[str] = []

    async def switch_session(self, path):
        self.switched.append(path)
        return {"cancelled": False}


@pytest.fixture
def fake_create_runtime(monkeypatch):
    """把 create_agent_session_runtime 换成替身工厂（避免真实 SDK 创建）。

    返回已创建的 runtime 列表，供断言 switch_session 调用。
    """
    created: List[_CreateFakeRuntime] = []

    async def _factory(opts):
        runtime = _CreateFakeRuntime()
        created.append(runtime)
        return runtime

    monkeypatch.setattr(session_methods, "create_agent_session_runtime", _factory)
    return created


@pytest.mark.asyncio
async def test_create_session_with_session_file_absolute(
    agent_dir, tmp_path, fake_create_runtime
):
    """绝对路径：直接切换到该文件，resumed=True。"""
    path = str(tmp_path / "explicit.jsonl")
    _write_session(path, "sid-explicit", str(tmp_path), user_texts=["hi"])

    reg = _session_registry()
    resp = await _call(
        reg,
        "createSession",
        {"cwd": str(tmp_path), "sessionFile": path},
    )
    assert resp.error is None
    assert resp.result["resumed"] is True
    assert fake_create_runtime[0].switched == [path]


@pytest.mark.asyncio
async def test_create_session_with_session_file_bare_id(
    agent_dir, tmp_path, fake_create_runtime
):
    """裸 id：在 cwd 的默认会话目录解析 <id>.jsonl。"""
    cwd = str(tmp_path / "proj")
    session_dir = _default_dir_for(cwd)
    path = os.path.join(session_dir, "abc123.jsonl")
    _write_session(path, "abc123", cwd, user_texts=["resume me"])

    reg = _session_registry()
    resp = await _call(reg, "createSession", {"cwd": cwd, "sessionFile": "abc123"})
    assert resp.error is None
    assert resp.result["resumed"] is True
    assert fake_create_runtime[0].switched == [path]


@pytest.mark.asyncio
async def test_create_session_bare_id_with_jsonl_suffix_not_doubled(
    agent_dir, tmp_path, fake_create_runtime
):
    """裸 id 自带 .jsonl 后缀时不重复拼接。"""
    cwd = str(tmp_path / "proj")
    session_dir = _default_dir_for(cwd)
    path = os.path.join(session_dir, "s2.jsonl")
    _write_session(path, "s2", cwd, user_texts=["hi"])

    reg = _session_registry()
    resp = await _call(reg, "createSession", {"cwd": cwd, "sessionFile": "s2.jsonl"})
    assert resp.error is None
    assert fake_create_runtime[0].switched == [path]


@pytest.mark.asyncio
async def test_create_session_session_file_not_found(
    agent_dir, tmp_path, fake_create_runtime
):
    """文件不存在：SESSION_NOT_FOUND，且不创建 runtime（校验先于重建）。"""
    missing = str(tmp_path / "nope.jsonl")
    reg = _session_registry()
    resp = await _call(
        reg, "createSession", {"cwd": str(tmp_path), "sessionFile": missing}
    )
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.SESSION_NOT_FOUND
    assert missing in resp.error["message"]
    assert fake_create_runtime == []


@pytest.mark.asyncio
async def test_create_session_session_file_invalid(
    agent_dir, tmp_path, fake_create_runtime
):
    """存在但非合法会话文件（无 session 头）：同样 SESSION_NOT_FOUND。"""
    path = str(tmp_path / "garbage.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"type": "message"}\n')

    reg = _session_registry()
    resp = await _call(
        reg, "createSession", {"cwd": str(tmp_path), "sessionFile": path}
    )
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.SESSION_NOT_FOUND
    assert fake_create_runtime == []


@pytest.mark.asyncio
async def test_create_session_session_file_mutually_exclusive(
    agent_dir, tmp_path, fake_create_runtime
):
    """sessionFile 与 sessionFlag / continueLast 互斥（含空串 flag 的交互 sentinel）。"""
    path = str(tmp_path / "explicit.jsonl")
    _write_session(path, "sid-x", str(tmp_path), user_texts=["hi"])

    reg = _session_registry()
    for extra in (
        {"sessionFlag": "some-id"},
        {"sessionFlag": ""},
        {"continueLast": True},
    ):
        resp = await _call(
            reg,
            "createSession",
            {"cwd": str(tmp_path), "sessionFile": path, **extra},
        )
        assert resp.error is not None, extra
        assert resp.error["code"] == JSONRPCError.INVALID_PARAMS, extra
    assert fake_create_runtime == []


@pytest.mark.asyncio
async def test_create_session_session_file_blank(agent_dir, tmp_path):
    """空白 sessionFile 报参数错误。"""
    reg = _session_registry()
    resp = await _call(
        reg, "createSession", {"cwd": str(tmp_path), "sessionFile": "   "}
    )
    assert resp.error is not None
    assert resp.error["code"] == JSONRPCError.INVALID_PARAMS


# ---------------------------------------------------------------------------
# createSession：noSession（pi --no-session 对位——临时会话契约开关）
# ---------------------------------------------------------------------------


@pytest.fixture
def capture_create_opts(monkeypatch):
    """捕获 create_agent_session_runtime 收到的 opts（验证内存态注入）。"""
    captured: List[Any] = []

    async def _factory(opts):
        captured.append(opts)
        return _CreateFakeRuntime()

    monkeypatch.setattr(session_methods, "create_agent_session_runtime", _factory)
    return captured


@pytest.mark.asyncio
async def test_create_session_no_session_injects_memory_manager(
    agent_dir, tmp_path, capture_create_opts
):
    """noSession=True：SessionManager.in_memory 注入 opts（不落盘机制与
    print 模式 --no-session 同源）。"""
    reg = _session_registry()
    resp = await _call(reg, "createSession", {"cwd": str(tmp_path), "noSession": True})
    assert resp.error is None
    opts = capture_create_opts[0]
    assert opts.session_manager is not None
    # 内存态管理器：persist=False（in_memory 构造语义——不落盘不进列表）
    assert opts.session_manager.is_persisted() is False


@pytest.mark.asyncio
async def test_create_session_no_session_mutually_exclusive(
    agent_dir, tmp_path, capture_create_opts
):
    """noSession 与恢复类参数互斥：参数错误且不创建 runtime。"""
    reg = _session_registry()
    for extra in (
        {"sessionFlag": "abc"},
        {"continueLast": True},
        {"sessionFile": str(tmp_path / "x.jsonl")},
    ):
        resp = await _call(
            reg, "createSession", {"cwd": str(tmp_path), "noSession": True, **extra}
        )
        assert resp.error is not None
        assert resp.error["code"] == JSONRPCError.INVALID_PARAMS
        assert "noSession" in resp.error["message"]
    assert capture_create_opts == []
