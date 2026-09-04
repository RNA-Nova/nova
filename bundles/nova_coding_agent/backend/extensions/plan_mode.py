"""plan_mode 扩展（Claude Code 风格只读规划模式）。

机制全貌（事件面全部现役）：

- ``/plan`` 命令 + ``ctrl+alt+p`` 快捷键 + ``--plan`` 启动旗标：切换规划模式；
- 模式开启时收缩工具集（禁用 edit/write——经 ``set_active_tools`` 从激活集
  移除，模型根本看不到），bash 收缩到只读命令白名单（``tool_call`` 拦截）；
- ``before_agent_start`` 注入规划/执行上下文消息（display=False 的自定义
  消息，custom_type=plan-mode-context）；``context`` 事件在非规划态滤除
  过期上下文消息；
- 模型在 "Plan:" 段输出编号计划 → ``agent_end`` 提取步骤并询问
  （执行/留在规划/改进）；选执行则恢复工具集进入执行态；
- 执行态中模型用 [DONE:n] 标记完成步骤（``turn_end`` 解析），全部完成
  自动收尾；
- 状态经 ``append_entry("plan-mode")`` 持久化，``session_start``（含
  reload）重建——分支/恢复所见即该历史点状态。

与 两处适配：①"Refine"用单行 ``input`` 原语（基线词汇无多行编辑器）；②pi 提示词里的 questionnaire/brave-search 引用
替换为本包的 question 工具。
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from nova_base.ui_primitives import input as ui_input
from nova_base.ui_primitives import notify_message, select, set_status

from nova_harness.core.extensions.api import NovaExtensionAPI
from nova_harness.core.types.events.results import (
    BeforeAgentStartEventResult,
    ContextEventResult,
    ToolCallEventResult,
)
from nova_harness.core.types.messages import CustomMessage

# ---------------------------------------------------------------------------
# bash 安全判定（危险否决 + 白名单放行）
# ---------------------------------------------------------------------------

_DESTRUCTIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\brm\b",
        r"\brmdir\b",
        r"\bmv\b",
        r"\bcp\b",
        r"\bmkdir\b",
        r"\btouch\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\bchgrp\b",
        r"\bln\b",
        r"\btee\b",
        r"\btruncate\b",
        r"\bdd\b",
        r"\bshred\b",
        r"(^|[^<])>(?!>)",
        r">>",
        r"\bnpm\s+(install|uninstall|update|ci|link|publish)",
        r"\byarn\s+(add|remove|install|publish)",
        r"\bpnpm\s+(add|remove|install|publish)",
        r"\bpip\s+(install|uninstall)",
        r"\bapt(-get)?\s+(install|remove|purge|update|upgrade)",
        r"\bbrew\s+(install|uninstall|upgrade)",
        r"\bgit\s+(add|commit|push|pull|merge|rebase|reset|checkout|branch\s+-[dD]|stash|cherry-pick|revert|tag|init|clone)",
        r"\bsudo\b",
        r"\bsu\b",
        r"\bkill\b",
        r"\bpkill\b",
        r"\bkillall\b",
        r"\breboot\b",
        r"\bshutdown\b",
        r"\bsystemctl\s+(start|stop|restart|enable|disable)",
        r"\bservice\s+\S+\s+(start|stop|restart)",
        r"\b(vim?|nano|emacs|code|subl)\b",
    ]
]

_SAFE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^\s*cat\b",
        r"^\s*head\b",
        r"^\s*tail\b",
        r"^\s*less\b",
        r"^\s*more\b",
        r"^\s*grep\b",
        r"^\s*find\b",
        r"^\s*ls\b",
        r"^\s*pwd\b",
        r"^\s*echo\b",
        r"^\s*printf\b",
        r"^\s*wc\b",
        r"^\s*sort\b",
        r"^\s*uniq\b",
        r"^\s*diff\b",
        r"^\s*file\b",
        r"^\s*stat\b",
        r"^\s*du\b",
        r"^\s*df\b",
        r"^\s*tree\b",
        r"^\s*which\b",
        r"^\s*whereis\b",
        r"^\s*type\b",
        r"^\s*env\b",
        r"^\s*printenv\b",
        r"^\s*uname\b",
        r"^\s*whoami\b",
        r"^\s*id\b",
        r"^\s*date\b",
        r"^\s*cal\b",
        r"^\s*uptime\b",
        r"^\s*ps\b",
        r"^\s*top\b",
        r"^\s*htop\b",
        r"^\s*free\b",
        r"^\s*git\s+(status|log|diff|show|branch|remote|config\s+--get)",
        r"^\s*git\s+ls-",
        r"^\s*npm\s+(list|ls|view|info|search|outdated|audit)",
        r"^\s*yarn\s+(list|info|why|audit)",
        r"^\s*node\s+--version",
        r"^\s*python\s+--version",
        r"^\s*curl\s",
        r"^\s*wget\s+-O\s*-",
        r"^\s*jq\b",
        r"^\s*sed\s+-n",
        r"^\s*awk\b",
        r"^\s*rg\b",
        r"^\s*fd\b",
        r"^\s*bat\b",
        r"^\s*eza\b",
    ]
]


def is_safe_command(command: str) -> bool:
    """只读命令判定：危险模式优先否决，白名单兜底放行。"""
    is_destructive = any(p.search(command) for p in _DESTRUCTIVE_PATTERNS)
    is_safe = any(p.search(command) for p in _SAFE_PATTERNS)
    return not is_destructive and is_safe


# ---------------------------------------------------------------------------
# 计划步骤解析
# ---------------------------------------------------------------------------

_STEP_VERBS = (
    "Use",
    "Run",
    "Execute",
    "Create",
    "Write",
    "Read",
    "Check",
    "Verify",
    "Update",
    "Modify",
    "Add",
    "Remove",
    "Delete",
    "Install",
)
_STEP_VERB_RE = re.compile(
    r"^(?:" + "|".join(_STEP_VERBS) + r")\s+(?:the\s+)?", re.IGNORECASE
)
_NUMBERED_RE = re.compile(r"^\s*(\d+)[.)]\s+\*{0,2}([^*\n]+)", re.MULTILINE)
_PLAN_HEADER_RE = re.compile(r"\*{0,2}Plan:\*{0,2}\s*\n", re.IGNORECASE)
_DONE_RE = re.compile(r"\[DONE:(\d+)\]", re.IGNORECASE)


def clean_step_text(text: str) -> str:
    """清洗步骤文本：去 markdown 装饰、去动作动词前缀、压缩空白、限长 50。"""
    cleaned = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = _STEP_VERB_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    if len(cleaned) > 50:
        cleaned = cleaned[:47] + "..."
    return cleaned


def extract_todo_items(message: str) -> List[Dict[str, Any]]:
    """从 "Plan:" 段提取编号步骤（无 Plan 头返回空）。"""
    items: List[Dict[str, Any]] = []
    header = _PLAN_HEADER_RE.search(message)
    if header is None:
        return items
    plan_section = message[header.end() :]
    for match in _NUMBERED_RE.finditer(plan_section):
        text = match.group(2).strip()
        text = re.sub(r"\*{1,2}$", "", text).strip()
        if len(text) > 5 and not text.startswith(("`", "/", "-")):
            cleaned = clean_step_text(text)
            if len(cleaned) > 3:
                items.append(
                    {"step": len(items) + 1, "text": cleaned, "completed": False}
                )
    return items


def extract_done_steps(message: str) -> List[int]:
    """提取全部 [DONE:n] 标记的步骤号。"""
    return [int(m.group(1)) for m in _DONE_RE.finditer(message) if m.group(1).isdigit()]


def mark_completed_steps(text: str, items: List[Dict[str, Any]]) -> int:
    """把 [DONE:n] 命中的步骤标记为完成，返回标记数。"""
    done = extract_done_steps(text)
    for step in done:
        item = next((t for t in items if t["step"] == step), None)
        if item is not None:
            item["completed"] = True
    return len(done)


# ---------------------------------------------------------------------------
# 提示词（questionnaire/brave-search 适配为本包 question 工具）
# ---------------------------------------------------------------------------

_PLAN_CONTEXT = """[PLAN MODE ACTIVE]
You are in plan mode - a read-only exploration mode for safe code analysis.

Restrictions:
- Built-in edit and write tools are disabled
- Other currently active tools remain available
- Bash is restricted to an allowlist of read-only commands

Ask clarifying questions using the question tool.

Create a detailed numbered plan under a "Plan:" header:

Plan:
1. First step description
2. Second step description
...

Do NOT attempt to make changes - just describe what you would do."""

_EXECUTION_CONTEXT_TEMPLATE = """[EXECUTING PLAN - Full tool access enabled]

Remaining steps:
{todo_list}

Execute each step in order.
After completing a step, include a [DONE:n] tag in your response."""

# 规划模式禁用的工具（从激活集移除——模型不可见，比拦截更硬）
_DISABLED_TOOLS = {"edit", "write"}


def _message_text(message: Any) -> str:
    """提取消息文本（content 为 str 或 TextContent 部件列表）。"""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts: List[str] = []
    for part in content:
        if isinstance(part, dict):
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
        elif getattr(part, "type", None) == "text":
            texts.append(getattr(part, "text", ""))
    return "\n".join(t for t in texts if t)


def _now_ms() -> int:
    return int(time.time() * 1000)


def extension(nova: NovaExtensionAPI) -> None:
    """注册 plan_mode 扩展。"""
    # 扩展实例闭包状态（reload 后由 session_start 从条目重建）
    state: Dict[str, Any] = {
        "enabled": False,
        "executing": False,
        "todos": [],  # [{step, text, completed}]
        "tools_before": None,  # Optional[List[str]]
    }

    def _persist(ctx: Any) -> None:
        ctx.append_entry(
            "plan-mode",
            {
                "enabled": state["enabled"],
                "executing": state["executing"],
                "todos": state["todos"],
                "tools_before": state["tools_before"],
            },
        )

    def _notify(ctx: Any, message: str, level: str = "info") -> None:
        if ctx.has_ui:
            notify_message(ctx.ui, message, level)

    def _update_status(ctx: Any) -> None:
        """footer 扩展状态行：
        执行态 📋 n/m；规划态 ⏸ plan；否则清除。无 UI 静默降级。"""
        if not ctx.has_ui:
            return
        if state["executing"] and state["todos"]:
            completed = sum(1 for t in state["todos"] if t["completed"])
            set_status(ctx.ui, "plan-mode", f"📋 {completed}/{len(state['todos'])}")
        elif state["enabled"]:
            set_status(ctx.ui, "plan-mode", "⏸ plan")
        else:
            set_status(ctx.ui, "plan-mode", None)

    def _enable_plan_tools(ctx: Any) -> None:
        if state["tools_before"] is None:
            state["tools_before"] = list(ctx.get_active_tools())
        ctx.set_active_tools(
            [t for t in state["tools_before"] if t not in _DISABLED_TOOLS]
        )

    def _restore_tools(ctx: Any) -> None:
        before = state["tools_before"]
        if before is not None:
            ctx.set_active_tools(before)
        state["tools_before"] = None

    def _toggle(ctx: Any) -> None:
        state["enabled"] = not state["enabled"]
        state["executing"] = False
        state["todos"] = []
        if state["enabled"]:
            _enable_plan_tools(ctx)
            _notify(ctx, "Plan mode enabled. Built-in write tools disabled.")
        else:
            _restore_tools(ctx)
            _notify(ctx, "Plan mode disabled. Full access restored.")
        _persist(ctx)
        _update_status(ctx)

    async def _cmd_plan(args: str, ctx: Any) -> None:
        _toggle(ctx)

    async def _shortcut_plan(ctx: Any) -> None:
        _toggle(ctx)

    # ---- tool_call 拦截：规划态 bash 限只读白名单 ----
    async def _on_tool_call(event: Any, ctx: Any) -> Optional[ToolCallEventResult]:
        if not state["enabled"] or event.tool_name != "bash":
            return None
        args = event.args if isinstance(event.args, dict) else {}
        command = args.get("command")
        if not isinstance(command, str) or not command:
            return None
        if not is_safe_command(command):
            return ToolCallEventResult(
                block=True,
                reason=(
                    "Plan mode: command blocked (not allowlisted). "
                    "Use /plan to disable plan mode first.\n"
                    f"Command: {command}"
                ),
            )
        return None

    # ---- context：非规划态滤除过期的规划上下文消息 ----
    async def _on_context(event: Any, ctx: Any) -> Optional[ContextEventResult]:
        if state["enabled"]:
            return None
        filtered = [
            m
            for m in event.messages
            if getattr(m, "custom_type", None) != "plan-mode-context"
            and "[PLAN MODE ACTIVE]" not in _message_text(m)
        ]
        return ContextEventResult(messages=filtered)

    # ---- before_agent_start：注入规划/执行上下文 ----
    async def _on_before_agent_start(
        event: Any, ctx: Any
    ) -> Optional[BeforeAgentStartEventResult]:
        if state["enabled"]:
            return BeforeAgentStartEventResult(
                message=CustomMessage(
                    custom_type="plan-mode-context",
                    content=_PLAN_CONTEXT,
                    display=False,
                    timestamp=_now_ms(),
                )
            )
        if state["executing"] and state["todos"]:
            remaining = [t for t in state["todos"] if not t["completed"]]
            todo_list = "\n".join(f"{t['step']}. {t['text']}" for t in remaining)
            return BeforeAgentStartEventResult(
                message=CustomMessage(
                    custom_type="plan-execution-context",
                    content=_EXECUTION_CONTEXT_TEMPLATE.format(todo_list=todo_list),
                    display=False,
                    timestamp=_now_ms(),
                )
            )
        return None

    # ---- turn_end：执行态解析 [DONE:n] ----
    async def _on_turn_end(event: Any, ctx: Any) -> None:
        if not state["executing"] or not state["todos"]:
            return None
        message = getattr(event, "message", None)
        if getattr(message, "role", "") != "assistant":
            return None
        if mark_completed_steps(_message_text(message), state["todos"]) > 0:
            _persist(ctx)
            _update_status(ctx)
        return None

    # ---- agent_end：提取计划并询问下一步；执行完毕自动收尾 ----
    async def _on_agent_end(event: Any, ctx: Any) -> None:
        # 执行态：全部完成 → 收尾
        if state["executing"] and state["todos"]:
            if all(t["completed"] for t in state["todos"]):
                completed = "\n".join(f"~~{t['text']}~~" for t in state["todos"])
                # 完成庆祝卡是展示类反馈——走持久化条目（转录可见、
                # 不进 LLM 上下文）；custom 消息只留上下文注入
                ctx.append_entry(
                    "plan-complete",
                    {"text": f"**Plan Complete!** ✓\n\n{completed}", "level": "info"},
                )
                state["executing"] = False
                state["todos"] = []
                _persist(ctx)
                _update_status(ctx)
            return None

        if not state["enabled"] or not ctx.has_ui:
            return None

        # 从最后一条 assistant 消息提取计划步骤
        last_assistant = next(
            (
                m
                for m in reversed(getattr(event, "messages", []))
                if getattr(m, "role", "") == "assistant"
            ),
            None,
        )
        if last_assistant is not None:
            extracted = extract_todo_items(_message_text(last_assistant))
            if extracted:
                state["todos"] = extracted

        if not state["todos"]:
            return None
        _persist(ctx)

        todo_text = "\n".join(
            f"{i + 1}. ☐ {t['text']}" for i, t in enumerate(state["todos"])
        )
        plan_list_message = {
            "custom_type": "plan-todo-list",
            "content": f"**Plan Steps ({len(state['todos'])}):**\n\n{todo_text}",
            "display": True,
        }

        choice = await select(
            ctx.ui,
            "Plan mode - what next?",
            [
                "Execute the plan (track progress)",
                "Stay in plan mode",
                "Refine the plan",
            ],
        )

        if isinstance(choice, str) and choice.startswith("Execute"):
            first = state["todos"][0]
            state["enabled"] = False
            state["executing"] = True
            _restore_tools(ctx)
            _persist(ctx)
            _update_status(ctx)
            remaining = "\n".join(f"{t['step']}. {t['text']}" for t in state["todos"])
            exec_message = (
                "Execute the plan.\n\nRemaining steps:\n"
                f"{remaining}\n\nStart with: {first['text']}\n"
                "After completing a step, include a [DONE:n] tag in your response."
            )
            await ctx.send_message(plan_list_message, {"deliverAs": "followUp"})
            await ctx.send_message(
                {
                    "custom_type": "plan-mode-execute",
                    "content": exec_message,
                    "display": True,
                },
                {"triggerTurn": True, "deliverAs": "followUp"},
            )
        elif choice == "Refine the plan":
            # 基线词汇无多行编辑器，用单行 input
            refinement = await ui_input(ctx.ui, "Refine the plan:")
            if isinstance(refinement, str) and refinement.strip():
                await ctx.send_message(plan_list_message, {"deliverAs": "followUp"})
                await ctx.send_user_message(
                    refinement.strip(), {"deliverAs": "followUp"}
                )
        return None

    # ---- session_start（含 reload）：旗标 + 条目重建状态 ----
    async def _on_session_start(event: Any, ctx: Any) -> None:
        if nova.getFlag("plan") is True:
            state["enabled"] = True

        sm = ctx.session_manager
        if sm is not None:
            plan_entry = next(
                (
                    e
                    for e in reversed(sm.get_entries())
                    if getattr(e, "type", "") == "custom"
                    and getattr(e, "custom_type", "") == "plan-mode"
                ),
                None,
            )
            data = getattr(plan_entry, "data", None) if plan_entry else None
            if isinstance(data, dict):
                state["enabled"] = data.get("enabled", state["enabled"])
                state["todos"] = data.get("todos", state["todos"])
                state["executing"] = data.get("executing", state["executing"])
                state["tools_before"] = data.get("tools_before", state["tools_before"])

        # 恢复后重建工具集状态（reload 后激活集已回默认）
        if state["enabled"] and state["tools_before"] is None:
            state["tools_before"] = list(ctx.get_active_tools())
        if state["enabled"]:
            ctx.set_active_tools(
                [t for t in state["tools_before"] if t not in _DISABLED_TOOLS]
            )
        _update_status(ctx)
        return None

    nova.registerCommand(
        "plan",
        {"description": "切换规划模式（只读探索，禁用写工具）", "handler": _cmd_plan},
    )
    nova.registerShortcut(
        "ctrl+alt+p", {"description": "切换规划模式", "handler": _shortcut_plan}
    )
    nova.registerFlag(
        "plan",
        {
            "description": "启动时进入规划模式（只读探索）",
            "type": "boolean",
            "default": False,
        },
    )
    nova.on("tool_call", _on_tool_call)
    nova.on("context", _on_context)
    nova.on("before_agent_start", _on_before_agent_start)
    nova.on("turn_end", _on_turn_end)
    nova.on("agent_end", _on_agent_end)
    nova.on("session_start", _on_session_start)
