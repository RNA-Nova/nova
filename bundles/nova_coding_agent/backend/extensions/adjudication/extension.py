"""adjudication 扩展入口——裁决子系统装配（permission_gate 的终态收编）。

装配（分层纪律：引擎在 nova_protocol、规则 I/O 在 harness、本扩展只做
接线 + 政策 + UI 词汇）：

- `RulesStore`（harness environments 子域）读 `rules.lark` → Policy；
- `AdjudicationEngine`（本目录 engine.py）：intent 分析 + execpolicy 求值；
- `ApprovalFlow`（本目录 approval.py）：弹窗 + 会话缓存 + 永远允许写回；
- 注册 Orchestrator 工厂（`nova_coding_agent.orchestration`）——bash 等
  执行类工具执行期内 `new_orchestrator()` 现造（pull 模型，codex 同款）；
- write/edit 保护路径经 tool_call 事件裁决（非 bash 工具的裁决点——
  bash 裁决在工具内走 Orchestrator，不经本事件）；
- 会话缓存分支恢复（permission_decision 条目回放）。
"""

from __future__ import annotations

from typing import Any, Optional

from nova_exec_server_client import default_exec_server_home, load_executor_config
from nova_harness.core.agent_session.controllers.orchestrator import Orchestrator
from nova_harness.core.harness.environments import RulesStore
from nova_harness.events.results import ToolCallEventResult
from nova_harness.extensions.api import NovaExtensionAPI
from nova_protocol.exec_server_policy import Policy

from nova_coding_agent.orchestration import AdjudicationAssembly, register_adjudication_assembly

from nova_coding_agent.adjudication.approval import ApprovalFlow
from nova_coding_agent.adjudication.engine import AdjudicationEngine

_ENTRY_TYPE = "permission_decision"


def extension(nova: NovaExtensionAPI) -> None:
    config = load_executor_config(project_trusted=False)
    rules_store = RulesStore(default_exec_server_home())
    try:
        policy = rules_store.load()
    except Exception:
        # 规则文件损坏：响亮报出并 fail-closed（空 Policy 全过审批——更严）
        import logging

        logging.getLogger(__name__).exception("rules.lark 解析失败，按空规则集 fail-closed 运行")
        policy = Policy.empty()

    engine = AdjudicationEngine(policy, config.approval_policy)
    flow = ApprovalFlow(rules_store, config.approval_policy)

    register_adjudication_assembly(
        AdjudicationAssembly(
            new_orchestrator=lambda: Orchestrator(
                approval_policy=config.approval_policy,
                request_approval=flow.request_approval,
            ),
            engine=engine,
        )
    )

    async def _on_tool_call(event: Any, ctx: Any) -> Optional[ToolCallEventResult]:
        """write/edit 保护路径裁决点（bash 不经此——走工具内 Orchestrator）。"""
        if event.tool_name not in ("write", "edit"):
            return None
        path = (event.args or {}).get("path") if isinstance(event.args, dict) else None
        if not isinstance(path, str) or not path:
            return None
        requirement = engine.adjudicate(event.tool_name, {"path": path}, ctx)
        if requirement.kind == "forbidden":
            flow._record(ctx, event.tool_name, path, "blocked", requirement.reason or "")
            return ToolCallEventResult(block=True, reason=requirement.reason)
        return None

    nova.on("tool_call", _on_tool_call)

    def _restore(ctx: Any) -> None:
        """会话条目回放重建"本会话允许"缓存（分支安全）。"""
        sm = getattr(ctx, "session_manager", None)
        if sm is None:
            return
        keys: list[tuple[str, str | None]] = []
        for entry in reversed(sm.get_branch()):
            if getattr(entry, "type", "") != "custom":
                continue
            if getattr(entry, "custom_type", "") != _ENTRY_TYPE:
                continue
            data = getattr(entry, "data", None)
            if isinstance(data, dict) and data.get("decision") == "always":
                keys.append((data.get("tool", ""), data.get("target")))
            if keys and len(keys) >= 64:
                break
        flow.restore_session_cache(keys)

    nova.on("session_start", lambda _event, ctx: _restore(ctx))
    nova.on("session_tree", lambda _event, ctx: _restore(ctx))
