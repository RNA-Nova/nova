"""RuntimeManager 单元测试：注册表生命周期（纯注入替身，不碰真实组装链）。

覆盖移植自 codex ``thread_manager.rs`` 的核心纪律：
registration-last（组装失败零注册）、ID 冲突在位者赢、close 幂等、
按身份销毁（原地换会话 id 漂移安全）、有界并发关停三分桶。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Dict, List, Optional

import pytest

from nova_harness.core.runtime_manager import (
    RuntimeManager,
    SessionIdCollisionError,
)
from nova_harness.types.session.config import CreateAgentSessionOptions


class _FakeRuntime:
    """轻量 runtime 替身：记录 dispose 调用，可配置挂起/抛异常。"""

    def __init__(
        self,
        session_id: str,
        *,
        dispose_error: Optional[Exception] = None,
        dispose_delay: float = 0.0,
    ) -> None:
        self.session: Any = _FakeSessionHolder(session_id)
        self.dispose_calls: int = 0
        self._dispose_error = dispose_error
        self._dispose_delay = dispose_delay

    async def dispose(self) -> None:
        self.dispose_calls += 1
        if self._dispose_delay:
            await asyncio.sleep(self._dispose_delay)
        if self._dispose_error is not None:
            raise self._dispose_error


class _FakeSessionHolder:
    def __init__(self, session_id: str) -> None:
        self.session_manager: Any = _FakeSessionManager(session_id)


class _FakeSessionManager:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def get_session_id(self) -> str:
        return self._session_id


class _Harness:
    """测试装置：注入工厂按剧本产出替身，记录收到的 options。"""

    def __init__(self) -> None:
        self.created: List[_FakeRuntime] = []
        self.received_options: List[Optional[CreateAgentSessionOptions]] = []
        self._ids: List[str] = []

    def queue_ids(self, *ids: str) -> None:
        self._ids.extend(ids)

    async def factory(
        self, options: Optional[CreateAgentSessionOptions]
    ) -> _FakeRuntime:
        self.received_options.append(options)
        session_id = self._ids.pop(0) if self._ids else f"sid-{len(self.created)}"
        runtime = _FakeRuntime(session_id)
        self.created.append(runtime)
        return runtime


def _manager(harness: _Harness, **kwargs: Any) -> RuntimeManager:
    return RuntimeManager(runtime_factory=harness.factory, **kwargs)


# ---------------------------------------------------------------------------
# open_session：注册与 registration-last
# ---------------------------------------------------------------------------


async def test_open_session_registers_and_returns():
    harness = _Harness()
    harness.queue_ids("sid-a")
    manager = _manager(harness)

    runtime = await manager.open_session()

    assert runtime is harness.created[0]
    assert manager.list_session_ids() == ["sid-a"]
    assert manager.get_session("sid-a") is runtime


async def test_factory_failure_registers_nothing():
    """组装失败 ⇒ 注册表原样（registration last，零回滚负担）。"""

    async def _boom(options):
        raise RuntimeError("assembly exploded")

    manager = RuntimeManager(runtime_factory=_boom)
    with pytest.raises(RuntimeError, match="assembly exploded"):
        await manager.open_session()
    assert manager.list_session_ids() == []


async def test_open_session_passes_caller_options():
    harness = _Harness()
    manager = _manager(harness)
    options = CreateAgentSessionOptions(agent_name="reviewer")

    await manager.open_session(options)

    assert harness.received_options[0] is options


async def test_default_agent_dir_injected_without_mutating_caller():
    """manager 级 agent_dir 经 replace 注入，调用方对象不被改写。"""
    harness = _Harness()
    manager = _manager(harness, agent_dir="/tmp/agents")

    options = CreateAgentSessionOptions(agent_name="x")
    await manager.open_session(options)

    assert harness.received_options[0].agent_dir == "/tmp/agents"
    # 显式 agent_dir 不被覆盖
    explicit = CreateAgentSessionOptions(agent_dir="/explicit")
    await manager.open_session(explicit)
    assert harness.received_options[1].agent_dir == "/explicit"
    # 调用方对象原样
    assert options.agent_dir is None


# ---------------------------------------------------------------------------
# ID 冲突：在位者赢
# ---------------------------------------------------------------------------


async def test_id_collision_incumbent_wins():
    harness = _Harness()
    harness.queue_ids("dup", "dup")
    manager = _manager(harness)

    incumbent = await manager.open_session()

    with pytest.raises(SessionIdCollisionError, match="dup"):
        await manager.open_session()

    # 第二次 factory 已产出新来者（组装发生在注册检查之前）
    newcomer = harness.created[1]

    # 新来者被销毁；在位者无损留任
    assert newcomer.dispose_calls == 1
    assert incumbent.dispose_calls == 0
    assert manager.get_session("dup") is incumbent


async def test_injectable_session_id_of():
    harness = _Harness()
    manager = _manager(harness, session_id_of=lambda runtime: f"custom-{id(runtime)}")

    runtime = await manager.open_session()

    assert manager.list_session_ids() == [f"custom-{id(runtime)}"]


# ---------------------------------------------------------------------------
# close_session / close_runtime
# ---------------------------------------------------------------------------


async def test_close_session_idempotent():
    harness = _Harness()
    manager = _manager(harness)

    runtime = await manager.open_session()
    assert (
        await manager.close_session(runtime.session.session_manager.get_session_id())
        is True
    )
    assert runtime.dispose_calls == 1
    assert await manager.close_session("missing") is False
    assert manager.list_session_ids() == []


async def test_close_runtime_removes_by_identity_after_id_drift():
    """原地换会后 id 漂移：close_runtime 按对象身份命中。"""
    harness = _Harness()
    harness.queue_ids("old-id")
    manager = _manager(harness)

    runtime = await manager.open_session()
    # 模拟 switch_session 原地替换会话：id 漂移
    runtime.session = _FakeSessionHolder("new-id")

    assert await manager.close_runtime(runtime) is True
    assert runtime.dispose_calls == 1
    assert manager.list_session_ids() == []
    # 已注销实例再关：False，且不重复销毁
    assert await manager.close_runtime(runtime) is False
    assert runtime.dispose_calls == 1


async def test_close_runtime_never_touches_untracked_instance():
    """不在注册表的实例不误杀（销毁责任留在调用方）。"""
    harness = _Harness()
    manager = _manager(harness)

    stranger = _FakeRuntime("stranger")
    assert await manager.close_runtime(stranger) is False
    assert stranger.dispose_calls == 0


# ---------------------------------------------------------------------------
# shutdown_all：有界并发 + 三分桶
# ---------------------------------------------------------------------------


async def test_shutdown_all_classifies_and_removes_only_completed():
    harness = _Harness()
    manager = _manager(harness)

    ok = _FakeRuntime("ok")
    bad = _FakeRuntime("bad", dispose_error=RuntimeError("nope"))
    slow = _FakeRuntime("slow", dispose_delay=5.0)
    manager._runtimes.update({"ok": ok, "bad": bad, "slow": slow})

    report = await manager.shutdown_all(timeout=0.05)

    assert report.completed == ("ok",)
    assert report.failed == ("bad",)
    assert report.timed_out == ("slow",)
    # 只移除 completed；失败/超时者留在注册表供重试
    assert set(manager.list_session_ids()) == {"bad", "slow"}
    assert ok.dispose_calls == 1


async def test_shutdown_all_empty_registry():
    manager = _manager(_Harness())
    report = await manager.shutdown_all()
    assert report.completed == ()
    assert report.failed == ()
    assert report.timed_out == ()


# ---------------------------------------------------------------------------
# 查询：仅活跃注册表
# ---------------------------------------------------------------------------


async def test_get_session_missing_returns_none():
    manager = _manager(_Harness())
    assert manager.get_session("ghost") is None
