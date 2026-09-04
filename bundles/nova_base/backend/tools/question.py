"""question 工具执行器（交互式——``ToolExecContext.ui`` 通道的首个消费者）。

：向用户提问并让其从选项中选择，
附加 "Type something." 自由输入路径。双路径自适应（能力协商驱动）：

- **单框路径**（``dialog:question`` 已注册时）：包侧自定义对话框——选项 +
  内联自由输入同框完成（组件在 ``frontend/tui/dialogs/question.ts``）；
- **基线两步降级**（dialog 未注册/老前端）：``select_items`` 带描述选项 →
  选中 "Type something." 后接 ``input``（零前端依赖的保底形态）。

details 契约（渲染器消费，键名 snake 随 wire 原样透传）：
``{question, options: [label...], answer, was_custom, index?}``
——用户取消 ``answer=None``（合法回执，非错误）；headless
（``has_ui=False``）返回错误回执。dialog 应答值键名为 camel
（``wasCustom``——TS 生产侧原样），本文件在边界归一为 snake details。

多问形态（Claude Code AskUserQuestion 同能力）：
可选 ``questions`` 数组（1~4 项，每项 ``{question, options}``）与单问
互斥——``questions`` 存在且合法走多问（一次弹一组相关问题），存在但
非法直接错误回执（不静默落回单问），缺席则单问路径完全不变。多问
details：``{questions: [{question, options: [label...], answer,
was_custom, index?}...]}``；中途任一取消 → 整体取消回执（每问
answer=None）。
"""

from typing import Any, Callable, Dict, List, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_base.ui_primitives import input as ui_input
from nova_base.ui_primitives import select_items

from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)
from nova_harness.core.types.ui import UIContext

# "Type something." 自由输入项的 value 哨兵（label 一致）。
_OTHER_VALUE = "__type_something__"
_OTHER_LABEL = "Type something."


class Tool:
    """question 工具执行器。"""

    name = "question"
    label = "Question"
    description = (
        "Ask the user one or more questions (1-4 at once) and let them pick "
        "from options. Use when you need user input to proceed — "
        "clarification, choosing an approach, or confirming a decision. "
        "Always include a small set of concrete options; the user can also "
        "pick 'Type something.' to answer freely."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "The question to ask the user (single-question form; must "
                    "be paired with `options`, mutually exclusive with `questions`)."
                ),
            },
            "options": {
                "type": "array",
                "description": "Options for the user to choose from.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Display label for the option.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional description shown below the label.",
                        },
                    },
                    "required": ["label"],
                },
            },
            "questions": {
                "type": "array",
                "description": (
                    "Ask multiple related questions at once (1-4 items, each "
                    "with its own question and options). Mutually exclusive "
                    "with the single-question form above."
                ),
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question to ask the user.",
                        },
                        "options": {
                            "type": "array",
                            "description": "Options for the user to choose from.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "description": "Display label for the option.",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "Optional description shown below the label.",
                                    },
                                },
                                "required": ["label"],
                            },
                        },
                    },
                    "required": ["question", "options"],
                },
            },
        },
    }
    # 注意：单问（question+options）与多问（questions）互斥由工具内校验兜底，
    # 不在 schema 根部使用 anyOf——Moonshot/Kimi 的 json schema 校验拒绝
    # 根部 type 与 anyOf 共存（400 invalid_request_error）。
    prompt_snippet = (
        "Use the `question` tool when you need the user to decide before "
        "proceeding: offer 2-4 concrete options; the user can also answer "
        "freely via 'Type something.'. You can ask 1-4 related questions at "
        "once via `questions`. Do not use it for information you can find "
        "yourself with read/grep/bash."
    )
    # 询问型工具必须串行——并行弹窗无意义且与其他写操作互斥。
    execution_mode = "sequential"

    def __init__(self, context: ToolContext) -> None:
        self._context = context

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update: Optional[Callable[[AgentToolResult], None]] = None,
        ctx: ToolExecContext = NULL_TOOL_EXEC_CONTEXT,
    ) -> AgentToolResult:
        """执行 question 工具调用（单问/多问双形态分派）。"""
        raw_questions = params.get("questions")
        if raw_questions is not None:
            # 多问形态：存在且合法 → 多问；存在但非法 → 错误回执
            # （模型明确想多问，静默落回单问会掩盖参数错误）
            questions = _validate_questions(raw_questions)
            if questions is None:
                return _multi_error_result(
                    "Error: Invalid questions (expect 1-4 items, each with "
                    "a non-empty question and options)"
                )
            return await self._execute_multi(questions, ctx)
        return await self._execute_single(params, ctx)

    async def _execute_single(
        self, params: Dict[str, Any], ctx: ToolExecContext
    ) -> AgentToolResult:
        """单问执行体（dialog 单框 / 基线两步降级，契约与原实现一致）。"""
        question = params.get("question")
        raw_options = params.get("options")

        options = _validate_options(raw_options)
        if not isinstance(question, str) or not question.strip():
            return _error_result(
                question or "", "Missing or empty required parameter: question"
            )
        if options is None:
            return _error_result(question, "Error: No options provided")
        question = question.strip()
        labels = [o["label"] for o in options]

        def _details(
            answer: Optional[str], was_custom: bool, index: Optional[int] = None
        ) -> Dict[str, Any]:
            d: Dict[str, Any] = {
                "question": question,
                "options": labels,
                "answer": answer,
                "was_custom": was_custom,
            }
            if index is not None:
                d["index"] = index
            return d

        if not ctx.has_ui:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="Error: UI not available (running in non-interactive mode)",
                    )
                ],
                details=_details(None, False),
                is_error=True,
            )

        # 单框路径（dialog:question 已注册时）：包侧自定义对话框一问一答——
        # 选项 + 内联自由输入同框完成（pi question.ts 的完整形态）
        if ctx.ui.has_capability("dialog:question"):
            resp = await ctx.ui.request(
                "dialog:question", {"question": question, "options": options}
            )
            if resp.cancelled or not isinstance(resp.value, dict):
                return AgentToolResult(
                    content=[
                        TextContent(type="text", text="User cancelled the selection")
                    ],
                    details=_details(None, False),
                )
            value = resp.value
            answer = value.get("answer")
            if not isinstance(answer, str) or not answer:
                return AgentToolResult(
                    content=[
                        TextContent(type="text", text="User cancelled the selection")
                    ],
                    details=_details(None, False),
                )
            was_custom = value.get("wasCustom") is True  # 值键 camel（TS 生产侧）
            if was_custom:
                return AgentToolResult(
                    content=[TextContent(type="text", text=f"User wrote: {answer}")],
                    details=_details(answer, True),
                )
            raw_index = value.get("index")
            index = (
                raw_index
                if isinstance(raw_index, int)
                else (labels.index(answer) + 1 if answer in labels else None)
            )
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"User selected: {index}. {answer}")
                ],
                details=_details(answer, False, index),
            )

        # 基线两步降级（dialog 未注册/老前端）：select_items → 自由项接 input
        picked = await _ask_two_step(ctx.ui, question, options)
        if picked is None:
            return AgentToolResult(
                content=[TextContent(type="text", text="User cancelled the selection")],
                details=_details(None, False),
            )
        if picked["was_custom"]:
            return AgentToolResult(
                content=[
                    TextContent(type="text", text=f"User wrote: {picked['answer']}")
                ],
                details=_details(picked["answer"], True),
            )
        return AgentToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"User selected: {picked.get('index')}. {picked['answer']}",
                )
            ],
            details=_details(picked["answer"], False, picked.get("index")),
        )

    async def _execute_multi(
        self, questions: List[Dict[str, Any]], ctx: ToolExecContext
    ) -> AgentToolResult:
        """多问执行体：dialog 单框 / 逐问两步降级，任一取消 → 整体取消。"""
        if not ctx.has_ui:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="Error: UI not available (running in non-interactive mode)",
                    )
                ],
                details=_multi_details(questions),
                is_error=True,
            )

        # 单框路径（dialog:question 已注册时）：一次弹一组问题
        if ctx.ui.has_capability("dialog:question"):
            resp = await ctx.ui.request(
                "dialog:question",
                {
                    "questions": [
                        {"question": q["question"], "options": q["options"]}
                        for q in questions
                    ]
                },
            )
            value = resp.value if isinstance(resp.value, dict) else None
            answers = value.get("answers") if value is not None else None
            if resp.cancelled or not isinstance(answers, list):
                return _multi_cancelled(questions)
            per_q: List[Dict[str, Any]] = []
            for i, q in enumerate(questions):
                item = answers[i] if i < len(answers) else None
                answer = item.get("answer") if isinstance(item, dict) else None
                if not isinstance(answer, str) or not answer:
                    return _multi_cancelled(questions)
                was_custom = item.get("wasCustom") is True  # 值键 camel（TS 生产侧）
                entry: Dict[str, Any] = {
                    **q,
                    "answer": answer,
                    "was_custom": was_custom,
                }
                if not was_custom:
                    labels = [o["label"] for o in q["options"]]
                    raw_index = item.get("index")
                    index = (
                        raw_index
                        if isinstance(raw_index, int)
                        else (labels.index(answer) + 1 if answer in labels else None)
                    )
                    if index is not None:
                        entry["index"] = index
                per_q.append(entry)
            return AgentToolResult(
                content=[TextContent(type="text", text=_multi_content(per_q))],
                details=_multi_details(per_q),
            )

        # 基线两步降级：逐问串行（复用单问两步逻辑），中途取消 → 整体取消
        per_q = []
        for q in questions:
            picked = await _ask_two_step(ctx.ui, q["question"], q["options"])
            if picked is None:
                return _multi_cancelled(questions)
            per_q.append({**q, **picked})
        return AgentToolResult(
            content=[TextContent(type="text", text=_multi_content(per_q))],
            details=_multi_details(per_q),
        )


def _validate_options(raw: Any) -> Optional[List[Dict[str, str]]]:
    """校验并归一化选项列表；不合法返回 None。"""
    if not isinstance(raw, list) or not raw:
        return None
    options: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            return None
        entry: Dict[str, str] = {"label": label.strip()}
        description = item.get("description")
        if isinstance(description, str) and description.strip():
            entry["description"] = description.strip()
        options.append(entry)
    return options


def _validate_questions(raw: Any) -> Optional[List[Dict[str, Any]]]:
    """校验并归一化多问列表（1~4 项，每项 question + options）；不合法返回 None。"""
    if not isinstance(raw, list) or not raw or len(raw) > 4:
        return None
    questions: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        question = item.get("question")
        if not isinstance(question, str) or not question.strip():
            return None
        options = _validate_options(item.get("options"))
        if options is None:
            return None
        questions.append({"question": question.strip(), "options": options})
    return questions


async def _ask_two_step(
    ui: UIContext, question: str, options: List[Dict[str, str]]
) -> Optional[Dict[str, Any]]:
    """基线两步询问：select_items → 自由项接 input；取消返回 None。

    命中返回 ``{"answer", "was_custom", "index"?}``（index 仅选项路径，1 基）。
    单问与多问降级路径共用。
    """
    items = [
        {
            "value": o["label"],
            "label": o["label"],
            "description": o.get("description", ""),
        }
        for o in options
    ]
    items.append(
        {"value": _OTHER_VALUE, "label": _OTHER_LABEL, "description": "自由输入"}
    )

    chosen = await select_items(ui, question, items)
    if chosen is None:
        return None

    if chosen == _OTHER_VALUE:
        # 自由输入路径（—第二步 input 原语）
        text = await ui_input(ui, question, placeholder="Your answer")
        if text is None or not text.strip():
            return None
        return {"answer": text.strip(), "was_custom": True}

    labels = [o["label"] for o in options]
    index = labels.index(chosen) + 1 if chosen in labels else None
    return {"answer": chosen, "was_custom": False, "index": index}


def _multi_details(per_q: List[Dict[str, Any]]) -> Dict[str, Any]:
    """多问 details（snake）：每问 {question, options: [label...], answer, was_custom, index?}。"""
    items: List[Dict[str, Any]] = []
    for q in per_q:
        d: Dict[str, Any] = {
            "question": q["question"],
            "options": [o["label"] for o in q["options"]],
            "answer": q.get("answer"),
            "was_custom": q.get("was_custom", False),
        }
        if q.get("index") is not None:
            d["index"] = q["index"]
        items.append(d)
    return {"questions": items}


def _multi_content(per_q: List[Dict[str, Any]]) -> str:
    """多问 content：编号行 ``1. <question> → <answer>``（自由输入 ``→ (wrote)``）。"""
    lines: List[str] = []
    for i, q in enumerate(per_q, 1):
        if q.get("was_custom"):
            lines.append(f"{i}. {q['question']} → (wrote) {q['answer']}")
        else:
            lines.append(f"{i}. {q['question']} → {q['answer']}")
    return "\n".join(lines)


def _multi_cancelled(questions: List[Dict[str, Any]]) -> AgentToolResult:
    """多问整体取消回执（合法非错误——每问 answer=None）。"""
    return AgentToolResult(
        content=[TextContent(type="text", text="User cancelled the selection")],
        details=_multi_details(questions),
    )


def _multi_error_result(message: str) -> AgentToolResult:
    """多问参数失败的统一回执。"""
    return AgentToolResult(
        content=[TextContent(type="text", text=message)],
        details={"questions": [], "error": message},
        is_error=True,
    )


def _error_result(question: str, message: str) -> AgentToolResult:
    """参数/环境失败的统一回执（details 带 answer=None 供渲染器标错）。"""
    return AgentToolResult(
        content=[TextContent(type="text", text=message)],
        details={"question": question, "options": [], "answer": None, "error": message},
        is_error=True,
    )
