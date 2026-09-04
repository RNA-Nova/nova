"""question 工具测试（交互式——ToolExecContext.ui 通道消费者）。

覆盖：参数校验、headless 降级（has_ui=False 错误回执）、取消（合法非错误）、
选项选择（带 index）、自由输入（"Type something." → input 两步）、
自由输入二次取消。
"""

import asyncio
import importlib.util
import os
from typing import Any, Dict, List, Optional, Set

from nova_harness.core.types.ui.primitives import UIResponse


def _load_question_tool():
    """加载 tools/question.py 并构造 Tool 实例。"""
    tool_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "tools", "question.py"
    )
    spec = importlib.util.spec_from_file_location("_test_tool_question", tool_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from nova_harness.core.types.resources.tools import (
        NULL_TOOL_SETTINGS,
        ToolContext,
    )

    context = ToolContext(cwd=os.getcwd(), settings=NULL_TOOL_SETTINGS)
    return module, module.Tool(context)


class _ScriptedUI:
    """剧本式 UI：按队列应答 request（糖库只要求 has_capability/request/notify）。"""

    def __init__(self, responses: List[UIResponse]):
        self._responses = list(responses)
        self.calls: List[tuple[str, Dict[str, Any]]] = []

    @property
    def capabilities(self) -> Set[str]:
        return {"select", "input"}

    def has_capability(self, method: str) -> bool:
        return method in self.capabilities

    async def request(
        self, method: str, params: Dict[str, Any], signal: Any = None
    ) -> UIResponse:
        self.calls.append((method, params))
        return self._responses.pop(0)

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        pass


def _exec_ctx(ui: Optional[_ScriptedUI], has_ui: bool):
    from nova_harness.core.types.resources.tools import ToolExecContext
    from nova_harness.core.types.ui import NoOpUIContext

    return ToolExecContext(ui=ui or NoOpUIContext(), has_ui=has_ui)


_PARAMS = {
    "question": "选哪种方案？",
    "options": [
        {"label": "方案 A", "description": "快但糙"},
        {"label": "方案 B"},
    ],
}


def test_headless_returns_error_receipt():
    """has_ui=False：错误回执（非弹窗），details.answer=None。"""
    _, tool = _load_question_tool()
    result = asyncio.run(tool.execute("id", _PARAMS, ctx=_exec_ctx(None, False)))
    assert result.is_error is True
    assert "non-interactive" in result.content[0].text
    assert result.details["answer"] is None


def test_empty_options_is_error():
    """空选项列表：参数错误回执。"""
    _, tool = _load_question_tool()
    result = asyncio.run(
        tool.execute("id", {"question": "q", "options": []}, ctx=_exec_ctx(None, False))
    )
    assert result.is_error is True
    assert "No options" in result.content[0].text


def test_select_option_returns_index():
    """选择第二个选项：answer + index=2，非自由输入。"""
    _, tool = _load_question_tool()
    ui = _ScriptedUI([UIResponse(value="方案 B")])
    result = asyncio.run(tool.execute("id", _PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert result.details["answer"] == "方案 B"
    assert result.details["index"] == 2
    assert result.details["was_custom"] is False
    assert "User selected: 2. 方案 B" in result.content[0].text
    # 只调用了 select（未走 input），且选项里附加了自由输入项
    assert [c[0] for c in ui.calls] == ["select"]
    items = ui.calls[0][1]["items"]
    assert items[-1]["label"] == "Type something."


def test_custom_answer_via_input():
    """选 "Type something." → 第二步 input 拿自由文本。"""
    _, tool = _load_question_tool()
    ui = _ScriptedUI(
        [UIResponse(value="__type_something__"), UIResponse(value="  自定义答案  ")]
    )
    result = asyncio.run(tool.execute("id", _PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert result.details["answer"] == "自定义答案"  # 空白规整
    assert result.details["was_custom"] is True
    assert "User wrote: 自定义答案" in result.content[0].text
    assert [c[0] for c in ui.calls] == ["select", "input"]


def test_cancel_select_is_valid_receipt():
    """第一步取消：合法回执（非错误），answer=None。"""
    _, tool = _load_question_tool()
    ui = _ScriptedUI([UIResponse(cancelled=True)])
    result = asyncio.run(tool.execute("id", _PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert result.details["answer"] is None
    assert "cancelled" in result.content[0].text


def test_cancel_input_after_other():
    """选了自由输入又取消第二步：同样按取消回执。"""
    _, tool = _load_question_tool()
    ui = _ScriptedUI(
        [UIResponse(value="__type_something__"), UIResponse(cancelled=True)]
    )
    result = asyncio.run(tool.execute("id", _PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert result.details["answer"] is None
    assert "cancelled" in result.content[0].text


def test_sequential_execution_mode():
    """询问型工具声明串行。"""
    _, tool = _load_question_tool()
    assert tool.execution_mode == "sequential"


class _DialogUI(_ScriptedUI):
    """带 dialog:question 能力面的剧本 UI。"""

    @property
    def capabilities(self):
        return {"dialog:question"}


def test_dialog_path_select_option():
    """dialog:question 注册时：单框选择（camel 值键 wasCustom/index）。"""
    _, tool = _load_question_tool()
    ui = _DialogUI(
        [UIResponse(value={"answer": "方案 B", "wasCustom": False, "index": 2})]
    )
    result = asyncio.run(tool.execute("id", _PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert result.details["answer"] == "方案 B"
    assert result.details["index"] == 2
    assert result.details["was_custom"] is False
    assert [c[0] for c in ui.calls] == ["dialog:question"]


def test_dialog_path_custom_answer():
    """单框自由输入：wasCustom=true。"""
    _, tool = _load_question_tool()
    ui = _DialogUI([UIResponse(value={"answer": "自定义", "wasCustom": True})])
    result = asyncio.run(tool.execute("id", _PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.details["was_custom"] is True
    assert "User wrote: 自定义" in result.content[0].text


def test_dialog_path_cancelled():
    """单框取消（cancelled 或空 answer）：合法取消回执。"""
    _, tool = _load_question_tool()
    ui = _DialogUI([UIResponse(cancelled=True)])
    result = asyncio.run(tool.execute("id", _PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert result.details["answer"] is None
    assert "cancelled" in result.content[0].text


def test_dialog_absent_falls_back_to_two_step():
    """dialog 能力缺席时走基线两步降级（select + input）。"""
    _, tool = _load_question_tool()
    ui = _ScriptedUI([UIResponse(value="方案 A")])  # 仅 select/input 能力
    result = asyncio.run(tool.execute("id", _PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.details["answer"] == "方案 A"
    assert [c[0] for c in ui.calls] == ["select"]


# ---------------------------------------------------------------------------
# 多问形态（questions 数组）
# ---------------------------------------------------------------------------

_MULTI_PARAMS = {
    "questions": [
        {"question": "选语言？", "options": [{"label": "Python"}, {"label": "Rust"}]},
        {"question": "写测试？", "options": [{"label": "要"}, {"label": "不要"}]},
    ]
}


def test_multi_dialog_path_answers_normalized():
    """多问 dialog 路径：answers 逐问归一（camel 值键 → snake details）。"""
    _, tool = _load_question_tool()
    ui = _DialogUI(
        [
            UIResponse(
                value={
                    "answers": [
                        {"answer": "Rust", "wasCustom": False, "index": 2},
                        {"answer": "自定义说明", "wasCustom": True},
                    ]
                }
            )
        ]
    )
    result = asyncio.run(tool.execute("id", _MULTI_PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert [c[0] for c in ui.calls] == ["dialog:question"]
    # request 参数为 questions 数组（含原始 options）
    sent = ui.calls[0][1]["questions"]
    assert sent[0]["question"] == "选语言？"
    assert sent[1]["options"][0]["label"] == "要"
    qs = result.details["questions"]
    assert qs[0]["question"] == "选语言？"
    assert qs[0]["options"] == ["Python", "Rust"]
    assert qs[0]["answer"] == "Rust"
    assert qs[0]["index"] == 2
    assert qs[0]["was_custom"] is False
    assert qs[1]["answer"] == "自定义说明"
    assert qs[1]["was_custom"] is True
    assert "index" not in qs[1]
    text = result.content[0].text
    assert "1. 选语言？ → Rust" in text
    assert "2. 写测试？ → (wrote) 自定义说明" in text


def test_multi_dialog_cancelled():
    """多问 dialog 整体取消（cancelled）：合法回执，每问 answer=None。"""
    _, tool = _load_question_tool()
    ui = _DialogUI([UIResponse(cancelled=True)])
    result = asyncio.run(tool.execute("id", _MULTI_PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert "cancelled" in result.content[0].text
    qs = result.details["questions"]
    assert len(qs) == 2
    assert all(q["answer"] is None for q in qs)


def test_multi_dialog_answers_not_list():
    """多问 dialog 应答 answers 非 list：同样按整体取消回执。"""
    _, tool = _load_question_tool()
    ui = _DialogUI([UIResponse(value={"answers": "nope"})])
    result = asyncio.run(tool.execute("id", _MULTI_PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert "cancelled" in result.content[0].text
    assert all(q["answer"] is None for q in result.details["questions"])


def test_multi_fallback_sequential():
    """多问降级路径：逐问串行走两步（select → select），带 index。"""
    _, tool = _load_question_tool()
    ui = _ScriptedUI([UIResponse(value="Python"), UIResponse(value="不要")])
    result = asyncio.run(tool.execute("id", _MULTI_PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert [c[0] for c in ui.calls] == ["select", "select"]
    qs = result.details["questions"]
    assert qs[0]["answer"] == "Python"
    assert qs[0]["index"] == 1
    assert qs[1]["answer"] == "不要"
    assert qs[1]["index"] == 2
    assert "1. 选语言？ → Python" in result.content[0].text


def test_multi_fallback_cancel_midway():
    """多问降级路径中途取消（第二问取消）：整体取消回执。"""
    _, tool = _load_question_tool()
    ui = _ScriptedUI([UIResponse(value="Python"), UIResponse(cancelled=True)])
    result = asyncio.run(tool.execute("id", _MULTI_PARAMS, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert "cancelled" in result.content[0].text
    assert all(q["answer"] is None for q in result.details["questions"])


def test_multi_headless_returns_error_receipt():
    """多问 has_ui=False：错误回执（details 为 questions 形状）。"""
    _, tool = _load_question_tool()
    result = asyncio.run(tool.execute("id", _MULTI_PARAMS, ctx=_exec_ctx(None, False)))
    assert result.is_error is True
    assert "non-interactive" in result.content[0].text
    assert all(q["answer"] is None for q in result.details["questions"])


def test_multi_questions_over_four_is_error():
    """questions 超 4 项：参数错误回执（不静默落回单问）。"""
    _, tool = _load_question_tool()
    params = {
        "questions": [
            {"question": f"q{i}", "options": [{"label": "a"}]} for i in range(5)
        ]
    }
    result = asyncio.run(tool.execute("id", params, ctx=_exec_ctx(None, False)))
    assert result.is_error is True
    assert "Invalid questions" in result.content[0].text


def test_multi_questions_empty_list_is_error():
    """questions 空数组：参数错误回执。"""
    _, tool = _load_question_tool()
    result = asyncio.run(
        tool.execute("id", {"questions": []}, ctx=_exec_ctx(None, False))
    )
    assert result.is_error is True
    assert "Invalid questions" in result.content[0].text


def test_multi_questions_invalid_item_is_error():
    """questions 项缺 options（空项）：参数错误回执。"""
    _, tool = _load_question_tool()
    params = {"questions": [{"question": "q", "options": []}]}
    result = asyncio.run(tool.execute("id", params, ctx=_exec_ctx(None, False)))
    assert result.is_error is True
    assert "Invalid questions" in result.content[0].text


def test_multi_takes_precedence_over_single_params():
    """questions 合法且单问参数并存：多问优先（details 为 questions 形状）。"""
    _, tool = _load_question_tool()
    params = {
        **_PARAMS,
        "questions": [{"question": "选一个？", "options": [{"label": "A"}]}],
    }
    ui = _DialogUI(
        [
            UIResponse(
                value={"answers": [{"answer": "A", "wasCustom": False, "index": 1}]}
            )
        ]
    )
    result = asyncio.run(tool.execute("id", params, ctx=_exec_ctx(ui, True)))
    assert result.is_error is False
    assert "questions" in result.details
    assert result.details["questions"][0]["answer"] == "A"
    # dialog 请求载荷为多问形态
    assert "questions" in ui.calls[0][1]


def test_schema_no_root_anyof_for_moonshot_flavor():
    """回归：parameters 根部不得 anyOf 与 type 共存。

    Moonshot/Kimi 服务端校验拒绝该形状（400 invalid_request_error：
    "when using anyOf, type should be defined in anyOf items instead of the
    parent schema"）。单问/多问互斥由工具内校验兜底，不经 schema 表达。
    """
    tool_module, _tool = _load_question_tool()
    assert "anyOf" not in tool_module.Tool.parameters
    assert "allOf" not in tool_module.Tool.parameters
    assert "oneOf" not in tool_module.Tool.parameters
