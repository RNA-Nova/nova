"""UI 能力抽象上下文接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional, Set

from nova_harness.core.types.ui.primitives import UIResponse


class UIContext(ABC):
    """前端 UI 能力抽象接口。

    实现者（TUI/Web UI/NoOp）只需实现 ``capabilities``、``request``、``notify``
    三个抽象方法。高层 convenience methods 基于它们实现，且只使用普通 dict 参数，
    不引入任何具体传输协议的原语类型。
    """

    @property
    @abstractmethod
    def capabilities(self) -> Set[str]:
        """返回前端支持的 UI method 集合。"""

    def has_capability(self, method: str) -> bool:
        """检查前端是否支持指定 UI method。"""
        return method in self.capabilities

    @abstractmethod
    async def request(self, method: str, params: Dict[str, Any]) -> UIResponse:
        """发送一个需要响应的 UI request。"""

    @abstractmethod
    def notify(self, method: str, params: Dict[str, Any]) -> None:
        """发送一个不需要响应的 UI 通知。"""

    # ------------------------------------------------------------------
    # request/response convenience methods
    # ------------------------------------------------------------------

    async def select(
        self, title: str, options: List[str], opts: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """显示选择器并返回用户选择；取消、不支持或返回非字符串时返回 None。"""
        if not self.has_capability("select"):
            return None
        params: Dict[str, Any] = {
            "title": title,
            "options": options,
        }
        if opts and "placeholder" in opts:
            params["placeholder"] = opts["placeholder"]
        resp = await self.request("select", params)
        if resp.cancelled or not isinstance(resp.value, str):
            return None
        return resp.value

    async def confirm(
        self, title: str, message: str, opts: Optional[Dict[str, Any]] = None
    ) -> bool:
        """显示确认对话框。取消、不支持或返回非 true 时都返回 False。"""
        if not self.has_capability("confirm"):
            return False
        resp = await self.request("confirm", {"title": title, "message": message})
        if resp.cancelled:
            return False
        if resp.confirmed is not None:
            return resp.confirmed
        return bool(resp.value)

    async def input(
        self,
        title: str,
        placeholder: Optional[str] = None,
        opts: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """显示文本输入框。"""
        if not self.has_capability("input"):
            return None
        params: Dict[str, Any] = {"title": title}
        if placeholder is not None:
            params["placeholder"] = placeholder
        resp = await self.request("input", params)
        if resp.cancelled or not isinstance(resp.value, str):
            return None
        return resp.value

    async def editor(
        self,
        title: str,
        prefill: Optional[str] = None,
    ) -> Optional[str]:
        """打开代码编辑器并返回保存后的内容；取消或前端不支持时返回 None。"""
        if not self.has_capability("editor"):
            return None
        params: Dict[str, Any] = {"title": title}
        if prefill is not None:
            params["prefill"] = prefill
        resp = await self.request("editor", params)
        if resp.cancelled or not isinstance(resp.value, str):
            return None
        return resp.value

    async def custom(
        self, component: str, props: Optional[Dict[str, Any]] = None
    ) -> Any:
        """渲染自定义组件并返回结果；取消或前端不支持时返回 None。"""
        if not self.has_capability("custom"):
            return None
        resp = await self.request(
            "custom", {"component": component, "props": props or {}}
        )
        return None if resp.cancelled else resp.value

    async def set_theme(self, theme: str) -> Dict[str, Any]:
        """设置主题。前端不支持时返回失败。"""
        if not self.has_capability("setTheme"):
            return {"success": False, "error": "setTheme not supported"}
        resp = await self.request("setTheme", {"theme": theme})
        if isinstance(resp.value, dict):
            return resp.value
        return {"success": bool(resp.value), "error": None}

    # ------------------------------------------------------------------
    # notification convenience methods
    # ------------------------------------------------------------------

    def notify_message(self, message: str, type: str = "info") -> None:
        """显示一条通知。"""
        self.notify("notify", {"message": message, "type": type})

    def set_status(self, key: str, text: Optional[str] = None) -> None:
        """设置底部/状态栏状态文本。"""
        self.notify("setStatus", {"key": key, "text": text})

    def set_working_message(self, message: Optional[str] = None) -> None:
        """设置流式过程中的工作/加载消息。"""
        self.notify("setWorkingMessage", {"message": message})

    def set_working_visible(self, visible: bool) -> None:
        """显示或隐藏工作指示器。"""
        self.notify("setWorkingVisible", {"visible": visible})

    def set_working_indicator(
        self,
        frames: Optional[List[str]] = None,
        interval_ms: Optional[int] = None,
    ) -> None:
        """设置工作指示器动画。"""
        self.notify(
            "setWorkingIndicator",
            {"frames": frames, "interval_ms": interval_ms},
        )

    def set_hidden_thinking_label(self, label: Optional[str] = None) -> None:
        """设置隐藏 thinking 块标签。"""
        self.notify("setHiddenThinkingLabel", {"label": label})

    def set_widget(
        self,
        key: str,
        content: Optional[Any] = None,
        placement: Literal["aboveEditor", "belowEditor"] = "aboveEditor",
    ) -> None:
        """设置 Widget 内容。"""
        self.notify(
            "setWidget",
            {"key": key, "content": content, "placement": placement},
        )

    def set_footer(self, factory: Optional[Any] = None) -> None:
        """设置自定义 footer。"""
        self.notify("setFooter", {"factory": factory})

    def set_header(self, factory: Optional[Any] = None) -> None:
        """设置自定义 header。"""
        self.notify("setHeader", {"factory": factory})

    def set_title(self, title: str) -> None:
        """设置终端窗口标题。"""
        self.notify("setTitle", {"title": title})

    def paste_to_editor(self, text: str) -> None:
        """向当前编辑器粘贴文本。"""
        self.notify("pasteToEditor", {"text": text})

    def set_editor_text(self, text: str) -> None:
        """直接设置编辑器文本。"""
        self.notify("setEditorText", {"text": text})

    def set_editor_component(self, factory: Optional[Any] = None) -> None:
        """替换整个编辑器组件。"""
        self.notify("setEditorComponent", {"factory": factory})

    def set_tools_expanded(self, expanded: bool) -> None:
        """设置工具输出展开状态。"""
        self.notify("setToolsExpanded", {"expanded": expanded})

    def add_autocomplete_provider(self, factory: Optional[Any] = None) -> None:
        """注册自动补全 provider。"""
        self.notify("addAutocompleteProvider", {"factory": factory})

    # ------------------------------------------------------------------
    # synchronous getters (mainly TUI-only; default implementations return empty)
    # ------------------------------------------------------------------

    def get_editor_text(self) -> str:
        """获取编辑器当前文本。RPC/无 UI 时返回空字符串。"""
        return ""

    def get_all_themes(self) -> List[Dict[str, Any]]:
        """获取所有可用主题。RPC/无 UI 时返回空列表。"""
        return []

    def get_theme(self, name: str) -> Optional[Dict[str, Any]]:
        """按名称获取主题。RPC/无 UI 时返回 None。"""
        return None

    def get_tools_expanded(self) -> bool:
        """获取工具输出是否展开。RPC/无 UI 时返回 False。"""
        return False

    # ------------------------------------------------------------------
    # subscription primitives
    # ------------------------------------------------------------------

    def on_terminal_input(self, handler: Any) -> Any:
        """注册终端输入监听器。默认返回无操作取消函数。"""
        return lambda: None


__all__ = ["UIContext"]
