"""Print 模式运行器。

提供非交互式运行单个 agent 的能力，支持 text 与 json 两种输出形态。
Print 模式不使用任何 UI 能力，仅依赖 ``NoOpUIContext`` 降级。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict, List, Optional

from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.harness.project_trust import (
    ProjectTrustStore,
    resolve_project_trusted,
)
from nova_harness.core.harness.session.manager import SessionManager
from nova_harness.core.sdk import create_agent_session_runtime
from nova_harness.core.types.project_trust import (
    ProjectTrustContext,
    ResolveProjectTrustedOptions,
)
from nova_harness.core.types.session.config import CreateAgentSessionOptions
from nova_harness.core.ui.noop import NoOpUIContext


class PrintRunner:
    """Print 模式运行器：非交互式执行 agent 任务并输出结果。"""

    def __init__(
        self,
        *,
        json_output: bool = False,
        no_session: bool = False,
        trust: Optional[bool] = None,
    ) -> None:
        self._json_output = json_output
        self._no_session = no_session
        self._trust = trust

    async def run_task(
        self,
        agent_name: str,
        task: str,
        cwd: Optional[str] = None,
    ) -> int:
        """运行一次 agent 任务并输出结果。"""
        import os

        agent_dir = get_agent_dir()
        resolved_cwd = cwd or os.getcwd()
        session_manager = None
        if self._no_session:
            session_manager = SessionManager.in_memory(resolved_cwd)
        runtime = await create_agent_session_runtime(
            CreateAgentSessionOptions(
                cwd=resolved_cwd,
                agent_dir=agent_dir,
                agent_name=agent_name,
                session_manager=session_manager,
                project_trusted=self._trust,
                resolve_project_trust=_make_resolve_project_trust(
                    resolved_cwd, agent_dir, trust_override=self._trust
                ),
            )
        )

        unsubscribe: Optional[Callable[[], None]] = None
        try:
            if self._json_output:
                self._emit_jsonl_header(runtime)
                unsubscribe = self._subscribe_jsonl(runtime)
            await runtime.session.prompt(task)
            await runtime.session.agent.wait_for_idle()
        finally:
            if unsubscribe is not None:
                unsubscribe()
            await runtime.dispose()

        if not self._json_output:
            self._emit_text(runtime)
        return 0

    def _subscribe_jsonl(self, runtime: Any) -> Callable[[], None]:
        """订阅 Agent 事件并以 JSONL 输出所有事件。"""

        def on_event(event: Any) -> None:
            self._emit_jsonl_event(event)

        return runtime.session.subscribe(on_event)

    def _emit_jsonl_header(self, runtime: Any) -> None:
        """输出会话 header（JSON 模式）。"""
        session_manager = getattr(runtime.session, "session_manager", None)
        if session_manager is None:
            return
        header = session_manager.get_header()
        if header is None:
            return
        self._emit_jsonl("session", _serialize_object(header))

    def _emit_text(self, runtime: Any) -> None:
        """输出纯文本结果。"""
        agent = getattr(runtime.session, "agent", None)
        state = getattr(agent, "state", None)
        messages = getattr(state, "messages", []) if state else []
        output = _get_final_output(messages)
        if output:
            sys.stdout.write(output + "\n")
            sys.stdout.flush()

    def _emit_jsonl(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Write a JSONL event to stdout."""
        event: Dict[str, Any] = {"type": event_type, **payload}
        self._emit_jsonl_event(event)

    def _emit_jsonl_event(self, event: Any) -> None:
        """将任意可序列化对象作为 JSONL 写入 stdout。"""
        data = _serialize_object(event)
        sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _make_resolve_project_trust(
    cwd: str, agent_dir: str, trust_override: Optional[bool] = None
):
    """构造 print 模式使用的 project trust 决议回调。"""
    settings_manager = SettingsManager.create(
        cwd=cwd, agent_dir=agent_dir, project_trusted=True
    )
    default_project_trust = settings_manager.get_default_project_trust()

    async def resolve_project_trust(extensions_result: Any) -> bool:
        trust_store = ProjectTrustStore.for_agent_dir(agent_dir)
        return await resolve_project_trusted(
            ResolveProjectTrustedOptions(
                cwd=cwd,
                trust_store=trust_store,
                trust_override=trust_override,
                default_project_trust=default_project_trust,
                extensions_result=extensions_result,
                project_trust_context=ProjectTrustContext(
                    cwd=cwd,
                    mode="print",
                    has_ui=False,
                    ui=NoOpUIContext(),
                ),
            )
        )

    return resolve_project_trust


async def run_print_mode(
    agent_name: str,
    task: str,
    cwd: Optional[str] = None,
    *,
    json_output: bool = False,
    no_session: bool = False,
    trust: Optional[bool] = None,
) -> int:
    """以 print 模式运行一次 agent 任务。"""
    runner = PrintRunner(json_output=json_output, no_session=no_session, trust=trust)
    return await runner.run_task(agent_name, task, cwd)


def _get_final_output(messages: List[Any]) -> str:
    """Extract the last assistant text from a message list."""
    for msg in reversed(messages):
        if getattr(msg, "role", None) == "assistant":
            for part in getattr(msg, "content", []):
                if getattr(part, "type", None) == "text":
                    return part.text or ""
    return ""


def _serialize_object(obj: Any) -> Any:
    """Serialize an object to a JSON-friendly value."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "__dict__"):
        return {
            k: _serialize_object(v)
            for k, v in obj.__dict__.items()
            if not k.startswith("_")
        }
    return obj
