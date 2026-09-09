"""experiment_mode 扩展（/experiment 模式一期：自动记录 + 自动召回）。

长任务（多步骤计算流水线）场景下的自动记忆系统（薄编排层——候选发现 /
schema 推导 / memory 读写 / 记录判定分别在 ``nova_coding_agent.experiment``
包的 steps / memory_store / recorder 模块，本文件只做命令与事件接线）。

机制全貌：

- ``/experiment`` 切换模式；``/experiment <skill名>`` 直接指定绑定；
  ``--experiment`` 启动旗标。严格模式：未绑定合格 skill 不允许开启；
- 模式状态经 ``append_entry("experiment-mode")`` 持久化、``session_start``
  重建；skill 绑定按项目持久化在 ``memory/system/binding.json``；
- ``input`` 事件实现绑定决策链（§4.2.1：提示词点名优先 → 持久绑定兜底 →
  现场扫描 1 个直绑 / ≥2 个弹选择器 / 0 个提示并拒绝启动任务）；任务开始
  时现场初始化 memory 骨架 + 推导步骤 schema 落 ``memory/system/``；
- ``tool_call`` 捕获 bash 命令（命令全文 + 引用 yaml 全文快照）；
  ``tool_execution_end`` 产物盘点；``agent_end`` 四标准判定 + 生成
  ``memory/tasks/<task_id>.md`` 并更新 INDEX；
- ``before_agent_start`` 注入召回内容（display=False，
  custom_type=experiment-memory）；``context`` 事件滤除旧注入防堆积；
- 模式关闭时所有事件钩子首行即退（零开销）；一切写盘/解析失败降级，
  绝不中断用户任务。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from nova_base.ui_primitives import confirm, notify_message, select, set_status
from nova_coding_agent.experiment import memory_store, recorder, steps

from nova_harness.core.extensions.api import NovaExtensionAPI
from nova_harness.core.types.events.results import (
    BeforeAgentStartEventResult,
    ContextEventResult,
    InputEventResult,
)
from nova_harness.core.types.messages import CustomMessage

logger = logging.getLogger(__name__)

# 召回注入消息与过滤锚点
_RECALL_CUSTOM_TYPE = "experiment-memory"


def _now_ms() -> int:
    return int(time.time() * 1000)


def extension(nova: NovaExtensionAPI) -> None:
    """注册 experiment_mode 扩展。"""
    # 扩展实例闭包状态（reload 后由 session_start 从条目重建）
    state: Dict[str, Any] = {
        "enabled": False,
        "skill": None,  # 持久绑定的 skill 名
        "schema": None,  # 当前任务的步骤 schema（dict）
        "record": None,  # 当前任务的 TaskRecord（None = 无进行中任务）
    }

    # ------------------------------------------------------------------
    # 通用辅助
    # ------------------------------------------------------------------

    def _notify(ctx: Any, message: str, level: str = "info") -> None:
        if ctx.has_ui:
            notify_message(ctx.ui, message, level)

    def _persist(ctx: Any) -> None:
        ctx.append_entry(
            "experiment-mode",
            {"enabled": state["enabled"], "skill": state["skill"]},
        )

    def _update_status(ctx: Any) -> None:
        """footer 扩展状态行：🧪 exp·<skill>；关闭时清除。"""
        if not ctx.has_ui:
            return
        if state["enabled"]:
            skill = state["skill"] or "未绑定"
            set_status(ctx.ui, "experiment-mode", f"🧪 exp·{skill}")
        else:
            set_status(ctx.ui, "experiment-mode", None)

    def _qualified_candidates(ctx: Any) -> List[steps.SkillCandidate]:
        """现场扫描合格候选（扫描失败降级为空列表）。"""
        try:
            return [c for c in steps.scan_candidates(cwd=ctx.cwd) if c.qualified]
        except Exception as exc:  # noqa: BLE001 —— 扫描失败不中断任务
            logger.warning("experiment: 候选扫描失败: %s", exc)
            return []

    def _placement_guide() -> str:
        return (
            "未找到合格的多步骤 skill。请将 skill 放置于项目 .agents/skills/<名>/ "
            "（或 ~/.agents/skills/<名>/）。资格判据：① 含 docs/ 目录；"
            "② docs/ 内含 ≥2 个编号步骤文档（如 00_*.md、01_*.md）；"
            "③ 步骤文档中含可执行脚本（.py）引用。"
        )

    def _restore_binding_from_file(ctx: Any) -> None:
        """项目级绑定文件兜底（后续会话自动沿用）。"""
        if state["skill"]:
            return
        binding = memory_store.read_binding(ctx.cwd)
        if isinstance(binding, dict):
            name = binding.get("skill")
            skill_dir = binding.get("skill_dir", "")
            if isinstance(name, str) and name and Path(skill_dir).is_dir():
                state["skill"] = name

    def _bind(ctx: Any, candidate: steps.SkillCandidate, announce: bool = True) -> None:
        """绑定 skill：写项目级绑定文件 + 推导 schema 落盘 + 初始化骨架。"""
        state["skill"] = candidate.name
        memory_store.ensure_skeleton(ctx.cwd)
        memory_store.write_binding(
            ctx.cwd,
            {
                "skill": candidate.name,
                "skill_dir": candidate.dir,
                "bound_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        try:
            schema = steps.derive_skill_schema(candidate.name, candidate.dir)
            memory_store.write_schema(ctx.cwd, schema)
        except Exception as exc:  # noqa: BLE001 —— schema 推导失败降级为少检查
            logger.warning("experiment: schema 推导失败: %s", exc)
        _persist(ctx)
        _update_status(ctx)
        if announce:
            _notify(ctx, f"Experiment: 已绑定 skill `{candidate.name}`。")

    def _begin_task(ctx: Any, skill_name: str, prompt: str) -> None:
        """任务开始：骨架 + schema + 建立 TaskRecord（失败降级，不阻塞任务）。"""
        try:
            memory_store.ensure_skeleton(ctx.cwd)
            schema = memory_store.read_schema(ctx.cwd)
            # schema 缺失或绑定变更时现场重推导
            if not schema or schema.get("skill_name") != skill_name:
                candidates = _qualified_candidates(ctx)
                cand = next((c for c in candidates if c.name == skill_name), None)
                if cand is not None:
                    schema = steps.derive_skill_schema(cand.name, cand.dir)
                    memory_store.write_schema(ctx.cwd, schema)
            state["schema"] = schema or {"skill_name": skill_name, "steps": []}
            state["record"] = recorder.start_record(
                ctx.cwd, skill_name, prompt, state["schema"]
            )
        except Exception as exc:  # noqa: BLE001 —— 记录失败不阻塞任务
            logger.warning("experiment: 任务记录初始化失败: %s", exc)
            state["record"] = None
            _notify(
                ctx, "Experiment: 任务记录初始化失败（任务本身不受影响）。", "warning"
            )

    def _finish_task(ctx: Any, messages: List[Any]) -> None:
        """任务结束：四标准判定 + 任务页 + INDEX（失败降级通知，不抛错）。"""
        record = state["record"]
        state["record"] = None
        try:
            record.interrupted = recorder.detect_interruption(messages)
            criteria = recorder.evaluate_criteria(record)
            status = recorder.overall_status(record, criteria)
            page = recorder.render_task_page(record, criteria, status)
            path = memory_store.write_task_page(ctx.cwd, record.task_id, page)
            memory_store.update_index(
                ctx.cwd, record.task_id, recorder.index_summary_line(record, status)
            )
            if path:
                _notify(
                    ctx,
                    f"Experiment: 任务档案已写入 {path}（判定：{status}）。",
                    "info" if status == "completed" else "warning",
                )
        except Exception as exc:  # noqa: BLE001 —— 写盘失败不中断任务
            logger.warning("experiment: 任务页生成失败: %s", exc)
            _notify(ctx, "Experiment: 任务档案写入失败（详见日志）。", "warning")

    # ------------------------------------------------------------------
    # 绑定决策链（§4.2.1）——input 事件
    # ------------------------------------------------------------------

    async def _resolve_task_skill(ctx: Any, text: str) -> Optional[str]:
        """决定本任务使用的 skill 名；返回 None 表示任务不应启动。"""
        # 现场扫描一次：全量候选（含不合格——点名不合格时要能报出理由）
        try:
            all_candidates = steps.scan_candidates(cwd=ctx.cwd)
        except Exception as exc:  # noqa: BLE001 —— 扫描失败不中断任务
            logger.warning("experiment: 候选扫描失败: %s", exc)
            all_candidates = []
        candidates = [c for c in all_candidates if c.qualified]
        # 1. 提示词点名
        mentioned = steps.find_mentioned_skills(text, all_candidates)
        if mentioned:
            name = mentioned[0]
            cand = next((c for c in candidates if c.name == name), None)
            if cand is None:
                # 点名但不合格/不存在 → 提示并列出当前候选，按未点名继续
                unqualified = next((c for c in all_candidates if c.name == name), None)
                reason = "、".join(unqualified.reasons) if unqualified else "不存在"
                available = "、".join(c.name for c in candidates) or "（无）"
                _notify(
                    ctx,
                    f"Experiment: 点名的 skill `{name}` 不可用（{reason}）。"
                    f"当前合格候选：{available}",
                    "warning",
                )
            else:
                # 与持久绑定不同 → 询问是否切换默认绑定
                if state["skill"] and state["skill"] != cand.name:
                    switch = False
                    if ctx.has_ui:
                        switch = await confirm(
                            ctx.ui,
                            "Experiment 绑定切换",
                            f"本任务使用 `{cand.name}`，与当前默认绑定 "
                            f"`{state['skill']}` 不同。是否切换默认绑定？",
                        )
                    if switch:
                        _bind(ctx, cand)
                elif not state["skill"]:
                    _bind(ctx, cand)
                return cand.name

        # 2. 未点名 + 已有持久绑定 → 沿用
        _restore_binding_from_file(ctx)
        if state["skill"]:
            return state["skill"]

        # 3. 未点名 + 无绑定 → 现场扫描
        if len(candidates) == 1:
            _bind(ctx, candidates[0])
            return candidates[0].name
        if len(candidates) >= 2:
            if not ctx.has_ui:
                _notify(
                    ctx,
                    "Experiment: 存在多个合格 skill，请用 /experiment <名> 指定绑定。",
                    "warning",
                )
                return None
            choice = await select(
                ctx.ui,
                "Experiment: 选择本任务绑定的 skill",
                [c.name for c in candidates],
            )
            if choice is None:
                _notify(ctx, "Experiment: 未选择 skill，任务未启动。", "warning")
                return None
            cand = next((c for c in candidates if c.name == choice), None)
            if cand is None:
                return None
            _bind(ctx, cand)
            return cand.name

        # 0 个候选 → 提示资格判据，任务不启动
        _notify(ctx, "Experiment: " + _placement_guide(), "warning")
        return None

    async def _on_input(event: Any, ctx: Any) -> Optional[InputEventResult]:
        if not state["enabled"]:
            return None
        text = getattr(event, "text", "") or ""
        skill_name = await _resolve_task_skill(ctx, text)
        if skill_name is None:
            # 任务不启动（提示已在决策链中发出）
            return InputEventResult(action="handled")
        _begin_task(ctx, skill_name, text)
        return InputEventResult(action="continue")

    # ------------------------------------------------------------------
    # 记录钩子（§4.5）
    # ------------------------------------------------------------------

    async def _on_tool_call(event: Any, ctx: Any) -> None:
        record = state["record"]
        if not state["enabled"] or record is None:
            return None
        if getattr(event, "tool_name", "") != "bash":
            return None
        args = event.args if isinstance(event.args, dict) else {}
        command = args.get("command")
        if isinstance(command, str) and command:
            try:
                record.record_command(getattr(event, "tool_call_id", ""), command)
            except Exception as exc:  # noqa: BLE001
                logger.warning("experiment: 命令记录失败: %s", exc)
        return None

    async def _on_tool_execution_end(event: Any, ctx: Any) -> None:
        record = state["record"]
        if not state["enabled"] or record is None:
            return None
        try:
            is_error = bool(getattr(event, "is_error", False))
            record.record_tool_end(
                getattr(event, "tool_call_id", ""),
                getattr(event, "tool_name", ""),
                is_error,
                error_summary=(
                    recorder.summarize_error(getattr(event, "result", None))
                    if is_error
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("experiment: 结果记录失败: %s", exc)
        return None

    async def _on_agent_end(event: Any, ctx: Any) -> None:
        if not state["enabled"] or state["record"] is None:
            return None
        _finish_task(ctx, list(getattr(event, "messages", []) or []))
        return None

    # ------------------------------------------------------------------
    # 召回（§4.7）
    # ------------------------------------------------------------------

    async def _on_before_agent_start(
        event: Any, ctx: Any
    ) -> Optional[BeforeAgentStartEventResult]:
        if not state["enabled"]:
            return None
        schema = state["schema"] or memory_store.read_schema(ctx.cwd)
        if not schema:
            return None
        try:
            content = memory_store.build_recall_content(ctx.cwd, schema)
        except Exception as exc:  # noqa: BLE001
            logger.warning("experiment: 召回内容组装失败: %s", exc)
            return None
        return BeforeAgentStartEventResult(
            message=CustomMessage(
                custom_type=_RECALL_CUSTOM_TYPE,
                content=content,
                display=False,
                timestamp=_now_ms(),
            )
        )

    async def _on_context(event: Any, ctx: Any) -> Optional[ContextEventResult]:
        """滤除旧召回注入，防止重复堆积（每轮由 before_agent_start 重新注入）。"""
        if not state["enabled"]:
            return None
        filtered = [
            m
            for m in event.messages
            if getattr(m, "custom_type", None) != _RECALL_CUSTOM_TYPE
        ]
        if len(filtered) == len(event.messages):
            return None
        return ContextEventResult(messages=filtered)

    # ------------------------------------------------------------------
    # 命令与生命周期
    # ------------------------------------------------------------------

    async def _enable(ctx: Any) -> None:
        """开启模式（严格模式：拿不到合格绑定则拒绝开启）。"""
        _restore_binding_from_file(ctx)
        if state["skill"] is None:
            candidates = _qualified_candidates(ctx)
            if len(candidates) == 1:
                _bind(ctx, candidates[0])
            elif len(candidates) >= 2 and ctx.has_ui:
                choice = await select(
                    ctx.ui,
                    "Experiment: 选择要绑定的 skill",
                    [c.name for c in candidates],
                )
                cand = next((c for c in candidates if c.name == choice), None)
                if cand is None:
                    _notify(ctx, "Experiment: 未选择 skill，模式未开启。", "warning")
                    return
                _bind(ctx, cand)
            elif len(candidates) >= 2:
                _notify(
                    ctx,
                    "Experiment: 存在多个合格 skill，请用 /experiment <名> 指定。",
                    "warning",
                )
                return
            else:
                _notify(ctx, "Experiment: " + _placement_guide(), "warning")
                return
        state["enabled"] = True
        _persist(ctx)
        _update_status(ctx)
        _notify(ctx, f"Experiment mode enabled（skill: {state['skill']}）。")

    def _disable(ctx: Any) -> None:
        state["enabled"] = False
        state["record"] = None
        state["schema"] = None
        _persist(ctx)
        _update_status(ctx)
        _notify(ctx, "Experiment mode disabled.")

    async def _cmd_experiment(args: str, ctx: Any) -> None:
        name = (args or "").strip()
        if name:
            # /experiment <skill名>：直接指定绑定（含开启）
            try:
                all_candidates = steps.scan_candidates(cwd=ctx.cwd)
            except Exception as exc:  # noqa: BLE001
                logger.warning("experiment: 候选扫描失败: %s", exc)
                all_candidates = []
            cand = next(
                (
                    c
                    for c in all_candidates
                    if c.name == name or Path(c.dir).name == name
                ),
                None,
            )
            if cand is None:
                available = "、".join(c.name for c in all_candidates) or "（无）"
                _notify(
                    ctx,
                    f"Experiment: 未找到 skill `{name}`。已发现候选：{available}",
                    "warning",
                )
                return
            if not cand.qualified:
                _notify(
                    ctx,
                    f"Experiment: skill `{name}` 不合格（{'、'.join(cand.reasons)}）。",
                    "warning",
                )
                return
            _bind(ctx, cand)
            if not state["enabled"]:
                state["enabled"] = True
                _persist(ctx)
                _update_status(ctx)
                _notify(ctx, f"Experiment mode enabled（skill: {cand.name}）。")
            return
        if state["enabled"]:
            _disable(ctx)
        else:
            await _enable(ctx)

    # ---- session_start（含 reload）：旗标 + 条目重建状态 ----
    async def _on_session_start(event: Any, ctx: Any) -> None:
        if nova.getFlag("experiment") is True:
            state["enabled"] = True

        sm = ctx.session_manager
        if sm is not None:
            entry = next(
                (
                    e
                    for e in reversed(sm.get_entries())
                    if getattr(e, "type", "") == "custom"
                    and getattr(e, "custom_type", "") == "experiment-mode"
                ),
                None,
            )
            data = getattr(entry, "data", None) if entry else None
            if isinstance(data, dict):
                state["enabled"] = data.get("enabled", state["enabled"])
                state["skill"] = data.get("skill", state["skill"])

        if state["enabled"]:
            _restore_binding_from_file(ctx)
        _update_status(ctx)
        return None

    nova.registerCommand(
        "experiment",
        {
            "description": "切换 experiment 模式（长任务自动记忆与审计）；/experiment <skill名> 直接绑定",
            "handler": _cmd_experiment,
        },
    )
    nova.registerFlag(
        "experiment",
        {
            "description": "启动时进入 experiment 模式",
            "type": "boolean",
            "default": False,
        },
    )
    nova.on("input", _on_input)
    nova.on("tool_call", _on_tool_call)
    nova.on("tool_execution_end", _on_tool_execution_end)
    nova.on("agent_end", _on_agent_end)
    nova.on("before_agent_start", _on_before_agent_start)
    nova.on("context", _on_context)
    nova.on("session_start", _on_session_start)
