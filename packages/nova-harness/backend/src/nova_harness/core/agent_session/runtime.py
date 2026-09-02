"""
AgentSessionRuntime - AgentSession 生命周期管理层。

把会话切换、fork、导航等“替换当前 runtime”的操作从 AgentSession 中抽出来，
让 AgentSession 专注单一会话内的消息/工具/事件处理。

- Runtime 持有 ``create_runtime`` 工厂，每次替换会话时通过工厂重新创建 services + session。
- 提供 ``set_rebind_session`` / ``set_before_session_invalidate`` 钩子。
- 负责 ``session_before_switch`` / ``session_before_fork`` / ``session_shutdown`` 等生命周期事件。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from nova_ai import UserMessage
from nova_harness.core.agent_session.agent import AgentSession
from nova_harness.core.agent_session.services import AgentSessionServices
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.types.events import (
    SessionBeforeForkEvent,
    SessionBeforeSwitchEvent,
    SessionReplacedEvent,
    SessionShutdownEvent,
    SessionStartEvent,
)
from nova_harness.core.types.session import (
    ForkOptions,
    NewSessionOptions,
    SwitchSessionOptions,
)
from nova_harness.core.types.session.diagnostics import AgentSessionRuntimeDiagnostic
from nova_harness.core.types.session.factory import (
    CreateAgentSessionRuntimeFactory,
    CreateAgentSessionRuntimeOptions,
    CreateAgentSessionRuntimeResult,
)
from nova_harness.core.utils.session_cwd import assert_session_cwd_exists


class SessionImportFileNotFoundError(FileNotFoundError):
    """导入 JSONL 时输入路径不存在。"""

    def __init__(self, file_path: str) -> None:
        super().__init__(f"File not found: {file_path}")
        self.file_path = file_path


class AgentSessionRuntime:
    """
    持有当前 AgentSession 及其绑定的服务集合，负责会话的创建、切换、fork 和释放。

    当会话发生变化时，runtime 会：
    1. 触发 ``session_before_switch`` / ``session_before_fork`` 扩展事件
    2. teardown 旧的 AgentSession（``session_shutdown`` + dispose）
    3. 通过 ``create_runtime`` 工厂创建新的 services + session
    4. 替换 self._session / self._services
    5. 触发 ``session_start`` 并调用 rebind / with_session 回调
    """

    def __init__(
        self,
        session: AgentSession,
        services: AgentSessionServices,
        create_runtime: CreateAgentSessionRuntimeFactory,
        diagnostics: Optional[List[AgentSessionRuntimeDiagnostic]] = None,
        model_fallback_message: Optional[str] = None,
    ) -> None:
        self._session = session
        self._services = services
        self._session_manager = session.session_manager
        self._create_runtime = create_runtime
        self._diagnostics = diagnostics or []
        self._model_fallback_message = model_fallback_message

        self._rebind_session: Optional[Callable[[AgentSession], Awaitable[None]]] = None
        self._before_session_invalidate: Optional[Callable[[], None]] = None

    @property
    def session(self) -> AgentSession:
        return self._session

    @property
    def services(self) -> AgentSessionServices:
        return self._services

    @property
    def cwd(self) -> str:
        return self._services.cwd

    @property
    def session_manager(self) -> SessionManager:
        return self._session.session_manager

    @property
    def diagnostics(self) -> List[AgentSessionRuntimeDiagnostic]:
        return self._diagnostics

    @property
    def model_fallback_message(self) -> Optional[str]:
        return self._model_fallback_message

    def set_rebind_session(
        self, callback: Optional[Callable[[AgentSession], Awaitable[None]]]
    ) -> None:
        """设置 session 替换完成后的重新绑定回调（如 UI 重新订阅事件）。"""
        self._rebind_session = callback

    def set_before_session_invalidate(
        self, callback: Optional[Callable[[], None]]
    ) -> None:
        """
        设置在旧 session 被销毁前、同步执行的回调。

        用于宿主层在旧扩展上下文失效前拆卸 TUI 组件等操作。
        """
        self._before_session_invalidate = callback

    # -------------------------------------------------------------------------
    # 事件辅助
    # -------------------------------------------------------------------------

    def _extension_runner(self) -> Optional[Any]:
        return self._session.extension_runner

    async def _emit_before_switch(
        self, reason: str, target_session_file: Optional[str] = None
    ) -> Dict[str, bool]:
        runner = self._extension_runner()
        if runner is None:
            return {"cancelled": False}
        result = await runner.emit(
            SessionBeforeSwitchEvent(
                reason=reason, target_session_file=target_session_file
            )
        )
        return {"cancelled": bool(getattr(result, "cancel", False))}

    async def _emit_before_fork(self, entry_id: str, position: str) -> Dict[str, bool]:
        runner = self._extension_runner()
        if runner is None:
            return {"cancelled": False}
        result = await runner.emit(
            SessionBeforeForkEvent(entry_id=entry_id, position=position)
        )
        return {"cancelled": bool(getattr(result, "cancel", False))}

    async def _teardown_current(
        self, reason: str, target_session_file: Optional[str] = None
    ) -> None:
        runner = self._extension_runner()
        if runner is not None and runner.has_handlers("session_shutdown"):
            await runner.emit(
                SessionShutdownEvent(
                    reason=reason,
                    target_session_file=target_session_file,
                )
            )
        if self._before_session_invalidate is not None:
            self._before_session_invalidate()
        self._session.dispose()

    def _apply(self, result: CreateAgentSessionRuntimeResult) -> None:
        self._session = result.session
        self._services = result.services
        self._session_manager = result.session.session_manager
        self._diagnostics = result.diagnostics
        self._model_fallback_message = result.model_fallback_message

    async def _finish_session_replacement(
        self,
        reason: str,
        with_session: Optional[Callable[[Any], Awaitable[None]]] = None,
    ) -> None:
        if self._rebind_session is not None:
            await self._rebind_session(self._session)
        if with_session is not None:
            await with_session(self._session.create_replaced_session_context())
        # Bus 2 通知（前端全量重同步的触发点）——必须在 rebind 之后发射，
        # 否则 RPC 事件桥还订在旧 session 上，通知丢进虚空
        self._session._emit(SessionReplacedEvent(reason=reason))

    # -------------------------------------------------------------------------
    # 公开 API
    # -------------------------------------------------------------------------

    async def new_session(
        self, options: Optional[NewSessionOptions] = None
    ) -> Dict[str, Any]:
        """创建新会话并替换当前 runtime 中的 session。"""
        opts = options or NewSessionOptions()

        before = await self._emit_before_switch("new")
        if before["cancelled"]:
            return {"cancelled": True}

        previous_session_file = self._session.session_file
        session_manager = (
            SessionManager.create(
                self._services.cwd, self._session_manager.get_session_dir()
            )
            if self._session_manager.is_persisted()
            else SessionManager.in_memory(self._services.cwd)
        )
        if opts.parent_session:
            session_manager.new_session(parent_session=opts.parent_session)

        await self._teardown_current("new", session_manager.get_session_file())
        result = await self._create_runtime(
            CreateAgentSessionRuntimeOptions(
                cwd=self._services.cwd,
                agent_dir=self._services.agent_dir,
                session_manager=session_manager,
                session_start_event=SessionStartEvent(
                    reason="new", previous_session_file=previous_session_file
                ),
            )
        )
        self._apply(result)

        if opts.setup is not None:
            await opts.setup(self._session.session_manager)
            self._session.agent.state.messages = (
                self._session.session_manager.build_session_context().messages
            )

        await self._finish_session_replacement("new", opts.with_session)
        return {"cancelled": False}

    async def switch_session(
        self, session_path: str, options: Optional[SwitchSessionOptions] = None
    ) -> Dict[str, Any]:
        """切换到指定会话文件并重建 session。"""
        opts = options or SwitchSessionOptions()

        before = await self._emit_before_switch("resume", session_path)
        if before["cancelled"]:
            return {"cancelled": True}

        previous_session_file = self._session.session_file
        session_manager = SessionManager.open(session_path, None, opts.cwd_override)
        assert_session_cwd_exists(session_manager, self._services.cwd)

        await self._teardown_current(
            "resume", target_session_file=session_manager.get_session_file()
        )

        project_trust_context = None
        if opts.project_trust_context_factory is not None:
            project_trust_context = opts.project_trust_context_factory(
                session_manager.get_cwd()
            )

        result = await self._create_runtime(
            CreateAgentSessionRuntimeOptions(
                cwd=session_manager.get_cwd(),
                agent_dir=self._services.agent_dir,
                session_manager=session_manager,
                session_start_event=SessionStartEvent(
                    reason="resume", previous_session_file=previous_session_file
                ),
                project_trust_context=project_trust_context,
            )
        )
        self._apply(result)
        await self._finish_session_replacement("resume", opts.with_session)
        return {"cancelled": False}

    async def fork(
        self, entry_id: str, options: Optional[ForkOptions] = None
    ) -> Dict[str, Any]:
        """在指定条目处 fork 出新会话。"""
        opts = options or ForkOptions()
        position = opts.position

        before = await self._emit_before_fork(entry_id, position)
        if before["cancelled"]:
            return {"cancelled": True}

        selected_entry = self._session.session_manager.get_entry(entry_id)
        if selected_entry is None:
            raise ValueError("Invalid entry ID for forking")

        target_leaf_id: Optional[str]
        selected_text: Optional[str] = None

        if position == "at":
            target_leaf_id = selected_entry.id
        else:
            if selected_entry.type != "message" or not isinstance(
                selected_entry.message, UserMessage
            ):
                raise ValueError("Invalid entry ID for forking")
            target_leaf_id = selected_entry.parent_id
            selected_text = self._session.get_user_message_text(selected_entry.message)

        previous_session_file = self._session.session_file

        if self._session_manager.is_persisted():
            current_session_file = self._session.session_file
            if not current_session_file:
                raise ValueError("Persisted session is missing a session file")
            session_dir = self._session_manager.get_session_dir()

            if target_leaf_id is None:
                session_manager = SessionManager.create(self._services.cwd, session_dir)
                session_manager.new_session(parent_session=current_session_file)
            else:
                session_manager = SessionManager.open(current_session_file, session_dir)
                forked_path = session_manager.create_branched_session(target_leaf_id)
                if not forked_path:
                    raise ValueError("Failed to create forked session")
        else:
            session_manager = self._session_manager
            if target_leaf_id is None:
                session_manager.new_session(parent_session=self._session.session_file)
            else:
                session_manager.create_branched_session(target_leaf_id)

        await self._teardown_current("fork", session_manager.get_session_file())
        result = await self._create_runtime(
            CreateAgentSessionRuntimeOptions(
                cwd=session_manager.get_cwd(),
                agent_dir=self._services.agent_dir,
                session_manager=session_manager,
                session_start_event=SessionStartEvent(
                    reason="fork", previous_session_file=previous_session_file
                ),
            )
        )
        self._apply(result)
        await self._finish_session_replacement("fork", opts.with_session)
        return {"cancelled": False, "selected_text": selected_text}

    async def reload(self) -> Dict[str, Any]:
        """重新加载设置、资源与扩展，并刷新当前 session 的系统提示词。

        直接委托给 ``AgentSession.reload()``，确保扩展 runner、工具注册表和系统
        提示词都被重建，与 TypeScript 端行为一致。
        """
        await self._session.reload()
        return {"cancelled": False}

    async def import_from_jsonl(
        self, input_path: str, cwd_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """从 JSONL 文件导入会话并切换到该会话。

        Args:
            input_path: JSONL 文件路径。
            cwd_override: 可选的 cwd 覆盖。

        Returns:
            ``{"cancelled": false}`` 表示成功；若被 ``session_before_switch`` 取消则返回
            ``{"cancelled": true}``。

        Raises:
            SessionImportFileNotFoundError: 输入路径不存在。
        """
        resolved_path = str(Path(input_path).resolve())
        if not os.path.exists(resolved_path):
            raise SessionImportFileNotFoundError(resolved_path)

        session_dir = self._session_manager.get_session_dir()
        if session_dir and not os.path.exists(session_dir):
            os.makedirs(session_dir, exist_ok=True)

        destination_path = (
            os.path.join(session_dir, os.path.basename(resolved_path))
            if session_dir
            else resolved_path
        )

        before = await self._emit_before_switch("resume", destination_path)
        if before["cancelled"]:
            return {"cancelled": True}

        previous_session_file = self._session.session_file

        if os.path.abspath(destination_path) != os.path.abspath(resolved_path):
            shutil.copy2(resolved_path, destination_path)

        session_manager = SessionManager.open(
            destination_path, session_dir, cwd_override
        )
        assert_session_cwd_exists(session_manager, self._services.cwd)

        await self._teardown_current("resume", session_manager.get_session_file())
        result = await self._create_runtime(
            CreateAgentSessionRuntimeOptions(
                cwd=session_manager.get_cwd(),
                agent_dir=self._services.agent_dir,
                session_manager=session_manager,
                session_start_event=SessionStartEvent(
                    reason="resume", previous_session_file=previous_session_file
                ),
            )
        )
        self._apply(result)
        await self._finish_session_replacement("import")
        return {"cancelled": False}

    async def dispose(self) -> None:
        """释放当前 runtime 占用的资源。"""
        runner = self._extension_runner()
        if runner is not None and runner.has_handlers("session_shutdown"):
            await runner.emit(SessionShutdownEvent(reason="quit"))
        if self._before_session_invalidate is not None:
            self._before_session_invalidate()
        self._session.dispose()
