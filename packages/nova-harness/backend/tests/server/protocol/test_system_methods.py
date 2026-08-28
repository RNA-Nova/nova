"""System JSON-RPC 方法测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nova_harness.server.protocol import JSONRPCError, build_request
from nova_harness.server.protocol.methods.state import ServerState
from nova_harness.server.protocol.methods.system import register
from nova_harness.server.protocol.router import MethodRegistry


class _FakeRunner:
    """扩展 runner 替身：命令 / flags / 当前值。"""

    def __init__(self):
        self._values = {"verbose": False}

    def get_registered_commands(self):
        return [
            SimpleNamespace(
                invocation_name="sess",
                resolved_name="sess",
                description="会话管理",
                source_info=SimpleNamespace(
                    source="extension", path="/ext/session_commands"
                ),
            )
        ]

    def get_flags(self):
        return {
            "verbose": SimpleNamespace(
                description="详细输出",
                type="boolean",
                default=False,
                extension_path="/ext/verbose",
            )
        }

    def get_flag_values(self):
        return dict(self._values)

    def set_flag_value(self, name, value):
        self._values[name] = value


class _FakeLoader:
    def get_skills(self):
        return {
            "skills": {
                "commit": SimpleNamespace(
                    name="commit",
                    description="生成提交信息",
                    source_info=SimpleNamespace(
                        source="package", path="/pkg/skills/commit"
                    ),
                ),
            },
            "diagnostics": [],
        }


def _state_with_session():
    session = SimpleNamespace(
        extension_runner=_FakeRunner(),
        prompt_templates=[
            SimpleNamespace(
                name="refactor",
                description="重构当前文件",
                source_info=SimpleNamespace(
                    source="package", path="/pkg/prompts/refactor.md"
                ),
            )
        ],
        resource_loader=_FakeLoader(),
    )
    state = ServerState(ui_context=MagicMock())
    state.set_runtime(SimpleNamespace(session=session))
    return state


@pytest.mark.asyncio
async def test_get_commands_merges_all_sources():
    """getCommands 合并扩展命令 + prompt templates + skills（回归 get_skills 路径）。"""
    registry = MethodRegistry()
    register(registry, _state_with_session())

    result = await registry.dispatch(build_request("getCommands", {}, id=3))

    assert result is not None and result.error is None
    commands = {c["name"]: c for c in result.result["commands"]}
    assert set(commands) == {"sess", "refactor", "skill:commit"}
    assert commands["sess"]["source"] == "extension"
    assert commands["refactor"]["source"] == "prompt"
    assert commands["skill:commit"]["source"] == "skill"


@pytest.mark.asyncio
async def test_get_commands_name_never_null_when_not_renamed():
    """invocation_name 为 None（未重命名）时线上 name 必须落回 resolved_name。

    回归：此前直接序列化 invocation_name，None 经 JSON 变 null，
    前端补全把 null 当 value 喂给 pi-tui → startsWith 崩溃杀进程。
    """
    state = _state_with_session()
    session = state.runtime.session
    session.extension_runner.get_registered_commands = lambda: [
        SimpleNamespace(
            invocation_name=None,
            resolved_name="tree",
            description="导航会话树",
            source_info=SimpleNamespace(source="extension", path="/ext/x"),
        )
    ]
    registry = MethodRegistry()
    register(registry, state)

    result = await registry.dispatch(build_request("getCommands", {}, id=5))

    assert result is not None and result.error is None
    names = [c["name"] for c in result.result["commands"]]
    assert "tree" in names
    assert all(isinstance(n, str) and n for n in names)


@pytest.mark.asyncio
async def test_get_extension_flags_returns_definitions_and_values():
    registry = MethodRegistry()
    register(registry, _state_with_session())

    result = await registry.dispatch(build_request("getExtensionFlags", {}, id=4))

    assert result is not None and result.error is None
    assert result.result["flags"] == [
        {
            "name": "verbose",
            "description": "详细输出",
            "type": "boolean",
            "default": False,
            "value": False,
            "extensionPath": "/ext/verbose",
        }
    ]


@pytest.mark.asyncio
async def test_set_extension_flag_updates_value():
    state = _state_with_session()
    registry = MethodRegistry()
    register(registry, state)

    result = await registry.dispatch(
        build_request("setExtensionFlag", {"name": "verbose", "value": True}, id=5)
    )
    assert result is not None and result.error is None
    assert result.result == {"success": True, "name": "verbose", "value": True}

    listed = await registry.dispatch(build_request("getExtensionFlags", {}, id=6))
    assert listed.result["flags"][0]["value"] is True


@pytest.mark.asyncio
async def test_set_extension_flag_rejects_unknown():
    registry = MethodRegistry()
    register(registry, _state_with_session())

    result = await registry.dispatch(
        build_request("setExtensionFlag", {"name": "nope", "value": 1}, id=7)
    )
    assert result is not None and result.error is not None
    assert "Unknown extension flag" in result.error["message"]


@pytest.mark.asyncio
async def test_cancel_request_cancels_running_task():
    """cancelRequest：取消在飞 task（cancelled: true），CancelledError 到达协程。

    连接化后按连接寻址：请求来自哪条连接，就只查那条连接的在飞表。
    """
    from nova_harness.server.connection import (
        Connection,
        ConnectionOrigin,
        _current_connection,
    )
    from nova_harness.server.transport import MemoryTransport

    state = _state_with_session()
    registry = MethodRegistry()
    register(registry, state)

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_running():
        started.set()
        try:
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(long_running())
    conn = Connection(MemoryTransport(), ConnectionOrigin.MEMORY)
    conn.request_tasks[42] = task
    # cancel 一个尚未首次调度的 task 时协程体不会执行（无 await 注入点），
    # 先确保 task 已进入 sleep 等待——对齐真实场景（server task 先执行 handler）
    await asyncio.wait_for(started.wait(), timeout=1.0)

    token = _current_connection.set(conn)
    try:
        result = await registry.dispatch(
            build_request("cancelRequest", {"id": 42}, id=9)
        )
    finally:
        _current_connection.reset(token)
    assert result is not None and result.error is None
    assert result.result == {"success": True, "cancelled": True}

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_cancel_request_idempotent_for_unknown_or_done():
    """幂等：id 不存在或 task 已完成 → cancelled: false（非错误）。

    无连接上下文（或本连接没有该 id）同样幂等 false——连接隔离语义。
    """
    from nova_harness.server.connection import (
        Connection,
        ConnectionOrigin,
        _current_connection,
    )
    from nova_harness.server.transport import MemoryTransport

    state = _state_with_session()
    registry = MethodRegistry()
    register(registry, state)

    result = await registry.dispatch(build_request("cancelRequest", {"id": 999}, id=10))
    assert result is not None and result.error is None
    assert result.result == {"success": True, "cancelled": False}

    done = asyncio.create_task(asyncio.sleep(0))
    await done
    conn = Connection(MemoryTransport(), ConnectionOrigin.MEMORY)
    conn.request_tasks[43] = done
    token = _current_connection.set(conn)
    try:
        result = await registry.dispatch(
            build_request("cancelRequest", {"id": 43}, id=11)
        )
    finally:
        _current_connection.reset(token)
    assert result is not None and result.error is None
    assert result.result == {"success": True, "cancelled": False}


@pytest.mark.asyncio
async def test_cancel_request_validates_params():
    """缺 id / 非整数 id → INVALID_PARAMS。"""
    registry = MethodRegistry()
    register(registry, _state_with_session())

    result = await registry.dispatch(build_request("cancelRequest", {}, id=12))
    assert result is not None and result.error is not None
    assert result.error["code"] == JSONRPCError.INVALID_PARAMS

    result = await registry.dispatch(
        build_request("cancelRequest", {"id": "abc"}, id=13)
    )
    assert result is not None and result.error is not None
    assert result.error["code"] == JSONRPCError.INVALID_PARAMS
