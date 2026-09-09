"""RuntimeManager — 进程级唯一会话工厂 + 活跃注册表。

对位 codex ``core/src/thread_manager.rs``：共享入口、注册放最后、
有界并发关停。纪律与 codex 一致——

- 组装委托给 ``assembly``（对位 ``Session::spawn``），manager 只做
  默认填充 + 委托 + 注册；组装失败发生在注册前，零回滚负担；
- 注册在锁内做 vacant 检查：session_id 冲突时**在位者赢**，
  新来者被销毁并报 ``SessionIdCollisionError``；
- 锁（``asyncio.Lock``）只护注册表 map 操作，绝不跨 ``await`` 组装；
- ``shutdown_all`` 有界并发扇出，只移除 completed（超时/失败者
  留在注册表供重试），报告三类分桶；
- ``get_session`` / ``list_session_ids`` 仅活跃注册表（不是目录，
  ``listSessions`` 的落盘扫描与此无关）。

注册表键为 **open 时刻的 session_id**：``switch_session`` /
``new_session`` / ``fork`` 原地替换会话后 id 会漂移，按身份销毁请用
``close_runtime(runtime)``。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from nova_harness.core.agent_session import AgentSessionRuntime
from nova_harness.types.session.config import CreateAgentSessionOptions

__all__ = [
    "RuntimeManager",
    "SessionIdCollisionError",
    "SessionShutdownReport",
]


RuntimeSessionFactory = Callable[
    [Optional[CreateAgentSessionOptions]], Awaitable[AgentSessionRuntime]
]
SessionIdOf = Callable[[AgentSessionRuntime], str]


@dataclass(frozen=True, kw_only=True)
class SessionShutdownReport:
    """``shutdown_all`` 的分桶报告（对位 codex ``ThreadShutdownReport``）。"""

    completed: Tuple[str, ...] = ()
    failed: Tuple[str, ...] = ()
    timed_out: Tuple[str, ...] = ()


class SessionIdCollisionError(RuntimeError):
    """open 出的 session_id 已在注册表中（在位者赢，新来者已被销毁）。"""


def _default_session_id(runtime: AgentSessionRuntime) -> str:
    return runtime.session.session_manager.get_session_id()


def _default_runtime_factory(
    options: Optional[CreateAgentSessionOptions],
) -> Awaitable[AgentSessionRuntime]:
    # 延迟导入：manager 不在包初始化期拉起 assembly（导入顺序不变量，
    # 见包 __init__ 注释），也方便测试注入工厂后无需真实组装链。
    from nova_harness.core.runtime_manager.assembly import (
        create_agent_session_runtime,
    )

    return create_agent_session_runtime(options)


class RuntimeManager:
    """进程级唯一会话工厂 + 注册表。外部（server / app / sdk 薄壳）创建
    会话一律走 ``open_session``；销毁走 ``close_session`` / ``close_runtime``
    / ``shutdown_all``，或持有 runtime 自行 ``dispose`` 后不再回归注册表。"""

    def __init__(
        self,
        *,
        agent_dir: Optional[Path] = None,
        runtime_factory: RuntimeSessionFactory = _default_runtime_factory,
        session_id_of: SessionIdOf = _default_session_id,
    ) -> None:
        self._agent_dir = agent_dir
        self._runtime_factory = runtime_factory
        self._session_id_of = session_id_of
        self._runtimes: Dict[str, AgentSessionRuntime] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 创建（registration last）
    # ------------------------------------------------------------------

    async def open_session(
        self,
        options: Optional[CreateAgentSessionOptions] = None,
    ) -> AgentSessionRuntime:
        """创建新会话运行时并注册。

        默认填充只经 ``dataclasses.replace`` 产出新 options，不改调用方
        对象；组装失败时注册表保持原样（registration last，零回滚）。
        """
        resolved = self._with_defaults(options)
        runtime = await self._runtime_factory(resolved)
        session_id = self._session_id_of(runtime)

        async with self._lock:
            if session_id in self._runtimes:
                # 在位者赢：新来者销毁（锁外执行，见下）
                incumbent = True
            else:
                self._runtimes[session_id] = runtime
                incumbent = False
        if incumbent:
            await runtime.dispose()
            raise SessionIdCollisionError(f"session '{session_id}' is already running")
        return runtime

    def _with_defaults(
        self, options: Optional[CreateAgentSessionOptions]
    ) -> CreateAgentSessionOptions:
        if options is None:
            if self._agent_dir is not None:
                return CreateAgentSessionOptions(agent_dir=self._agent_dir)
            return CreateAgentSessionOptions()
        if self._agent_dir is not None and options.agent_dir is None:
            return replace(options, agent_dir=self._agent_dir)
        return options

    # ------------------------------------------------------------------
    # 销毁
    # ------------------------------------------------------------------

    async def close_session(self, session_id: str) -> bool:
        """按 open 时刻的 session_id 销毁并注销。返回是否命中。"""
        async with self._lock:
            runtime = self._runtimes.pop(session_id, None)
        if runtime is None:
            return False
        await runtime.dispose()
        return True

    async def close_runtime(self, runtime: AgentSessionRuntime) -> bool:
        """按对象身份销毁并注销（session_id 原地漂移安全）。

        不在注册表的 runtime 不动、不销毁（返回 False）——销毁责任仍在
        调用方，避免误杀他人持有的实例。
        """
        async with self._lock:
            found_id: Optional[str] = None
            for session_id, registered in self._runtimes.items():
                if registered is runtime:
                    found_id = session_id
                    break
            if found_id is not None:
                del self._runtimes[found_id]
        if found_id is None:
            return False
        await runtime.dispose()
        return True

    async def shutdown_all(self, timeout: float = 10.0) -> SessionShutdownReport:
        """有界并发关停全部注册会话；只移除 completed。

        对位 codex ``shutdown_all_threads_bounded``：锁内快照后立刻放锁，
        逐个 ``wait_for`` 超时扇出，超时/失败者留在注册表供重试。
        """
        async with self._lock:
            items: List[Tuple[str, AgentSessionRuntime]] = list(self._runtimes.items())

        outcomes = await asyncio.gather(
            *(self._dispose_bounded(runtime, timeout) for _, runtime in items)
        )

        completed: List[str] = []
        failed: List[str] = []
        timed_out: List[str] = []
        for (session_id, _runtime), outcome in zip(items, outcomes):
            if outcome == "completed":
                completed.append(session_id)
            elif outcome == "failed":
                failed.append(session_id)
            else:
                timed_out.append(session_id)

        async with self._lock:
            for session_id in completed:
                self._runtimes.pop(session_id, None)

        return SessionShutdownReport(
            completed=tuple(sorted(completed)),
            failed=tuple(sorted(failed)),
            timed_out=tuple(sorted(timed_out)),
        )

    @staticmethod
    async def _dispose_bounded(runtime: AgentSessionRuntime, timeout: float) -> str:
        try:
            await asyncio.wait_for(runtime.dispose(), timeout)
        except asyncio.TimeoutError:
            return "timed_out"
        except Exception:
            return "failed"
        return "completed"

    # ------------------------------------------------------------------
    # 查询（仅活跃注册表）
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Optional[AgentSessionRuntime]:
        """按 open 时刻的 session_id 取运行时；仅查活跃注册表。"""
        return self._runtimes.get(session_id)

    def list_session_ids(self) -> List[str]:
        """列出活跃会话 id（注册表键 = open 时刻 id，见模块 docstring）。"""
        return list(self._runtimes.keys())
