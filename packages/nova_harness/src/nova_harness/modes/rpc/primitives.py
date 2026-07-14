"""UI 原语定义。

预先定义后端可发送的 UI method 名称与 params/response schema。这些原语是
RPC 模式（JSON-RPC over stdio）下前后端交互的契约；WebSocket 模式未来可在此
复用或定义自己的 schema。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, FrozenSet, List, Literal, Optional

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

# ------------------------------------------------------------------------------
# Request / response primitives
# ------------------------------------------------------------------------------


class SelectRequest(NovaBaseModel):
    """选择器请求参数。"""

    title: str
    options: List[str]
    placeholder: Optional[str] = None


class SelectResponse(NovaBaseModel):
    """选择器响应。"""

    value: Optional[str] = None
    cancelled: bool = False


class ConfirmRequest(NovaBaseModel):
    """确认对话框请求参数。"""

    title: str
    message: str


class ConfirmResponse(NovaBaseModel):
    """确认对话框响应。"""

    confirmed: bool = False
    cancelled: bool = False


class InputRequest(NovaBaseModel):
    """文本输入框请求参数。"""

    title: str
    placeholder: Optional[str] = None


class InputResponse(NovaBaseModel):
    """文本输入框响应。"""

    value: Optional[str] = None
    cancelled: bool = False


class EditorRequest(NovaBaseModel):
    """代码编辑器请求参数。"""

    title: str
    prefill: Optional[str] = None


class EditorResponse(NovaBaseModel):
    """代码编辑器响应。"""

    value: Optional[str] = None
    cancelled: bool = False


class CustomRequest(NovaBaseModel):
    """自定义组件请求参数。

    前端根据 ``component`` 名称渲染对应组件，并把 ``props`` 传入。
    """

    component: str
    props: Dict[str, Any] = Field(default_factory=dict)


class CustomResponse(NovaBaseModel):
    """自定义组件响应。"""

    value: Any = None
    cancelled: bool = False


class SetThemeRequest(NovaBaseModel):
    """设置主题请求参数。可以是主题名称字符串或完整主题对象。"""

    theme: str


class SetThemeResponse(NovaBaseModel):
    """设置主题响应。"""

    success: bool = True
    error: Optional[str] = None


# ------------------------------------------------------------------------------
# One-way notification / setter primitives
# ------------------------------------------------------------------------------


class NotifyParams(NovaBaseModel):
    """通知消息参数。"""

    message: str
    type: Literal["info", "warning", "error"] = "info"


class SetStatusParams(NovaBaseModel):
    """设置状态栏文本参数。"""

    key: str
    text: Optional[str] = None


class SetWorkingMessageParams(NovaBaseModel):
    """设置工作/加载消息参数。"""

    message: Optional[str] = None


class SetWorkingVisibleParams(NovaBaseModel):
    """显示或隐藏工作指示器参数。"""

    visible: bool = True


class SetWorkingIndicatorParams(NovaBaseModel):
    """设置工作指示器动画参数。

    ``frames`` 为空列表时隐藏指示器；省略时恢复默认 spinner。
    """

    frames: Optional[List[str]] = None
    interval_ms: Optional[int] = None


class SetHiddenThinkingLabelParams(NovaBaseModel):
    """设置隐藏 thinking 块标签参数。"""

    label: Optional[str] = None


class SetWidgetParams(NovaBaseModel):
    """设置 Widget 参数。

    ``content`` 为字符串列表时前端按文本行渲染；为可调用对象时前端调用获取组件。
    """

    key: str
    content: Optional[Any] = None
    placement: Literal["aboveEditor", "belowEditor"] = "aboveEditor"


class SetFooterParams(NovaBaseModel):
    """设置自定义 footer 参数。"""

    factory: Optional[Any] = None


class SetHeaderParams(NovaBaseModel):
    """设置自定义 header 参数。"""

    factory: Optional[Any] = None


class SetTitleParams(NovaBaseModel):
    """设置终端窗口标题参数。"""

    title: str


class PasteToEditorParams(NovaBaseModel):
    """向当前编辑器粘贴文本参数。"""

    text: str


class SetEditorTextParams(NovaBaseModel):
    """直接设置编辑器文本参数。"""

    text: str


class SetEditorComponentParams(NovaBaseModel):
    """替换整个编辑器组件参数。"""

    factory: Optional[Any] = None


class SetToolsExpandedParams(NovaBaseModel):
    """设置工具输出展开状态参数。"""

    expanded: bool = True


class AddAutocompleteProviderParams(NovaBaseModel):
    """注册自动补全 provider 参数。"""

    factory: Optional[Any] = None


# ------------------------------------------------------------------------------
# Standard UI method registry
# ------------------------------------------------------------------------------

# 标准 UI method 名称集合，作为前后端契约文档。
# 前端可选择性实现子集，并通过 ``extension/ui/capabilities`` 通知后端。
STANDARD_UI_METHODS: FrozenSet[str] = frozenset(
    {
        # request/response
        "select",
        "confirm",
        "input",
        "editor",
        "custom",
        "setTheme",
        # one-way notifications
        "notify",
        "setStatus",
        "setWorkingMessage",
        "setWorkingVisible",
        "setWorkingIndicator",
        "setHiddenThinkingLabel",
        "setWidget",
        "setFooter",
        "setHeader",
        "setTitle",
        "pasteToEditor",
        "setEditorText",
        "setEditorComponent",
        "setToolsExpanded",
        "addAutocompleteProvider",
        # synchronous getters (mainly TUI-only)
        "getEditorText",
        "getAllThemes",
        "getTheme",
        "getToolsExpanded",
        # subscription
        "onTerminalInput",
    }
)


def is_standard_ui_method(method: str) -> bool:
    """判断是否为预定义的标准 UI method。"""
    return method in STANDARD_UI_METHODS


# 在 RPC 模式下可降级为 no-op / 默认值的方法
RPC_NOOP_UI_METHODS: FrozenSet[str] = frozenset(
    {
        "setWorkingMessage",
        "setWorkingVisible",
        "setWorkingIndicator",
        "setHiddenThinkingLabel",
        "setFooter",
        "setHeader",
        "custom",
        "onTerminalInput",
        "addAutocompleteProvider",
        "setEditorComponent",
        "pasteToEditor",
        "setTheme",
    }
)

TerminalInputHandler = Callable[[str], Optional[Dict[str, Any]]]
"""终端输入处理器类型：接收输入字符串，返回 ``{"consume": bool, "data": str}`` 或 None。"""
