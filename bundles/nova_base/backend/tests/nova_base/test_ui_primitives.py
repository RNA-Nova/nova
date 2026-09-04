"""ui_primitives 糖库测试：form 原语 + 既有糖函数的能力门/返回值归一。"""

from types import SimpleNamespace

import pytest
from nova_base.ui_primitives import (
    STANDARD_METHODS,
    confirm,
    form,
    input,
    select,
)


class _FakeUI:
    """模拟泛型 UIContext：按脚本应答 request。"""

    def __init__(self, responses=None, capabilities=None):
        # responses: {method: UIResponse 形态 SimpleNamespace}
        self._responses = responses or {}
        self._capabilities = capabilities
        self.calls = []

    def has_capability(self, method):
        if self._capabilities is None:
            return True
        return method in self._capabilities

    async def request(self, method, params):
        self.calls.append((method, params))
        return self._responses.get(
            method, SimpleNamespace(value=None, cancelled=True, confirmed=None)
        )

    def notify(self, method, params):
        self.calls.append((method, params))


def _resp(value=None, cancelled=False, confirmed=None):
    return SimpleNamespace(value=value, cancelled=cancelled, confirmed=confirmed)


@pytest.mark.asyncio
async def test_form_returns_field_dict():
    ui = _FakeUI({"form": _resp(value={"name": "nova", "desc": "x"})})
    result = await form(ui, "建资源", [{"key": "name", "label": "名称"}])
    assert result == {"name": "nova", "desc": "x"}
    # 线上词汇形状：method=form，params 原样透传 title/fields
    method, params = ui.calls[0]
    assert method == "form"
    assert params["title"] == "建资源"
    assert params["fields"] == [{"key": "name", "label": "名称"}]


@pytest.mark.asyncio
async def test_form_cancelled_returns_none():
    ui = _FakeUI({"form": _resp(cancelled=True)})
    assert await form(ui, "t", [{"key": "a", "label": "a"}]) is None


@pytest.mark.asyncio
async def test_form_non_dict_value_returns_none():
    """前端返回异常形状（字符串）时按取消处理——糖函数不向前端崩溃。"""
    ui = _FakeUI({"form": _resp(value="oops")})
    assert await form(ui, "t", [{"key": "a", "label": "a"}]) is None


@pytest.mark.asyncio
async def test_form_unsupported_capability_returns_none_without_request():
    ui = _FakeUI(capabilities={"select", "confirm", "input", "notify"})
    assert await form(ui, "t", [{"key": "a", "label": "a"}]) is None
    assert ui.calls == []  # 能力缺失：不发请求（优雅降级）


@pytest.mark.asyncio
async def test_form_value_coerced_to_str_dict():
    ui = _FakeUI({"form": _resp(value={"n": 42})})
    assert await form(ui, "t", [{"key": "n", "label": "n"}]) == {"n": "42"}


def test_form_in_standard_methods():
    assert "form" in STANDARD_METHODS
    # 基线四件套仍在（语义冻结不回归）
    assert {"select", "confirm", "input", "notify"} <= STANDARD_METHODS


@pytest.mark.asyncio
async def test_baseline_sugars_unaffected():
    """既有糖函数回归：select/confirm/input 的行为不因新增 form 改变。"""
    ui = _FakeUI(
        {
            "select": _resp(value="a"),
            "confirm": _resp(confirmed=True),
            "input": _resp(value="text"),
        }
    )
    assert await select(ui, "t", ["a", "b"]) == "a"
    assert await confirm(ui, "t", "m") is True
    assert await input(ui, "t") == "text"


def test_set_status_in_standard_methods():
    assert "set_status" in STANDARD_METHODS


def test_set_status_frame_shape():
    """set_status 糖：命名通知帧形（key 幂等覆盖语义由前端承载）。"""
    from nova_base.ui_primitives import set_status

    ui = _FakeUI()
    set_status(ui, "plan-mode", "⏸ plan")
    assert ui.calls == [("set_status", {"key": "plan-mode", "text": "⏸ plan"})]


def test_set_status_clear_maps_empty_text():
    """清除语义：None/空串统一归为空文本（前端据此删除状态位）。"""
    from nova_base.ui_primitives import set_status

    ui = _FakeUI()
    set_status(ui, "plan-mode", None)
    set_status(ui, "plan-mode", "")
    assert ui.calls == [
        ("set_status", {"key": "plan-mode", "text": ""}),
        ("set_status", {"key": "plan-mode", "text": ""}),
    ]
