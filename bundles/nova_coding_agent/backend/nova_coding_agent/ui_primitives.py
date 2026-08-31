"""UI 标准原语：词汇定义与 Python 糖库。

架构 2.0 终案（``nova-client/docs/ui-primitives.md``）：harness 的
``UIContext`` 是**泛型 transport**（零词汇），交互词汇的定义权归包。
本模块是**标准词汇的官方定义点**：

- ``STANDARD_METHODS``：基线四件套（select/confirm/input/notify——语义冻结的
  交互原子：决断/选择/文本/告知，shape 无呈现假设）+ ``form``（官方复合
  原语：多字段表单，四件套表达不了的结构化输入）；
- ``select`` / ``select_items`` / ``confirm`` / ``input`` / ``form`` /
  ``notify_message`` / ``set_status``：便捷糖函数，包装
  ``UIContext.request`` / ``notify``，提供类型化返回值。

第三方包可定义自定义原语（建议 ``ext:`` 前缀），经同一泛型通道。
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional

from nova_harness.types.ui import UIContext

# 基线四件套：交互原子（语义冻结——决断/选择/文本/告知）+ form（官方复合原语）
# + set_status（展示类词汇：footer 扩展状态行，pi ctx.ui.setStatus 对位）。
# 前端原生实现；能力协商时按名宣告。
STANDARD_METHODS: FrozenSet[str] = frozenset(
    {
        "select",
        "confirm",
        "input",
        "notify",
        "form",
        "set_status",
    }
)


async def select(
    ui: UIContext,
    title: str,
    options: List[str],
    placeholder: Optional[str] = None,
) -> Optional[str]:
    """显示选择器并返回用户选择；取消、不支持或返回非字符串时返回 None。"""
    if not ui.has_capability("select"):
        return None
    params: Dict[str, Any] = {"title": title, "options": options}
    if placeholder is not None:
        params["placeholder"] = placeholder
    resp = await ui.request("select", params)
    if resp.cancelled or not isinstance(resp.value, str):
        return None
    return resp.value


async def select_items(
    ui: UIContext,
    title: str,
    items: List[Dict[str, str]],
    placeholder: Optional[str] = None,
) -> Optional[str]:
    """结构化选择器（元信息列）：items 为 ``{value, label, description?}``。

    返回选中项的 ``value``（取消/不支持返回 None）。与 ``select`` 同通道
    （``ui.request("select")``）——前端按 items 渲染多列（搜索 + 描述）。
    """
    if not ui.has_capability("select"):
        return None
    params: Dict[str, Any] = {"title": title, "items": items}
    if placeholder is not None:
        params["placeholder"] = placeholder
    resp = await ui.request("select", params)
    if resp.cancelled or not isinstance(resp.value, str):
        return None
    return resp.value


async def confirm(ui: UIContext, title: str, message: str) -> bool:
    """显示确认对话框。取消、不支持或返回非肯定时都返回 False。"""
    if not ui.has_capability("confirm"):
        return False
    resp = await ui.request("confirm", {"title": title, "message": message})
    if resp.cancelled:
        return False
    if resp.confirmed is not None:
        return resp.confirmed
    return bool(resp.value)


async def input(
    ui: UIContext, title: str, placeholder: Optional[str] = None
) -> Optional[str]:
    """显示文本输入框；取消、不支持或返回非字符串时返回 None。"""
    if not ui.has_capability("input"):
        return None
    params: Dict[str, Any] = {"title": title}
    if placeholder is not None:
        params["placeholder"] = placeholder
    resp = await ui.request("input", params)
    if resp.cancelled or not isinstance(resp.value, str):
        return None
    return resp.value


async def form(
    ui: UIContext,
    title: str,
    fields: List[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    """显示多字段表单；提交返回 ``{key: value}``，取消/不支持返回 None。

    ``fields`` 每项：``{key, label, placeholder?}``（placeholder 语义为预填值，
    与 ``input`` 原语一致）。键位：tab/↑↓ 切换字段，enter 下一项（末项提交），
    ctrl+enter 任意位置提交，esc 取消。
    """
    if not ui.has_capability("form"):
        return None
    resp = await ui.request("form", {"title": title, "fields": fields})
    if resp.cancelled or not isinstance(resp.value, dict):
        return None
    return {str(k): v if isinstance(v, str) else str(v) for k, v in resp.value.items()}


def notify_message(ui: UIContext, message: str, type: str = "info") -> None:
    """显示一条通知消息（type: info / warning / error / progress）。"""
    ui.notify("notify", {"message": message, "type": type})


def set_status(ui: UIContext, key: str, text: Optional[str]) -> None:
    """设置 footer 扩展状态行（pi ``ctx.ui.setStatus`` 对位）。

    ``key`` 幂等覆盖（同一来源反复更新同一位）；``text`` 为 None 或空串
    时清除该位。无对应能力的前端经 transport 的 capability 检查静默降级。
    """
    ui.notify("set_status", {"key": key, "text": text or ""})


__all__ = [
    "STANDARD_METHODS",
    "confirm",
    "form",
    "input",
    "notify_message",
    "select",
    "select_items",
    "set_status",
]
